"""시장 구조 — ICT 방식.

일반적인 구조 매매와 다른 점이 하나 있고, 그게 전부다.

  **MSS 는 변위(displacement)를 동반해야 한다.**

스윙을 하나 깼다고 다 같은 게 아니다. 알고리즘이 진짜로 방향을 바꿀 때는
가격이 '효율적으로' 움직이지 못하고 FVG(불균형)를 남긴다. 갭 하나 남기지
못하고 살살 넘어간 돌파는 유동성만 건드린 것이지 전환이 아니다.

  BOS  기존 방향으로 스윙 돌파 → 추세 지속
  MSS  반대 방향으로 스윙 돌파 + 변위 → 전환 (여기서만 진입 준비)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Sequence

from crowcode.data import Candle, atr

Dir = Literal[1, -1]          # 1 = 상승(bullish), -1 = 하락(bearish)
BULL: Dir = 1
BEAR: Dir = -1


@dataclass(frozen=True)
class Swing:
    index: int
    ts: datetime
    price: float
    is_high: bool
    confirmed_at: int          # 이 인덱스부터 사용 가능 (룩어헤드 차단)


@dataclass(frozen=True)
class Displacement:
    """변위 — 불균형을 남기며 한 방향으로 강하게 나간 구간."""
    start: int
    end: int
    direction: Dir
    size: float                # 가격 폭
    atr_multiple: float
    gap_low: float             # 남긴 FVG
    gap_high: float

    @property
    def has_gap(self) -> bool:
        return self.gap_high > self.gap_low


@dataclass(frozen=True)
class StructureEvent:
    index: int
    ts: datetime
    kind: Literal["BOS", "MSS"]
    direction: Dir
    level: float               # 깨진 스윙 가격
    swing_index: int
    displacement: Displacement | None = None

    @property
    def valid_mss(self) -> bool:
        """변위 없는 MSS 는 ICT 기준으로 전환이 아니다."""
        return self.kind == "MSS" and self.displacement is not None


def swings(candles: Sequence[Candle], left: int = 1, right: int = 1) -> list[Swing]:
    """스윙 고·저. ICT 기본형은 3캔들(좌1 우1)이다.

    좌우 봉보다 높고(낮고), 최소 한쪽과는 엄격히 차이가 나야 한다.
    확정은 `right` 봉 뒤에 이뤄지므로 그 전에는 쓸 수 없다.
    """
    out: list[Swing] = []
    bars = list(candles)          # Series 슬라이스는 리스트가 아니다
    n = len(bars)
    for i in range(left, n - right):
        c = bars[i]
        side = bars[i - left:i] + bars[i + 1:i + 1 + right]

        if all(c.high >= x.high for x in side) and any(c.high > x.high for x in side):
            out.append(Swing(i, c.ts, c.high, True, i + right))
        if all(c.low <= x.low for x in side) and any(c.low < x.low for x in side):
            out.append(Swing(i, c.ts, c.low, False, i + right))
    out.sort(key=lambda s: (s.confirmed_at, s.index))
    return out


def find_displacement(
    candles: Sequence[Candle],
    end_index: int,
    direction: Dir,
    lookback: int = 5,
    min_atr: float = 1.0,
    atr_period: int = 20,
) -> Displacement | None:
    """`end_index` 로 끝나는 변위를 찾는다.

    조건 두 가지를 모두 만족해야 한다.
      1) 구간 이동폭이 ATR 의 `min_atr` 배 이상   — 에너지
      2) 그 구간에 FVG 가 남아 있다               — 비효율
    """
    if end_index < 2:
        return None
    a = atr(candles[max(0, end_index - 60):end_index + 1], atr_period)
    if a <= 0:
        return None

    best: Displacement | None = None
    for start in range(max(0, end_index - lookback), end_index):
        seg = candles[start:end_index + 1]
        if len(seg) < 3:
            continue
        if direction == BULL:
            size = max(c.high for c in seg) - min(c.low for c in seg)
        else:
            size = max(c.high for c in seg) - min(c.low for c in seg)
        if size < min_atr * a:
            continue

        gap_lo, gap_hi = _largest_gap(candles, start, end_index, direction)
        if gap_hi <= gap_lo:
            continue
        d = Displacement(start, end_index, direction, size, size / a, gap_lo, gap_hi)
        if best is None or d.atr_multiple > best.atr_multiple:
            best = d
    return best


def _largest_gap(candles: Sequence[Candle], start: int, end: int, direction: Dir):
    """구간 안에서 가장 큰 3봉 FVG. 없으면 (0, 0)."""
    lo = hi = 0.0
    best = 0.0
    for i in range(max(start + 2, 2), end + 1):
        a, c = candles[i - 2], candles[i]
        if direction == BULL and c.low > a.high:
            span = c.low - a.high
            if span > best:
                best, lo, hi = span, a.high, c.low
        elif direction == BEAR and c.high < a.low:
            span = a.low - c.high
            if span > best:
                best, lo, hi = span, c.high, a.low
    return lo, hi


@dataclass
class StructureState:
    bias: Dir | None = None
    events: list[StructureEvent] = None       # type: ignore[assignment]

    def __post_init__(self):
        if self.events is None:
            self.events = []

    def last(self, kind: str | None = None, direction: Dir | None = None) -> StructureEvent | None:
        for e in reversed(self.events):
            if kind and e.kind != kind:
                continue
            if direction is not None and e.direction != direction:
                continue
            return e
        return None

    def last_mss(self, valid_only: bool = True) -> StructureEvent | None:
        for e in reversed(self.events):
            if e.kind == "MSS" and (not valid_only or e.valid_mss):
                return e
        return None


def analyze(
    candles: Sequence[Candle],
    left: int = 1,
    right: int = 1,
    require_displacement: bool = True,
    min_displacement_atr: float = 1.0,
) -> StructureState:
    """봉 단위로 전진하며 BOS / MSS 를 수집한다 (인과적)."""
    sw = swings(candles, left, right)
    by_confirm: dict[int, list[Swing]] = {}
    for s in sw:
        by_confirm.setdefault(s.confirmed_at, []).append(s)

    st = StructureState()
    last_high: Swing | None = None
    last_low: Swing | None = None

    for i, c in enumerate(candles):
        for s in by_confirm.get(i, []):
            if s.is_high:
                last_high = s
            else:
                last_low = s

        broke_up = last_high is not None and i > last_high.index and c.close > last_high.price
        broke_dn = last_low is not None and i > last_low.index and c.close < last_low.price

        if broke_up:
            kind = "MSS" if st.bias == BEAR else "BOS"
            disp = None
            if kind == "MSS" and require_displacement:
                disp = find_displacement(candles, i, BULL, min_atr=min_displacement_atr)
                if disp is None:
                    # 변위 없는 돌파는 유동성만 건드린 것 — 전환으로 치지 않는다
                    last_high = None
                    continue
            elif kind == "MSS":
                disp = find_displacement(candles, i, BULL, min_atr=min_displacement_atr)
            st.events.append(StructureEvent(i, c.ts, kind, BULL, last_high.price,
                                            last_high.index, disp))
            st.bias = BULL
            last_high = None

        elif broke_dn:
            kind = "MSS" if st.bias == BULL else "BOS"
            disp = None
            if kind == "MSS" and require_displacement:
                disp = find_displacement(candles, i, BEAR, min_atr=min_displacement_atr)
                if disp is None:
                    last_low = None
                    continue
            elif kind == "MSS":
                disp = find_displacement(candles, i, BEAR, min_atr=min_displacement_atr)
            st.events.append(StructureEvent(i, c.ts, kind, BEAR, last_low.price,
                                            last_low.index, disp))
            st.bias = BEAR
            last_low = None

    return st
