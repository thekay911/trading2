"""딜링 레인지 · 프리미엄/디스카운트 · OTE.

ICT 는 "싸게 사고 비싸게 판다" 를 문자 그대로 지킨다.
레인지의 50%(equilibrium) 를 기준으로 아래는 디스카운트, 위는 프리미엄이다.

  매수는 디스카운트에서만, 매도는 프리미엄에서만.

OTE (Optimal Trade Entry) 는 되돌림의 **62% ~ 79%** 구간이다.
70.5% 가 중심이고, 79% 를 넘어가면 셋업이 깨진 것으로 본다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

from crowcode.data import Candle
from ict.structure import BEAR, BULL, Dir, Swing

OTE_START = 0.62
OTE_OPTIMAL = 0.705
OTE_END = 0.79


@dataclass(frozen=True)
class DealingRange:
    high: float
    low: float
    high_index: int
    low_index: int

    @property
    def size(self) -> float:
        return self.high - self.low

    @property
    def equilibrium(self) -> float:
        return (self.high + self.low) / 2.0

    @property
    def direction(self) -> Dir:
        """저점이 나중이면 하락 레인지, 고점이 나중이면 상승 레인지."""
        return BULL if self.high_index > self.low_index else BEAR

    def position(self, price: float) -> float:
        """0 = 저점, 1 = 고점."""
        return (price - self.low) / self.size if self.size > 0 else 0.5

    def is_discount(self, price: float) -> bool:
        return price < self.equilibrium

    def is_premium(self, price: float) -> bool:
        return price > self.equilibrium

    def level(self, fraction: float) -> float:
        """저점에서 위로 fraction 만큼."""
        return self.low + self.size * fraction

    def retracement(self, fraction: float, side: Literal["buy", "sell"]) -> float:
        """되돌림 비율 → 가격.

        매수는 고점에서 아래로, 매도는 저점에서 위로 되돌린다.
        """
        if side == "buy":
            return self.high - self.size * fraction
        return self.low + self.size * fraction

    def ote(self, side: Literal["buy", "sell"]) -> tuple[float, float]:
        """OTE 구간 (낮은 가격, 높은 가격)."""
        a = self.retracement(OTE_START, side)
        b = self.retracement(OTE_END, side)
        return (min(a, b), max(a, b))

    def in_ote(self, price: float, side: Literal["buy", "sell"]) -> bool:
        lo, hi = self.ote(side)
        return lo <= price <= hi

    def projection(self, multiple: float, side: Literal["buy", "sell"]) -> float:
        """표준편차 확장 — 레인지 크기의 배수로 목표를 잡는다."""
        if side == "buy":
            return self.high + self.size * multiple
        return self.low - self.size * multiple


def swing_range(sw: Sequence[Swing], now: int, lookback: int = 60) -> DealingRange | None:
    """최근 스윙들로 딜링 레인지를 만든다."""
    usable = [s for s in sw if s.confirmed_at <= now and s.index >= now - lookback]
    highs = [s for s in usable if s.is_high]
    lows = [s for s in usable if not s.is_high]
    if not highs or not lows:
        return None
    h = max(highs, key=lambda s: s.price)
    l = min(lows, key=lambda s: s.price)
    if h.price <= l.price:
        return None
    return DealingRange(h.price, l.price, h.index, l.index)


def leg_range(candles: Sequence[Candle], start: int, end: int) -> DealingRange | None:
    """특정 구간(변위 레그)의 레인지. OTE 는 보통 이걸로 잰다."""
    if end <= start or end >= len(candles):
        return None
    seg = candles[start:end + 1]
    hi = max(range(len(seg)), key=lambda i: seg[i].high)
    lo = min(range(len(seg)), key=lambda i: seg[i].low)
    if seg[hi].high <= seg[lo].low:
        return None
    return DealingRange(seg[hi].high, seg[lo].low, start + hi, start + lo)
