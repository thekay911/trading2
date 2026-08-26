"""유동성 / POI: 동일 고저점, 유동성 스윕, 오더블록, FVG.

채널 원문 요지
--------------
  "유동성 죽이고 나서 올린다"  → sweep 후 진입
  "직전 저점 아래 SL"          → 스윕 극점 바깥에 손절
  "백업(=되돌림) 존에서 지정가" → 오더블록 / FVG 리테스트
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Sequence

from crowcode.data import Candle, atr
from crowcode.structure import Swing, StructureEvent

Side = Literal["buy", "sell"]


@dataclass(frozen=True)
class LiquidityPool:
    kind: Literal["equal_highs", "equal_lows"]
    price: float
    indices: tuple[int, ...]

    @property
    def strength(self) -> int:
        return len(self.indices)


@dataclass(frozen=True)
class Sweep:
    index: int
    ts: datetime
    direction: Literal["above", "below"]  # 어느 쪽 유동성을 먹었는가
    level: float                          # 먹힌 레벨
    extreme: float                        # 스윕 봉의 극점 (SL 기준점)


@dataclass(frozen=True)
class OrderBlock:
    index: int
    ts: datetime
    side: Side          # 이 블록이 지지할 방향
    top: float
    bottom: float

    @property
    def mid(self) -> float:
        return (self.top + self.bottom) / 2.0

    def contains(self, price: float) -> bool:
        return self.bottom <= price <= self.top


@dataclass(frozen=True)
class FVG:
    index: int
    ts: datetime
    side: Side
    top: float
    bottom: float

    @property
    def mid(self) -> float:
        return (self.top + self.bottom) / 2.0


def liquidity_pools(
    swings: Sequence[Swing], tol: float, min_count: int = 2
) -> list[LiquidityPool]:
    """오차 `tol` 안에 모여 있는 스윙 고점/저점을 유동성 풀로 묶는다."""
    out: list[LiquidityPool] = []
    for kind, sk in (("equal_highs", "high"), ("equal_lows", "low")):
        pts = sorted([s for s in swings if s.kind == sk], key=lambda s: s.price)
        cluster: list[Swing] = []
        for s in pts:
            if cluster and abs(s.price - cluster[0].price) <= tol:
                cluster.append(s)
                continue
            if len(cluster) >= min_count:
                out.append(_pool(kind, cluster))
            cluster = [s]
        if len(cluster) >= min_count:
            out.append(_pool(kind, cluster))
    return out


def _pool(kind: str, cluster: Sequence[Swing]) -> LiquidityPool:
    price = sum(s.price for s in cluster) / len(cluster)
    return LiquidityPool(kind, price, tuple(sorted(s.index for s in cluster)))  # type: ignore[arg-type]


def find_sweeps(
    candles: Sequence[Candle],
    swings: Sequence[Swing],
    lookback: int = 60,
    min_wick_ratio: float = 0.5,
    start: int | None = None,
) -> list[Sweep]:
    """스윕 = 직전 스윙 레벨을 꼬리로 뚫고 종가는 되돌아온 봉.

    min_wick_ratio: 침투가 '거부'로 인정되려면 되돌림 꼬리가 봉 전체 범위의
                    이 비율 이상이어야 한다 (단순 돌파와 구분).
    """
    out: list[Sweep] = []
    n = len(candles)
    ordered = sorted(swings, key=lambda s: s.index)
    for i in range(max(1, start or 1), n):
        c = candles[i]
        if c.range <= 0:
            continue
        lo = max(0, i - lookback)
        prior = [s for s in ordered if lo <= s.index < i and s.confirmed_at <= i - 1]

        highs = [s for s in prior if s.kind == "high"]
        if highs:
            lvl = max(s.price for s in highs)
            if c.high > lvl and c.close < lvl:
                if (c.high - max(c.open, c.close)) / c.range >= min_wick_ratio * 0.5:
                    out.append(Sweep(i, c.ts, "above", lvl, c.high))

        lows = [s for s in prior if s.kind == "low"]
        if lows:
            lvl = min(s.price for s in lows)
            if c.low < lvl and c.close > lvl:
                if (min(c.open, c.close) - c.low) / c.range >= min_wick_ratio * 0.5:
                    out.append(Sweep(i, c.ts, "below", lvl, c.low))
    return out


def last_sweep(sweeps: Sequence[Sweep], side: Side, now_index: int, within: int) -> Sweep | None:
    """매수는 '아래쪽 유동성 스윕', 매도는 '위쪽 유동성 스윕' 이후에만 유효."""
    want = "below" if side == "buy" else "above"
    for s in reversed(sweeps):
        if s.index > now_index:
            continue
        if now_index - s.index > within:
            return None
        if s.direction == want:
            return s
    return None


def order_blocks(
    candles: Sequence[Candle], events: Sequence[StructureEvent], max_lookback: int = 30
) -> list[OrderBlock]:
    """구조 돌파(BOS/CHOCH)를 만든 임펄스 직전의 반대 색 캔들 = 오더블록."""
    out: list[OrderBlock] = []
    for ev in events:
        side: Side = "buy" if ev.direction == "bullish" else "sell"
        start = max(0, ev.index - max_lookback)
        found = None
        for j in range(ev.index - 1, start - 1, -1):
            c = candles[j]
            if side == "buy" and c.bearish:
                found = c
                idx = j
                break
            if side == "sell" and c.bullish:
                found = c
                idx = j
                break
        if found is None:
            continue
        out.append(OrderBlock(idx, found.ts, side, found.high, found.low))
    return out


def fair_value_gaps(candles: Sequence[Candle], min_size: float = 0.0) -> list[FVG]:
    """3봉 FVG(불균형): low[i] > high[i-2] (상승) / high[i] < low[i-2] (하락)."""
    out: list[FVG] = []
    for i in range(2, len(candles)):
        a, c = candles[i - 2], candles[i]
        if c.low - a.high > min_size:
            out.append(FVG(i, c.ts, "buy", c.low, a.high))
        elif a.low - c.high > min_size:
            out.append(FVG(i, c.ts, "sell", a.low, c.high))
    return out


@dataclass(frozen=True)
class POI:
    """진입 지정가를 걸 관심 구역."""
    kind: str
    side: Side
    top: float
    bottom: float
    index: int

    @property
    def mid(self) -> float:
        return (self.top + self.bottom) / 2.0

    @property
    def entry(self) -> float:
        return self.mid

    def entry_price(self, mode: str = "proximal") -> float:
        """존 안에서 지정가를 걸 지점.

        매수 존은 아래에 있으므로 proximal = top, distal = bottom.
        매도 존은 위에 있으므로 proximal = bottom, distal = top.
        """
        if mode == "mid":
            return self.mid
        if self.side == "buy":
            return self.top if mode == "proximal" else self.bottom
        return self.bottom if mode == "proximal" else self.top

    def split_entries(self, n: int, mode: str = "proximal") -> list[float]:
        """분할 진입 가격 (채널: 한 셋업에 최대 2번)."""
        if n <= 1:
            return [self.entry_price(mode)]
        near, far = self.entry_price("proximal"), self.entry_price("distal")
        return [near + (far - near) * k / (n - 1) for k in range(n)]


def collect_pois(
    candles: Sequence[Candle],
    events: Sequence[StructureEvent],
    side: Side,
    kinds: Sequence[str] = ("order_block", "fvg"),
    now_index: int | None = None,
    max_age: int = 200,
    max_candidates: int = 12,
) -> list[POI]:
    """방향에 맞고 아직 가격이 되돌아오지 않은(미소진) POI 를 최신순으로 반환."""
    now = len(candles) - 1 if now_index is None else now_index
    pois: list[POI] = []

    if "order_block" in kinds:
        for ob in order_blocks(candles, events[-25:]):
            if ob.side == side and now - ob.index <= max_age:
                pois.append(POI("order_block", side, ob.top, ob.bottom, ob.index))
    if "fvg" in kinds:
        atr_v = atr(candles[-200:] if len(candles) > 200 else candles)
        for g in fair_value_gaps(candles, min_size=atr_v * 0.1):
            if g.side == side and now - g.index <= max_age:
                pois.append(POI("fvg", side, g.top, g.bottom, g.index))

    pois.sort(key=lambda p: p.index, reverse=True)
    return [p for p in pois[:max_candidates] if not invalidated(candles, p, now)]


def invalidated(candles: Sequence[Candle], poi: POI, now: int) -> bool:
    """존을 '관통해서 종가 마감'하면 무효. 단순 리테스트(터치)는 무효가 아니다.

    채널에서 말하는 "SL 이 깨지면 구조가 바뀐 것" 과 같은 기준이다.
    """
    for k in range(poi.index + 1, now + 1):
        c = candles[k]
        if poi.side == "buy" and c.close < poi.bottom:
            return True
        if poi.side == "sell" and c.close > poi.top:
            return True
    return False


def tested(candles: Sequence[Candle], poi: POI, now: int) -> int:
    """POI 가 몇 번 리테스트됐는지 (많이 테스트된 존은 힘이 약하다)."""
    n = 0
    for k in range(poi.index + 1, now + 1):
        c = candles[k]
        if poi.side == "buy" and c.low <= poi.top:
            n += 1
        elif poi.side == "sell" and c.high >= poi.bottom:
            n += 1
    return n
