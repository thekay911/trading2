"""PD Array — ICT 가 진입을 허용하는 유일한 자리.

ICT 는 "아무데서나 사지 않는다". 진입은 항상 **불균형이 남은 구역**이나
**주문이 남은 구역**에서만 한다. 그 구역들을 PD Array 라 부른다.

프리미엄에서 파는 순서 (비쌀수록 위)      디스카운트에서 사는 순서 (쌀수록 아래)
  · Old Low (되돌림 저점)                    · Old High
  · Rejection Block                          · Rejection Block
  · Bearish Order Block                      · Bullish Order Block
  · Bearish FVG / Liquidity Void             · Bullish FVG / Liquidity Void
  · Bearish Breaker                          · Bullish Breaker
  · Bearish Mitigation Block                 · Bullish Mitigation Block

여기서는 실제로 코드화가 되는 것만 구현한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Sequence

from crowcode.data import Candle, atr
from ict.structure import BEAR, BULL, Dir, StructureEvent

ArrayKind = Literal["FVG", "OB", "BREAKER", "BPR", "IFVG", "REJECTION"]


@dataclass(frozen=True)
class PDArray:
    kind: ArrayKind
    direction: Dir             # BULL = 매수 자리 (아래에서 받친다)
    top: float
    bottom: float
    index: int                 # 형성 봉
    origin: int | None = None  # 만들어낸 구조 이벤트 등

    @property
    def mid(self) -> float:
        """Consequent Encroachment — FVG 의 50%. ICT 가 가장 중요하게 보는 지점."""
        return (self.top + self.bottom) / 2.0

    @property
    def size(self) -> float:
        return self.top - self.bottom

    def contains(self, price: float) -> bool:
        return self.bottom <= price <= self.top

    def entry(self, style: str = "ce") -> float:
        """진입 가격. ce = 50%(기본), proximal = 가까운 쪽, distal = 먼 쪽."""
        if style == "ce":
            return self.mid
        if self.direction == BULL:
            return self.top if style == "proximal" else self.bottom
        return self.bottom if style == "proximal" else self.top


# ----------------------------------------------------------------------
# FVG — 3봉 불균형
# ----------------------------------------------------------------------
def fair_value_gaps(candles: Sequence[Candle], min_size: float = 0.0,
                    start: int = 0) -> list[PDArray]:
    out: list[PDArray] = []
    for i in range(max(2, start), len(candles)):
        a, c = candles[i - 2], candles[i]
        if c.low - a.high > min_size:
            out.append(PDArray("FVG", BULL, c.low, a.high, i))
        elif a.low - c.high > min_size:
            out.append(PDArray("FVG", BEAR, a.low, c.high, i))
    return out


def is_filled(candles: Sequence[Candle], arr: PDArray, upto: int,
              full: bool = False) -> bool:
    """FVG 가 메워졌는가. `full` 이면 완전 통과, 아니면 CE(50%) 터치."""
    level = arr.bottom if arr.direction == BULL else arr.top
    target = level if full else arr.mid
    for k in range(arr.index + 1, min(upto, len(candles) - 1) + 1):
        c = candles[k]
        if arr.direction == BULL and c.low <= target:
            return True
        if arr.direction == BEAR and c.high >= target:
            return True
    return False


def inversion_fvgs(candles: Sequence[Candle], gaps: Sequence[PDArray],
                   upto: int) -> list[PDArray]:
    """Inversion FVG — 뚫린 FVG 는 반대 방향 지지/저항이 된다."""
    out: list[PDArray] = []
    for g in gaps:
        for k in range(g.index + 1, min(upto, len(candles) - 1) + 1):
            c = candles[k]
            broke = (g.direction == BULL and c.close < g.bottom) or \
                    (g.direction == BEAR and c.close > g.top)
            if broke:
                out.append(PDArray("IFVG", -g.direction, g.top, g.bottom, k, g.index))
                break
    return out


# ----------------------------------------------------------------------
# 오더블록 — 변위 직전의 반대 색 캔들
# ----------------------------------------------------------------------
def order_block(candles: Sequence[Candle], impulse_end: int, direction: Dir,
                lookback: int = 12, body_only: bool = False) -> PDArray | None:
    """상승 변위 직전의 마지막 '음봉' 이 강세 오더블록이다."""
    for j in range(impulse_end - 1, max(-1, impulse_end - lookback) - 1, -1):
        c = candles[j]
        opposite = (direction == BULL and c.close < c.open) or \
                   (direction == BEAR and c.close > c.open)
        if not opposite:
            continue
        top = c.body_top if body_only else c.high
        bot = c.body_bottom if body_only else c.low
        return PDArray("OB", direction, top, bot, j, impulse_end)
    return None


def breaker(candles: Sequence[Candle], ob: PDArray, upto: int) -> PDArray | None:
    """브레이커 — 실패한 오더블록. 뚫리고 나면 반대 역할을 한다."""
    for k in range(ob.index + 1, min(upto, len(candles) - 1) + 1):
        c = candles[k]
        failed = (ob.direction == BULL and c.close < ob.bottom) or \
                 (ob.direction == BEAR and c.close > ob.top)
        if failed:
            return PDArray("BREAKER", -ob.direction, ob.top, ob.bottom, k, ob.index)
    return None


def rejection_block(candles: Sequence[Candle], index: int, direction: Dir) -> PDArray | None:
    """리젝션 블록 — 몸통이 아니라 '꼬리' 가 만든 구역."""
    c = candles[index]
    if direction == BULL:
        if c.body_bottom <= c.low:
            return None
        return PDArray("REJECTION", BULL, c.body_bottom, c.low, index)
    if c.high <= c.body_top:
        return None
    return PDArray("REJECTION", BEAR, c.high, c.body_top, index)


# ----------------------------------------------------------------------
# BPR — 반대 방향 FVG 두 개가 겹친 구간. 가장 강한 반응이 나온다.
# ----------------------------------------------------------------------
def balanced_price_ranges(gaps: Sequence[PDArray], max_gap_bars: int = 30) -> list[PDArray]:
    out: list[PDArray] = []
    for i, g1 in enumerate(gaps):
        for g2 in gaps[i + 1:]:
            if g2.index - g1.index > max_gap_bars:
                break
            if g1.direction == g2.direction:
                continue
            top = min(g1.top, g2.top)
            bot = max(g1.bottom, g2.bottom)
            if top > bot:
                out.append(PDArray("BPR", g2.direction, top, bot, g2.index, g1.index))
    return out


# ----------------------------------------------------------------------
# 수집
# ----------------------------------------------------------------------
def collect(
    candles: Sequence[Candle],
    event: StructureEvent,
    upto: int,
    kinds: Sequence[str] = ("FVG", "OB", "BREAKER"),
    min_fvg_atr: float = 0.15,
) -> list[PDArray]:
    """구조 이벤트가 만든 변위 안에서 진입 가능한 PD Array 를 모은다.

    ICT 2022 모델은 **변위가 남긴 FVG** 에 들어간다. 그래서 변위 구간으로
    범위를 한정하는 것이 핵심이다 — 아무 FVG 나 쓰면 모델이 아니다.
    """
    out: list[PDArray] = []
    d = event.displacement
    lo = d.start if d else max(0, event.index - 10)
    hi = event.index
    a = atr(candles[max(0, hi - 60):hi + 1])

    if "FVG" in kinds:
        for g in fair_value_gaps(candles, min_size=a * min_fvg_atr, start=lo):
            if g.index <= hi and g.direction == event.direction:
                out.append(g)
    if "OB" in kinds:
        ob = order_block(candles, hi, event.direction)
        if ob is not None:
            out.append(ob)
    if "BREAKER" in kinds:
        ob = order_block(candles, hi, -event.direction)
        if ob is not None:
            br = breaker(candles, ob, upto)
            if br is not None and br.direction == event.direction:
                out.append(br)

    fresh = [p for p in out if not is_filled(candles, p, upto)]
    fresh.sort(key=lambda p: p.index, reverse=True)
    return fresh


def unicorn(arrays: Sequence[PDArray]) -> PDArray | None:
    """유니콘 — 브레이커와 FVG 가 겹친 자리. ICT 가 최상급으로 치는 셋업."""
    brs = [a for a in arrays if a.kind == "BREAKER"]
    gaps = [a for a in arrays if a.kind == "FVG"]
    for b in brs:
        for g in gaps:
            if b.direction != g.direction:
                continue
            top, bot = min(b.top, g.top), max(b.bottom, g.bottom)
            if top > bot:
                return PDArray("BPR", b.direction, top, bot, max(b.index, g.index), b.index)
    return None
