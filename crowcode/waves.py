"""엘리엇 파동 보조 판정 (임펄스 5파 / ABC 조정).

채널은 "파동 4가 1946 에서 끝났고 이번 주 5파" 처럼 파동 카운트를
목표가와 진입 타이밍에 쓴다. 여기서는 스윙 지그재그를 만들고
엄격한 엘리엇 규칙 3가지만 검증하는 가벼운 카운터를 제공한다.

규칙
  1) 2파는 1파의 시작점을 넘지 못한다.
  2) 3파는 1파와 5파 중 가장 짧을 수 없다.
  3) 4파는 1파의 종점과 겹치지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

from crowcode.data import Candle
from crowcode.structure import Swing


@dataclass(frozen=True)
class Leg:
    start_index: int
    end_index: int
    start_price: float
    end_price: float

    @property
    def size(self) -> float:
        return abs(self.end_price - self.start_price)

    @property
    def up(self) -> bool:
        return self.end_price > self.start_price


@dataclass(frozen=True)
class WaveCount:
    legs: tuple[Leg, ...]
    labels: tuple[str, ...]
    pattern: Literal["impulse", "correction", "unknown"]
    valid: bool
    direction: Literal["bullish", "bearish", "none"]

    @property
    def current_wave(self) -> str | None:
        return self.labels[-1] if self.labels else None


def zigzag(swings: Sequence[Swing]) -> list[Leg]:
    """스윙 고/저를 교대로 이어 지그재그 레그를 만든다."""
    ordered = sorted(swings, key=lambda s: s.index)
    picked: list[Swing] = []
    for s in ordered:
        if not picked:
            picked.append(s)
            continue
        prev = picked[-1]
        if s.kind == prev.kind:
            # 같은 종류가 연속되면 더 극단적인 값으로 교체
            if (s.kind == "high" and s.price > prev.price) or (s.kind == "low" and s.price < prev.price):
                picked[-1] = s
        else:
            picked.append(s)
    return [
        Leg(a.index, b.index, a.price, b.price)
        for a, b in zip(picked, picked[1:])
    ]


def count(swings: Sequence[Swing], max_legs: int = 5) -> WaveCount:
    legs = zigzag(swings)
    if len(legs) < 3:
        return WaveCount(tuple(legs), (), "unknown", False, "none")

    tail = legs[-max_legs:]
    if len(tail) >= 5:
        w1, w2, w3, w4, w5 = tail[-5:]
        direction = "bullish" if w1.up else "bearish"
        ok = _rule_2(w1, w2) and _rule_3(w1, w3, w5) and _rule_4(w1, w4)
        return WaveCount(tuple(tail), ("1", "2", "3", "4", "5"), "impulse", ok, direction)

    a, b, c = tail[-3:]
    direction = "bearish" if a.up else "bullish"  # 조정이 끝나면 반대 방향
    ok = c.size >= 0.5 * a.size
    return WaveCount(tuple(tail), ("A", "B", "C"), "correction", ok, direction)


def _rule_2(w1: Leg, w2: Leg) -> bool:
    if w1.up:
        return w2.end_price > w1.start_price
    return w2.end_price < w1.start_price


def _rule_3(w1: Leg, w3: Leg, w5: Leg) -> bool:
    return not (w3.size < w1.size and w3.size < w5.size)


def _rule_4(w1: Leg, w4: Leg) -> bool:
    if w1.up:
        return w4.end_price > w1.end_price
    return w4.end_price < w1.end_price


def correction_complete(wc: WaveCount) -> bool:
    """ABC 조정 완료 → 채널이 말하는 '조정 끝나면 본 방향으로'."""
    return wc.pattern == "correction" and wc.valid and wc.current_wave == "C"
