"""Crow Concept 규칙 파라미터.

각 필드 주석의 `[출처]` 는 t.me/crowconcept 채널에서 반복적으로 언급된
원문 규칙을 가리킨다. 자세한 대응표는 docs/RULEBOOK.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace


@dataclass(frozen=True)
class SessionWindow:
    """UTC 기준 세션 구간 (시작 포함, 끝 제외)."""
    name: str
    start_hour: float
    end_hour: float


# 채널이 실제로 매매하는 시간대: 유럽 + 미국 세션
LONDON = SessionWindow("London", 7.0, 12.0)
NEWYORK = SessionWindow("NewYork", 12.0, 17.0)
ASIA = SessionWindow("Asia", 0.0, 7.0)


@dataclass(frozen=True)
class CrowConfig:
    # ------------------------------------------------------------------
    # 1. 타임프레임 계층 (탑다운)
    #    [출처] "H4/D1 = 중장기, H1 = 확인, M15 = 당일, M1/M5 = 당일 세션 한정"
    # ------------------------------------------------------------------
    htf: str = "H4"          # 방향(편향) 결정
    mtf: str = "M15"         # POI(주문블록/FVG/유동성) 선정
    ltf: str = "M5"          # 진입 트리거(CHOCH)

    # ------------------------------------------------------------------
    # 2. 구조 탐지
    # ------------------------------------------------------------------
    swing_left: int = 2               # 프랙탈 좌측 봉 수
    swing_right: int = 2              # 프랙탈 우측 봉 수 (확정 지연 = 룩어헤드 방지)
    equal_level_tol_atr: float = 0.15 # 동일고점/동일저점 허용 오차 (ATR 배수)
    structure_break_on_close: bool = True  # 종가 돌파만 BOS/CHOCH 로 인정
    #    [출처] "M5 캔들이 1953 아래 종가 마감해야 확인"

    # ------------------------------------------------------------------
    # 3. Wyckoff
    # ------------------------------------------------------------------
    wyckoff_lookback: int = 120       # 레인지 탐색 구간
    wyckoff_min_touches: int = 2      # 레인지 상·하단 최소 터치 수
    range_max_width_atr: float = 12.0 # 이보다 넓으면 레인지로 보지 않음
    spring_max_penetration_atr: float = 1.2  # 스프링/업스러스트 최대 침투 폭

    # ------------------------------------------------------------------
    # 4. 진입 모델
    # ------------------------------------------------------------------
    require_liquidity_sweep: bool = True  # [출처] "유동성 죽이고 나서 간다"
    require_choch: bool = True            # [출처] "CHOCH 확인 후 진입"
    sweep_lookback: int = 60
    poi_types: tuple[str, ...] = ("order_block", "fvg")
    entry_style: str = "limit"            # [출처] "자는 동안 지정가 걸어둔다"
    market_if_at_poi: bool = True         # [출처] 즉시 진입 스캘프도 병행 ("지금 매수, SL/TP ~")
    poi_touch_atr: float = 0.25           # POI 로 간주하는 근접 허용 (ATR 배수)
    entry_at: str = "proximal"            # 존의 어느 지점에 지정가를 거는가
                                          #   proximal = 가까운 쪽 경계(빨리 체결)
                                          #   mid      = 존 중앙(평균 단가)
                                          #   distal   = 먼 쪽 경계(최적가, 미체결 위험)
    max_entry_distance_atr: float = 3.0   # 현재가에서 이보다 먼 POI 는 '아직 때가 아님' 으로 보류
    limit_expiry_bars: int = 24           # 미체결 지정가 취소까지의 LTF 봉 수
    sl_buffer_atr: float = 0.35           # 스윕 극점 바깥 버퍼

    # ------------------------------------------------------------------
    # 5. 리스크 / 자금관리  ("quản lý vốn là thứ quan trọng bậc nhất")
    # ------------------------------------------------------------------
    risk_pct: float = 1.0             # [출처] "1틱당 계좌 1% 이하"
    min_rr: float = 2.0               # [출처] "최소 1:2"
    target_rr: float = 3.0            # [출처] "1:3 이 기본, 스윙은 1:5~1:10"
    breakeven_at_r: float = 2.0       # [출처] "2R 도달 시 SL 을 본절로"
    partial_at_r: float = 3.0         # [출처] "1:3 도달 시 분할 청산"
    partial_fraction: float = 0.5
    move_sl_only_forward: bool = True # [출처] "SL 은 절대 밀지 않는다"
    allow_averaging_down: bool = False# [출처] "물타기 금지"
    max_entries_per_setup: int = 2    # [출처] "한 셋업에 최대 2번 분할 진입"

    max_consecutive_losses: int = 2   # [출처] "2번 손절 나면 그날은 끝"
    max_daily_loss_pct: float = 3.0
    max_trades_per_day: int = 5       # [출처] "M15 기준 하루 5~8 시그널"
    max_leverage: int = 50            # [출처] "Exness 레버리지 1:50 또는 1:20 으로 낮춰라"

    # ------------------------------------------------------------------
    # 6. 세션 / 뉴스 필터
    # ------------------------------------------------------------------
    sessions: tuple[SessionWindow, ...] = (LONDON, NEWYORK)
    news_blackout_before_min: int = 15   # [출처] "NFP 전에는 물량 줄인다"
    news_blackout_after_min: int = 30
    trade_on_friday_close: bool = False

    # ------------------------------------------------------------------
    # 7. 계좌 분리  [출처] "여유 자금이면 계좌를 3개로 나눠라"
    # ------------------------------------------------------------------
    account_split: tuple[float, float, float] = (0.6, 0.3, 0.1)  # swing / scalp / highrisk

    # ------------------------------------------------------------------
    # 8. 심볼 사양 (사이징용)
    # ------------------------------------------------------------------
    contract_size: float = 100.0      # XAUUSD 1랏 = 100 oz
    min_lot: float = 0.01
    lot_step: float = 0.01
    max_lot: float = 50.0

    name: str = "swing"

    def with_(self, **kw) -> "CrowConfig":
        return replace(self, **kw)


# ----------------------------------------------------------------------
# 프리셋 — 채널이 계좌를 3종류로 나누는 방식 그대로
# ----------------------------------------------------------------------
SWING = CrowConfig(
    name="swing",
    htf="D1", mtf="H4", ltf="H1",
    risk_pct=1.0, target_rr=5.0, min_rr=3.0,
    max_trades_per_day=2, limit_expiry_bars=24,   # H1 24봉 = 하루 ("걸어두고 잔다")
)

SCALP = CrowConfig(
    name="scalp",
    htf="H1", mtf="M15", ltf="M5",
    risk_pct=0.5, target_rr=3.0, min_rr=2.0,
    max_trades_per_day=8, limit_expiry_bars=96,   # M5 96봉 = 8시간 (세션 내내 유효)
    sl_buffer_atr=0.25,
)

# [출처] "M1 은 SL 이 2~3핍이라 스프레드에 죽는다 → 소액 고위험 계좌로만"
HIGH_RISK = CrowConfig(
    name="highrisk",
    htf="M15", mtf="M5", ltf="M1",
    risk_pct=6.0, target_rr=3.0, min_rr=1.0,
    max_trades_per_day=20, max_consecutive_losses=3,
    limit_expiry_bars=10, sl_buffer_atr=0.2, max_leverage=20,
)

PRESETS: dict[str, CrowConfig] = {
    "swing": SWING,
    "scalp": SCALP,
    "highrisk": HIGH_RISK,
}

DEFAULT = SCALP


def preset(name: str) -> CrowConfig:
    key = name.lower()
    if key not in PRESETS:
        raise ValueError(f"알 수 없는 프리셋: {name} (가능: {', '.join(PRESETS)})")
    return PRESETS[key]
