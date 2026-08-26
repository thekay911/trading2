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
    risk_pct: float = 2.0             # 운용 규칙: 거래당 2%
    #    [참고] 채널 원문은 "1틱당 계좌 1% 이하"
    min_rr: float = 2.0               # [출처] "최소 1:2"
    target_rr: float = 3.0            # [출처] "1:3 이 기본, 스윙은 1:5~1:10"
    breakeven_at_r: float = 2.0       # [출처] "2R 도달 시 SL 을 본절로"
    partial_at_r: float = 3.0         # [출처] "1:3 도달 시 분할 청산"
    partial_fraction: float = 0.5
    move_sl_only_forward: bool = True # [출처] "SL 은 절대 밀지 않는다"
    allow_averaging_down: bool = False# [출처] "물타기 금지"
    max_entries_per_setup: int = 2    # [출처] "한 셋업에 최대 2번 분할 진입"

    max_consecutive_losses: int = 2   # [출처] "2번 손절 나면 그날은 끝"
    max_daily_loss_pct: float = 3.0   # 당일 소프트 한도 — 다음 날 자동 해제

    # 계좌 서킷브레이커: 도달하면 '복기 전까지' 잠근다.
    # 위의 일일 한도와 다른 점은 자정에 자동으로 풀리지 않는다는 것이다.
    # 진 날을 그냥 넘기지 않고 원인을 확인한 뒤 재개하기 위한 장치.
    hard_stop_loss_pct: float = 10.0
    halt_requires_review: bool = True  # False 면 다음 날 자동 해제
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
    # 8. 심볼 사양 (사이징용) — 기본값은 XAUUSD
    # ------------------------------------------------------------------
    contract_size: float = 100.0      # XAUUSD 1랏 = 100 oz → $1 움직임 = $100
    min_lot: float = 0.01
    lot_step: float = 0.01
    max_lot: float = 50.0

    # ------------------------------------------------------------------
    # 9. 손절 폭 가드
    #
    #    금에서 "핍" 은 브로커·트레이더마다 10배까지 차이 난다.
    #      pip_size = 1.00  →  1핍 = $1     (채널이 "SL 2~3핍" 이라 쓴 기준)
    #      pip_size = 0.10  →  1핍 = $0.10  (많은 브로커의 표기)
    #      pip_size = 0.01  →  1핍 = MT5 포인트 1
    #    내부 계산은 전부 **가격(달러)** 으로 하고, pip_size 는 표시와
    #    입력 변환에만 쓴다. 그래야 이 혼동이 계산까지 번지지 않는다.
    # ------------------------------------------------------------------
    pip_size: float = 1.0             # 1핍이 몇 달러인가

    #: 손절 범위를 어떻게 쓸 것인가
    #:   "filter" — 구조가 만든 손절이 범위 밖이면 그 셋업을 버린다
    #:   "clamp"  — 범위보다 좁으면 넓혀서 쓴다. 범위보다 넓으면 버린다
    #:              (좁히는 쪽으로는 절대 건드리지 않는다. 구조 안쪽으로
    #:               손절을 밀어 넣으면 그냥 맞으러 가는 자리가 된다)
    sl_mode: str = "filter"
    min_sl_price: float = 0.0         # 이보다 좁으면 스프레드·노이즈에 먹힌다
    max_sl_price: float = 0.0         # 이보다 넓으면 그 프리셋의 성격이 아니다
    max_spread_ratio: float = 0.0     # 스프레드 / 손절폭 상한
    #    [출처] "M1 은 SL 이 2~3핍이라 스프레드에 죽는다" — 이 규칙을 수치화한 것

    name: str = "swing"

    def with_(self, **kw) -> "CrowConfig":
        return replace(self, **kw)

    # --- 핍 <-> 가격 ---------------------------------------------------
    def pips_to_price(self, pips: float) -> float:
        return pips * self.pip_size

    def price_to_pips(self, price: float) -> float:
        return price / self.pip_size if self.pip_size else 0.0

    def with_sl_pips(self, lo: float, hi: float, pip_size: float | None = None) -> "CrowConfig":
        """손절 범위를 핍으로 지정한다. 내부에는 가격으로 저장된다."""
        size = self.pip_size if pip_size is None else pip_size
        return replace(self, pip_size=size,
                       min_sl_price=lo * size, max_sl_price=hi * size)

    @property
    def sl_pips(self) -> tuple[float, float]:
        return (self.price_to_pips(self.min_sl_price),
                self.price_to_pips(self.max_sl_price))

    def sl_label(self) -> str:
        lo, hi = self.sl_pips
        return (f"{lo:g}~{hi:g}핍 (${self.min_sl_price:g}~${self.max_sl_price:g}, "
                f"1핍=${self.pip_size:g})")

    # ------------------------------------------------------------------
    def validate(self) -> list[str]:
        """설정끼리 모순되는 조합을 찾아낸다.

        가장 흔한 사고는 '거래당 리스크'와 '일일 손실 한도'가 어긋나는 경우다.
        일일 한도가 리스크보다 낮으면 첫 손절에서 하루가 끝나 버려서
        연속 손절 규칙이 아예 작동하지 않는다.
        """
        out: list[str] = []
        need = self.risk_pct * self.max_consecutive_losses
        if self.max_daily_loss_pct < self.risk_pct:
            out.append(
                f"[모순] 일일 손실 한도 {self.max_daily_loss_pct}% < 거래당 리스크 "
                f"{self.risk_pct}% → 첫 손절에서 그날이 끝난다")
        elif self.max_daily_loss_pct < need:
            out.append(
                f"[주의] 일일 손실 한도 {self.max_daily_loss_pct}% < 리스크×연속손절 "
                f"{need:.1f}% → 연속 손절 규칙보다 일일 한도가 먼저 걸린다")
        if self.min_rr > self.target_rr:
            out.append(f"[모순] min_rr {self.min_rr} > target_rr {self.target_rr}")
        if self.max_sl_price and self.min_sl_price and self.min_sl_price >= self.max_sl_price:
            out.append("[모순] min_sl_price >= max_sl_price → 모든 셋업이 기각된다")
        if self.min_sl_price and self.max_sl_price:
            lo, hi = self.sl_pips
            ratio = hi / max(lo, 1e-9)
            if ratio > 6.0:
                out.append(
                    f"[주의] 손절 허용폭이 너무 넓다 ({lo:g}~{hi:g}핍). "
                    "프리셋의 성격이 흐려져 걸러내는 기능을 못 한다")
            elif ratio < 1.5:
                out.append(
                    f"[주의] 손절 허용폭이 너무 좁다 ({lo:g}~{hi:g}핍, {ratio:.2f}배). "
                    "구조상 손절은 이 창에 잘 들어오지 않아 대부분의 셋업이 "
                    "sl_too_tight / sl_too_wide 로 기각된다")
        if self.risk_pct > 3.0:
            out.append(
                f"[경고] 거래당 리스크 {self.risk_pct}% 는 파산 지향 설정이다. "
                "소액 전용 계좌에서만 쓸 것")
        if self.hard_stop_loss_pct and self.hard_stop_loss_pct <= self.max_daily_loss_pct:
            out.append(
                f"[주의] 서킷브레이커 {self.hard_stop_loss_pct}% ≤ 일일 한도 "
                f"{self.max_daily_loss_pct}% → 일일 한도가 먼저 걸려 서킷브레이커는 "
                "사실상 작동하지 않는다")
        if self.partial_at_r >= self.target_rr and self.partial_fraction > 0:
            out.append(
                f"[주의] 분할 청산 {self.partial_at_r}R 이 목표 {self.target_rr}R 이상 "
                "→ 목표에 닿아야 분할이 되므로 사실상 분할이 없다")
        if self.breakeven_at_r >= self.target_rr:
            out.append(
                f"[주의] 본절 이동 {self.breakeven_at_r}R 이 목표 {self.target_rr}R 이상 "
                "→ 본절이 사실상 작동하지 않는다")
        return out


# ----------------------------------------------------------------------
# 리스크 사다리 (운용 규칙: 거래당 2%, 1:3 RR)
#
#   거래당        -2%
#   연속 2회 손절  -4%   → 그날 매매 종료
#   일일 한도      -6%   → 그날 매매 종료 (연속이 아니어도)
#   서킷브레이커   -10%  → 잠금. 복기 전까지 재개 불가
#
# 각 단계는 앞 단계보다 커야 의미가 있다. 일일 한도가 거래당 리스크보다
# 낮으면 첫 손절에서 하루가 끝나 연속 손절 규칙이 죽는다 — validate() 가 잡는다.
#
# 프리셋 — 전부 XAUUSD 기준으로 조정되어 있다.
#
# 손절 폭 가드는 '금 달러' 단위다. 금은 M5 ATR 이 대략 $1.5~4,
# H1 이 $4~10, D1 이 $25~40 수준이라 프레임마다 성격이 확연히 다르다.
# 값은 전략을 방해하지 않을 만큼 넉넉하고, 헛발질은 걸러낼 만큼 좁게 잡았다.
# 금 가격대가 크게 바뀌면(예: $2000 → $4000) 함께 넓혀야 한다.
# ----------------------------------------------------------------------
SWING = CrowConfig(
    name="swing",
    htf="D1", mtf="H4", ltf="H1",
    risk_pct=2.0, min_rr=3.0, target_rr=3.0,
    breakeven_at_r=1.5, partial_at_r=2.0,
    max_trades_per_day=2, limit_expiry_bars=24,   # H1 24봉 = 하루 ("걸어두고 잔다")
    min_sl_price=6.0, max_sl_price=60.0,          # 금 스윙: $6~60 손절
    max_spread_ratio=0.04,
    max_consecutive_losses=2, max_daily_loss_pct=6.0,
)

# 금에 가장 무난한 프레임. 런던·뉴욕 세션 안에서 대부분 결판난다.
INTRADAY = CrowConfig(
    name="intraday",
    htf="H4", mtf="H1", ltf="M15",
    risk_pct=2.0, min_rr=3.0, target_rr=3.0,
    breakeven_at_r=1.5, partial_at_r=2.0,
    max_trades_per_day=3, limit_expiry_bars=32,   # M15 32봉 = 8시간
    sl_buffer_atr=0.3,
    min_sl_price=2.5, max_sl_price=25.0,
    max_spread_ratio=0.08,
    max_consecutive_losses=2, max_daily_loss_pct=6.0,
)

SCALP = CrowConfig(
    name="scalp",
    htf="H1", mtf="M15", ltf="M5",
    risk_pct=2.0, min_rr=3.0, target_rr=3.0,
    breakeven_at_r=1.5, partial_at_r=2.0,
    max_trades_per_day=6, limit_expiry_bars=96,   # M5 96봉 = 8시간 (세션 내내 유효)
    sl_buffer_atr=0.25,
    min_sl_price=1.5, max_sl_price=10.0,
    max_spread_ratio=0.12,
    max_consecutive_losses=2, max_daily_loss_pct=6.0,
)

# [출처] "M1 은 SL 이 2~3핍이라 스프레드에 죽는다 → 소액 고위험 계좌로만"
#        "X10 sau 2 ngày" — 2일 만에 10배. 반대로 2일 만에 0이 될 수도 있다.
# 이 프리셋만 채널 원문의 6% 를 유지한다 (운용 규칙 2% 와 별개인,
# 없어져도 되는 소액 계좌용 기록). 나머지 한도는 모순 없게 맞췄다.
HIGH_RISK = CrowConfig(
    name="highrisk",
    htf="M15", mtf="M5", ltf="M1",
    risk_pct=6.0, min_rr=1.5, target_rr=3.0,
    breakeven_at_r=1.5, partial_at_r=2.0,
    # 6% 리스크면 하루 한 번 지면 끝이다. 그게 이 설정의 정직한 모습이다.
    max_trades_per_day=20, max_consecutive_losses=1,
    max_daily_loss_pct=6.0,
    limit_expiry_bars=30, sl_buffer_atr=0.2, max_leverage=20,
    min_sl_price=0.8, max_sl_price=4.0,
    max_spread_ratio=0.20,
)

PRESETS: dict[str, CrowConfig] = {
    "swing": SWING,
    "intraday": INTRADAY,
    "scalp": SCALP,
    "highrisk": HIGH_RISK,
}

DEFAULT = INTRADAY


def preset(name: str) -> CrowConfig:
    key = name.lower()
    if key not in PRESETS:
        raise ValueError(f"알 수 없는 프리셋: {name} (가능: {', '.join(PRESETS)})")
    return PRESETS[key]
