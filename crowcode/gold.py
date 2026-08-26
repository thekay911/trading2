"""XAUUSD(금) 전용 지식.

이 저장소는 금만 매매한다는 전제로 조정되어 있다. 여기에는 심볼 이름 해석,
브로커 사양 점검, 최소 필요 자본 계산, 금을 움직이는 지표 목록처럼
'금이라서 생기는 문제'만 모아 둔다.

금의 성격 (전략 파라미터가 이 위에 얹혀 있다)
--------------------------------------------
  · 1랏 = 100 oz → $1 움직임 = $100.  0.01랏이면 $1 움직임 = $1
  · 호가 단위 0.01 → MT5 '포인트' 1 = $0.01.  흔히 말하는 "2핍"은 보통 $2 를 뜻한다
  · 변동성:  M5 ATR ≈ $1.5~4,  H1 ≈ $4~10,  D1 ≈ $25~40
  · 스프레드: ECN 15~35 포인트($0.15~0.35), 스탠다드 30~60 포인트
  · 스왑이 대체로 크게 마이너스 → 스윙은 보유 비용을 반드시 계산할 것
  · 세션: 런던 오픈(07:00 GMT)과 뉴욕(12:30~17:00 GMT)에 거의 모든 움직임이 몰린다
    아시아 세션은 좁은 박스가 잦아 이 엔진에서는 제외되어 있다
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable, Sequence

from crowcode.config import CrowConfig

# ----------------------------------------------------------------------
# 심볼 이름 — 브로커마다 접미사가 다르다
# ----------------------------------------------------------------------
CANONICAL = "XAUUSD"

#: 흔한 표기. 우선순위 순서대로 찾는다.
SYMBOL_PATTERNS: tuple[str, ...] = (
    "XAUUSD", "XAUUSD.m", "XAUUSDm", "XAUUSD.a", "XAUUSD_", "XAUUSD.raw",
    "XAUUSD.pro", "XAUUSD-ECN", "XAUUSDx", "GOLD", "GOLD.m", "Gold", "XAUUSD.i",
)


def resolve_symbol(available: Iterable[str]) -> str | None:
    """마켓워치에 있는 이름들 중에서 금 심볼을 고른다.

    브로커마다 XAUUSD / XAUUSD.m / XAUUSDm / GOLD 로 제각각이라
    이름을 잘못 넣어 "심볼 없음" 으로 죽는 일이 잦다.
    """
    names = [n for n in available if n]
    if not names:
        return None
    upper = {n.upper(): n for n in names}

    for pat in SYMBOL_PATTERNS:
        if pat.upper() in upper:
            return upper[pat.upper()]

    # 접미사가 붙은 변형: XAUUSD 로 시작하는 것 중 가장 짧은 이름
    starts = sorted([n for n in names if n.upper().startswith("XAUUSD")], key=len)
    if starts:
        return starts[0]

    golds = sorted([n for n in names if "GOLD" in n.upper()], key=len)
    return golds[0] if golds else None


# ----------------------------------------------------------------------
# 참고용 표준 사양 (실제 값은 항상 브로커에서 읽는다)
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class GoldReference:
    contract_size: float = 100.0     # 1랏 = 100 트로이온스
    point: float = 0.01
    digits: int = 2
    typical_spread_points: int = 25  # ECN 기준
    money_per_dollar_per_lot: float = 100.0

    def money_at(self, lots: float, price_move: float) -> float:
        """`lots` 랏으로 금이 `price_move` 달러 움직였을 때의 손익."""
        return lots * self.money_per_dollar_per_lot * price_move


REFERENCE = GoldReference()


# ----------------------------------------------------------------------
# 자본 요건
# ----------------------------------------------------------------------
def money_per_price_unit(info) -> float:
    """가격 1달러당 1랏의 손익. 브로커 사양에서 계산한다."""
    ts = getattr(info, "tick_size", 0.0) or 0.0
    tv = getattr(info, "tick_value", 0.0) or 0.0
    if ts > 0 and tv > 0:
        return tv / ts
    return getattr(info, "contract_size", REFERENCE.contract_size)


def min_viable_balance(cfg: CrowConfig, sl_price: float, info=None) -> float:
    """손절 폭 `sl_price` 인 셋업을 최소 랏으로라도 잡으려면 필요한 잔고.

    이 금액에 못 미치면 시그널이 나와도 'sizing' 으로 계속 기각된다.
    금은 0.01랏 · $1 움직임 = $1 이라 손절 $5 짜리 셋업의 최소 리스크가 $5,
    거래당 0.5% 로 잡으면 잔고 $1,000 이 필요하다.
    """
    per_unit = money_per_price_unit(info) if info is not None else cfg.contract_size
    min_lot = getattr(info, "volume_min", cfg.min_lot) if info is not None else cfg.min_lot
    if cfg.risk_pct <= 0 or sl_price <= 0:
        return 0.0
    return min_lot * sl_price * per_unit * 100.0 / cfg.risk_pct


# ----------------------------------------------------------------------
# 사전 점검
# ----------------------------------------------------------------------
@dataclass
class Check:
    level: str        # ok / warn / fail
    title: str
    detail: str

    def line(self) -> str:
        mark = {"ok": "OK  ", "warn": "주의", "fail": "실패"}.get(self.level, "?   ")
        return f"[{mark}] {self.title}\n         {self.detail}"


@dataclass
class Preflight:
    symbol: str
    preset: str
    balance: float
    checks: list[Check] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return any(c.level == "fail" for c in self.checks)

    def report(self) -> str:
        head = [
            "=" * 60,
            f" XAUUSD 사전 점검 — {self.symbol} / 프리셋 {self.preset} / 잔고 {self.balance:,.2f}",
            "=" * 60,
        ]
        body = [c.line() for c in self.checks]
        tail = ["-" * 60,
                ("이 조합으로는 매매할 수 없다. 위의 '실패' 항목을 먼저 해결할 것."
                 if self.failed else "치명적 문제 없음. 다만 '주의' 항목은 읽어 볼 것.")]
        return "\n".join(head + body + tail)


def preflight(cfg: CrowConfig, info, balance: float, spread_price: float | None = None) -> Preflight:
    """브로커·계좌·프리셋 조합이 실제로 돌아갈 수 있는지 미리 확인한다.

    실전에서 "왜 한 번도 진입을 안 하지?" 의 원인은 대부분 여기서 잡힌다.
    """
    out = Preflight(symbol=getattr(info, "name", CANONICAL), preset=cfg.name, balance=balance)
    per_unit = money_per_price_unit(info)
    min_lot = getattr(info, "volume_min", cfg.min_lot)
    point = getattr(info, "point", REFERENCE.point) or REFERENCE.point

    # --- 1) 계약 사양이 금이 맞는지
    if abs(per_unit - REFERENCE.money_per_dollar_per_lot) > 1.0:
        out.checks.append(Check(
            "warn", "계약 사양",
            f"1랏 · $1 움직임 = {per_unit:,.2f} (금 표준은 100). "
            "마이크로 계좌이거나 심볼이 금이 아닐 수 있다."))
    else:
        out.checks.append(Check(
            "ok", "계약 사양", f"1랏 · $1 움직임 = {per_unit:,.2f}, 최소 랏 {min_lot}"))

    # --- 2) 자본 요건
    typical = cfg.min_sl_price or 3.0
    worst = cfg.max_sl_price or typical * 3
    need_typical = min_viable_balance(cfg, typical, info)
    need_worst = min_viable_balance(cfg, worst, info)
    if balance < need_typical:
        out.checks.append(Check(
            "fail", "자본 요건",
            f"손절 ${typical:.2f} 짜리 최소 셋업조차 잡으려면 {need_typical:,.0f} 필요 "
            f"(현재 {balance:,.0f}). 리스크%를 올리거나 상위 프레임 프리셋을 쓸 것."))
    elif balance < need_worst:
        out.checks.append(Check(
            "warn", "자본 요건",
            f"손절 ${typical:.2f} 셋업은 가능하지만, 폭이 ${worst:.2f} 까지 벌어지는 셋업은 "
            f"{need_worst:,.0f} 이 있어야 잡는다 (현재 {balance:,.0f}). 그런 날은 그냥 넘어간다."))
    else:
        out.checks.append(Check(
            "ok", "자본 요건",
            f"손절 ${typical:.2f}~${worst:.2f} 전 구간 진입 가능 "
            f"(필요 {need_worst:,.0f} ≤ 잔고 {balance:,.0f})"))

    # --- 3) 최소 이격
    stops_pts = getattr(info, "stops_level_points", 0) or 0
    stops_price = stops_pts * point
    if cfg.min_sl_price and stops_price >= cfg.min_sl_price:
        out.checks.append(Check(
            "fail", "브로커 최소 이격",
            f"브로커가 요구하는 이격 {stops_pts}포인트(${stops_price:.2f}) 가 "
            f"이 프리셋의 최소 손절 ${cfg.min_sl_price:.2f} 이상 → 모든 주문이 거부된다."))
    elif stops_price > 0:
        out.checks.append(Check(
            "ok", "브로커 최소 이격",
            f"{stops_pts}포인트(${stops_price:.2f}) — 최소 손절 ${cfg.min_sl_price:.2f} 안에 들어간다"))
    else:
        out.checks.append(Check("ok", "브로커 최소 이격", "제한 없음(0)"))

    # --- 4) 스프레드
    if spread_price is not None and spread_price > 0:
        pts = spread_price / point
        limit = (cfg.min_sl_price or 0) * cfg.max_spread_ratio
        if cfg.max_spread_ratio > 0 and limit > 0 and spread_price > limit:
            out.checks.append(Check(
                "warn", "스프레드",
                f"현재 {pts:.0f}포인트(${spread_price:.2f}). 최소 손절 셋업 기준 허용치는 "
                f"${limit:.2f} → 좁은 손절 셋업은 계속 걸러진다. "
                "ECN 계좌로 바꾸거나 상위 프레임을 쓸 것."))
        else:
            out.checks.append(Check(
                "ok", "스프레드", f"{pts:.0f}포인트(${spread_price:.2f})"))

    # --- 5) 설정 자체의 모순
    warns = cfg.validate()
    if warns:
        for w in warns:
            level = "fail" if w.startswith("[모순]") else "warn"
            out.checks.append(Check(level, "설정 점검", w))
    else:
        out.checks.append(Check("ok", "설정 점검", "규칙 간 모순 없음"))

    # --- 6) 스윙 보유 비용
    if cfg.htf in ("D1", "W1"):
        out.checks.append(Check(
            "warn", "보유 비용",
            "금은 스왑이 대체로 크게 마이너스다. 며칠 보유하는 스윙은 "
            "브로커의 XAUUSD 스왑을 확인하고 목표 R 에 반영할 것."))

    return out


# ----------------------------------------------------------------------
# 금을 움직이는 지표
# ----------------------------------------------------------------------
#: 금 변동성이 실제로 튀는 이벤트. 시각은 매달 바뀌므로 직접 채워야 한다.
MOVERS: tuple[tuple[str, str], ...] = (
    ("FOMC 금리 결정 / 기자회견", "가장 크다. 전후 1시간은 아예 쉬는 편이 낫다"),
    ("미국 CPI", "발표 직후 $20~40 급변이 흔하다"),
    ("비농업고용(NFP)", "매월 첫 금요일 12:30 GMT"),
    ("PCE 물가지수", "연준이 보는 지표라 반응이 크다"),
    ("파월 의장 연설", "예정에 없던 발언도 포함"),
    ("미국 실업수당 청구", "매주 목요일 12:30 GMT, 반응은 중간"),
    ("ISM 제조업/서비스업", "14:00 GMT"),
    ("지정학적 헤드라인", "예측 불가. 스윙 포지션의 갭 리스크"),
)


def parse_news(spec: str):
    """'2026-09-04 12:30, 2026-09-11 12:30' → NewsEvent 목록 (GMT 기준).

    세미콜론·쉼표·줄바꿈 아무거나 구분자로 쓸 수 있다.
    """
    from crowcode.sessions import NewsEvent

    out = []
    for chunk in spec.replace(";", ",").replace("\n", ",").split(","):
        text = chunk.strip()
        if not text:
            continue
        stamp = text
        name = "news"
        if "@" in text:                       # "CPI@2026-09-11 12:30"
            name, stamp = [x.strip() for x in text.split("@", 1)]
        try:
            ts = datetime.fromisoformat(stamp.replace("/", "-"))
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        out.append(NewsEvent(ts, name, "high"))
    return out


def movers_table() -> str:
    width = max(len(n) for n, _ in MOVERS)
    lines = ["금이 크게 움직이는 이벤트 (시각은 GMT, 매달 확인 필요)", ""]
    lines += [f"  {n:<{width}}  {d}" for n, d in MOVERS]
    lines += ["", "  --news 옵션에 'CPI@2026-09-11 12:30' 형식으로 넣으면 전후 진입이 차단된다."]
    return "\n".join(lines)
