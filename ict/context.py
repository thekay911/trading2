"""ICT Mentorship 2022 Ep.2 — Elements To A Trade Setup.

ICT 는 "여기서 사고 여기서 판다" 로 트레이드를 보지 않는다. 먼저 지금
가격이 **어떤 상태(Context)** 인지 정하고, 그 상태 안에서만 Reference
Point(오더블록·FVG·유동성풀)를 쓴다.

    주간 편향 -> 일간 편향 -> DOL(가격이 끌려갈 곳)
        -> 예상되는 스톱 헌트 -> 유동성 이벤트
        -> 확장(Expansion) -> 되돌림(Retracement)
        -> FVG / 오더블록 -> 진입

이 파일이 그 위쪽 절반(편향과 상태)을 담당한다. 아래쪽 절반(참조점)은
`pdarrays.py` 와 `liquidity.py` 에 이미 있었다. **없던 건 위쪽이었고,
그래서 코드가 아무 때나 참조점만 보고 진입했다.**

Context 4가지
    Expansion       균형점에서 빠르게 이탈 — 재가격이 진행 중
    Retracement     방금 만든 레인지 안으로 되돌아오는 중 — 진입을 찾는 상태
    Reversal        유동성을 털고 방향이 바뀌는 중
    Consolidation   레인지 안에 갇힘 — 양쪽에 주문이 쌓이는 중

진입을 찾을 수 있는 상태는 **Retracement 와 Reversal 뿐이다.**
Expansion 중에 들어가면 이미 간 걸 쫓는 것이고, Consolidation 중에는
어느 쪽으로 터질지 모른다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal, Sequence

from crowcode.data import Candle
from ict.gold import STANDARD, GoldProfile, Volatility
from ict.structure import BEAR, BULL, Dir
from ict.timeops import ny_date

Context = Literal["Expansion", "Retracement", "Reversal", "Consolidation"]

#: 진입을 프레임할 수 있는 상태
TRADEABLE: tuple[Context, ...] = ("Retracement", "Reversal")


@dataclass(frozen=True)
class Range:
    """딜링 레인지와 그 균형점."""
    high: float
    low: float

    @property
    def equilibrium(self) -> float:
        return (self.high + self.low) / 2.0

    @property
    def size(self) -> float:
        return self.high - self.low

    def position(self, price: float) -> float:
        return (price - self.low) / self.size if self.size > 0 else 0.5

    def is_discount(self, price: float) -> bool:
        """상승 후 되돌림이 50% 아래 — 매수 셋업을 프레임하는 자리."""
        return price < self.equilibrium

    def is_premium(self, price: float) -> bool:
        return price > self.equilibrium


@dataclass
class Bias:
    """주간·일간 편향과 DOL."""
    weekly: Dir | None
    daily: Dir | None
    dol: float | None            #: 가격이 끌려갈 유동성
    dol_label: str
    reasons: list[str] = field(default_factory=list)

    @property
    def agreed(self) -> Dir | None:
        """주간과 일간이 같은 쪽을 볼 때만 방향으로 인정한다."""
        if self.weekly is not None and self.weekly == self.daily:
            return self.weekly
        return None

    def describe(self) -> str:
        n = {BULL: "상승", BEAR: "하락", None: "-"}
        out = [f"주간 {n[self.weekly]}  일간 {n[self.daily]}  "
               f"합의 {n[self.agreed]}"]
        if self.dol is not None:
            out.append(f"DOL -> {self.dol_label} {self.dol:.2f}")
        out += [f"  · {r}" for r in self.reasons]
        return "\n".join(out)


# ----------------------------------------------------------------------
def _period_extremes(candles: Sequence[Candle], lo_i: int, hi_i: int
                     ) -> tuple[float, float]:
    seg = candles[lo_i:hi_i]
    return max(c.high for c in seg), min(c.low for c in seg)


def week_of(d: date) -> tuple[int, int]:
    return d.isocalendar()[:2]


def bias(candles: Sequence[Candle], now: int, day_of_bar: Sequence[date],
         gold: GoldProfile = STANDARD) -> Bias:
    """주간 편향 -> 일간 편향 -> DOL.

    ICT 가 말하는 대로, 이번 주가 어느 쪽 유동성을 목표로 삼을 가능성이
    높은지를 먼저 본다. '월~금 한 방향' 이 아니라 **어느 쪽 유동성이 더
    중요한 목표인가** 를 정하는 것이다.

    현재가에서 가장 가까운 고·저를 무조건 목표로 잡지 않는다 —
    아직 안 건드린(untapped) 쪽을 본다.
    """
    b = Bias(None, None, None, "")
    if now < 200:
        return b

    today = day_of_bar[now]
    this_week = week_of(today)

    # 주 경계 찾기
    wk_start = now
    while wk_start > 0 and week_of(day_of_bar[wk_start - 1]) == this_week:
        wk_start -= 1
    prev_end = wk_start - 1
    if prev_end < 50:
        return b
    prev_week = week_of(day_of_bar[prev_end])
    pw_start = prev_end
    while pw_start > 0 and week_of(day_of_bar[pw_start - 1]) == prev_week:
        pw_start -= 1

    pwh, pwl = _period_extremes(candles, pw_start, prev_end + 1)
    price = candles[now].close

    # --- 주간 편향: 전주 레인지의 어디에서 이번 주를 보내고 있는가 ------
    rng = Range(pwh, pwl)
    if rng.size > 0:
        pos = rng.position(price)
        if pos > 1.0:
            b.weekly = BULL
            b.reasons.append(f"전주 고점 {pwh:.2f} 위에서 거래 중 — 위쪽 유동성이 목표")
        elif pos < 0.0:
            b.weekly = BEAR
            b.reasons.append(f"전주 저점 {pwl:.2f} 아래에서 거래 중")
        elif pos < 0.4:
            b.weekly = BULL
            b.reasons.append(f"전주 레인지의 {pos:.0%} — 디스카운트, 위를 가지러 갈 자리")
        elif pos > 0.6:
            b.weekly = BEAR
            b.reasons.append(f"전주 레인지의 {pos:.0%} — 프리미엄")

    # --- 일간 편향: 전일 레인지 대비 --------------------------------
    prev_day = today - timedelta(days=1)
    d_end = now
    while d_end > 0 and day_of_bar[d_end] == today:
        d_end -= 1
    d_start = d_end
    while d_start > 0 and day_of_bar[d_start - 1] == day_of_bar[d_end]:
        d_start -= 1
    if d_end > d_start:
        pdh, pdl = _period_extremes(candles, d_start, d_end + 1)
        dr = Range(pdh, pdl)
        if dr.size > 0:
            pos = dr.position(price)
            if pos < 0.4:
                b.daily = BULL
                b.reasons.append(f"전일 레인지의 {pos:.0%} (디스카운트)")
            elif pos > 0.6:
                b.daily = BEAR
                b.reasons.append(f"전일 레인지의 {pos:.0%} (프리미엄)")

        # --- DOL: 아직 안 건드린 쪽 ---------------------------------
        took_h = any(c.high > pdh for c in candles[d_end + 1:now + 1])
        took_l = any(c.low < pdl for c in candles[d_end + 1:now + 1])
        if not took_h and took_l:
            b.dol, b.dol_label = pdh, "PDH"
            b.reasons.append("전일 저점은 이미 털었고 고점은 남았다")
        elif not took_l and took_h:
            b.dol, b.dol_label = pdl, "PDL"
            b.reasons.append("전일 고점은 이미 털었고 저점은 남았다")
        elif not took_h and not took_l:
            d = b.agreed or b.daily or b.weekly
            if d == BULL:
                b.dol, b.dol_label = pdh, "PDH"
            elif d == BEAR:
                b.dol, b.dol_label = pdl, "PDL"
    return b


# ----------------------------------------------------------------------
def context(candles: Sequence[Candle], now: int, atr: float,
            lookback: int = 40, gold: GoldProfile = STANDARD) -> tuple[Context, Range]:
    """지금 가격이 어떤 상태인가.

    Consolidation  최근 레인지가 ATR 대비 좁다 — 양쪽에 주문이 쌓이는 중
    Expansion      마지막 봉들이 레인지를 빠르게 벗어나는 중 — 이미 가는 중
    Reversal       레인지 극점을 털고 되돌아왔다 — 스톱런 뒤 전환
    Retracement    확장 뒤 레인지 안으로 돌아오는 중 — 진입을 찾는 상태
    """
    lo = max(0, now - lookback)
    seg = candles[lo:now + 1]
    hi = max(c.high for c in seg)
    low = min(c.low for c in seg)
    r = Range(hi, low)
    price = candles[now].close

    if atr <= 0 or r.size <= 0:
        return "Consolidation", r

    # 레인지가 ATR 몇 배인가 — 좁으면 통합
    if r.size < atr * 3.0:
        return "Consolidation", r

    # 최근 3봉이 레인지 극점을 새로 만들고 있으면 확장
    recent = candles[max(0, now - 2):now + 1]
    made_high = max(c.high for c in recent) >= hi - 1e-9
    made_low = min(c.low for c in recent) <= low + 1e-9
    if made_high or made_low:
        return "Expansion", r

    # 레인지 극점을 털고 반대로 종가 — 전환
    tail = candles[max(0, now - 10):now + 1]
    swept_high = any(c.high >= hi - 1e-9 for c in tail) and price < r.equilibrium
    swept_low = any(c.low <= low + 1e-9 for c in tail) and price > r.equilibrium
    if swept_high or swept_low:
        return "Reversal", r

    return "Retracement", r
