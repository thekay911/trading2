"""시장 구조: 스윙 포인트, BOS(구조 돌파), CHOCH(성격 변화).

채널의 진입 근거 중 가장 자주 등장하는 것이 CHOCH 이다.
  "CHOCH 로 단기 하락 구조가 깨져서 스캘핑 매수"

정의
----
BOS   : 추세 방향으로 직전 스윙을 돌파 → 추세 지속
CHOCH : 추세 반대 방향으로 직전 스윙을 돌파 → 추세 전환 후보

모든 계산은 인과적(causal)이다. i 번째 봉을 처리할 때는
확정된(= i - swing_right 이전) 스윙만 사용한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Sequence

from crowcode.data import Candle

Direction = Literal["bullish", "bearish"]
SwingKind = Literal["high", "low"]


@dataclass(frozen=True)
class Swing:
    index: int
    ts: datetime
    price: float
    kind: SwingKind
    confirmed_at: int  # 이 인덱스 이후부터 사용 가능


@dataclass(frozen=True)
class StructureEvent:
    index: int
    ts: datetime
    kind: Literal["BOS", "CHOCH"]
    direction: Direction
    level: float          # 돌파된 스윙 가격
    close: float          # 돌파 봉 종가
    swing_index: int


def swing_points(
    candles: Sequence[Candle], left: int = 2, right: int = 2
) -> list[Swing]:
    """프랙탈 스윙 고점/저점.

    고점: high[i] 가 좌우 `left`/`right` 봉의 high 보다 크거나 같고,
          최소한 한쪽에는 엄격히 크다 (평평한 이중 고점 처리).
    """
    out: list[Swing] = []
    n = len(candles)
    for i in range(left, n - right):
        c = candles[i]
        lo_win = candles[i - left:i]
        hi_win = candles[i + 1:i + 1 + right]

        if all(c.high >= x.high for x in lo_win) and all(c.high >= x.high for x in hi_win) \
                and any(c.high > x.high for x in lo_win + hi_win):
            out.append(Swing(i, c.ts, c.high, "high", i + right))

        if all(c.low <= x.low for x in lo_win) and all(c.low <= x.low for x in hi_win) \
                and any(c.low < x.low for x in lo_win + hi_win):
            out.append(Swing(i, c.ts, c.low, "low", i + right))
    out.sort(key=lambda s: (s.confirmed_at, s.index))
    return out


@dataclass
class StructureState:
    """마지막 시점의 구조 상태."""
    bias: Direction | None = None
    last_high: Swing | None = None
    last_low: Swing | None = None
    events: list[StructureEvent] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.events is None:
            self.events = []

    @property
    def last_event(self) -> StructureEvent | None:
        return self.events[-1] if self.events else None

    def last_choch(self) -> StructureEvent | None:
        for e in reversed(self.events):
            if e.kind == "CHOCH":
                return e
        return None


def analyze_structure(
    candles: Sequence[Candle],
    left: int = 2,
    right: int = 2,
    break_on_close: bool = True,
) -> StructureState:
    """봉 단위로 전진하며 BOS/CHOCH 이벤트를 수집한다."""
    swings = swing_points(candles, left, right)
    by_confirm: dict[int, list[Swing]] = {}
    for s in swings:
        by_confirm.setdefault(s.confirmed_at, []).append(s)

    st = StructureState()
    for i, c in enumerate(candles):
        # 1) 이 봉에서 새로 확정된 스윙을 반영
        for s in by_confirm.get(i, []):
            if s.kind == "high":
                st.last_high = s
            else:
                st.last_low = s

        # 2) 확정된 스윙에 대한 돌파 판정
        ref_up = c.close if break_on_close else c.high
        ref_dn = c.close if break_on_close else c.low

        if st.last_high is not None and i > st.last_high.index and ref_up > st.last_high.price:
            kind = "CHOCH" if st.bias == "bearish" else "BOS"
            st.events.append(StructureEvent(i, c.ts, kind, "bullish",
                                            st.last_high.price, c.close, st.last_high.index))
            st.bias = "bullish"
            st.last_high = None  # 돌파된 고점은 소진

        elif st.last_low is not None and i > st.last_low.index and ref_dn < st.last_low.price:
            kind = "CHOCH" if st.bias == "bullish" else "BOS"
            st.events.append(StructureEvent(i, c.ts, kind, "bearish",
                                            st.last_low.price, c.close, st.last_low.index))
            st.bias = "bearish"
            st.last_low = None

    return st


def htf_bias(candles: Sequence[Candle], left: int = 2, right: int = 2) -> Direction | None:
    """상위 타임프레임 편향. 채널식으로 말하면 'Buy only / Sell only' 결정."""
    return analyze_structure(candles, left, right).bias


def recent_choch(state: StructureState, within_bars: int, now_index: int) -> StructureEvent | None:
    """최근 `within_bars` 안에서 발생한 CHOCH 만 유효 트리거로 인정."""
    ev = state.last_choch()
    if ev is None:
        return None
    return ev if now_index - ev.index <= within_bars else None
