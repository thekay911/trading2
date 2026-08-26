"""Wyckoff 수급 국면 판정.

채널은 금/EURUSD 분석에서 Wyckoff 를 방향 필터로 쓴다.
  "스프링 나왔고 재축적 국면이다 → 되돌림만 매도"
  "Phase A 라서 구 지지 구간에서 매수 지정가"

구현은 정통 Wyckoff 의 축약판이다.
  축적(Accumulation): SC → AR → ST → Spring → Test → SOS → LPS
  분산(Distribution): BC → AR → ST → UT/UTAD → SOW → LPSY

Phase 요약
  A: 기존 추세 정지 (SC/BC, AR, ST)
  B: 레인지 내 축적/분산 (반복 터치)
  C: 최종 흔들기 (Spring / Upthrust)
  D: 레인지 이탈 시도 (SOS / SOW, LPS / LPSY)
  E: 추세 진행
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

from crowcode.data import Candle, atr

Schematic = Literal["accumulation", "distribution", "undefined"]
Phase = Literal["A", "B", "C", "D", "E", "none"]


@dataclass(frozen=True)
class TradingRange:
    start: int
    end: int
    top: float
    bottom: float
    top_touches: int
    bottom_touches: int

    @property
    def width(self) -> float:
        return self.top - self.bottom

    @property
    def mid(self) -> float:
        return (self.top + self.bottom) / 2.0

    def position(self, price: float) -> float:
        """레인지 내 상대 위치 0(하단)~1(상단)."""
        if self.width <= 0:
            return 0.5
        return (price - self.bottom) / self.width


@dataclass(frozen=True)
class WyckoffView:
    schematic: Schematic
    phase: Phase
    range: TradingRange | None
    spring: int | None = None      # 스프링 봉 인덱스
    upthrust: int | None = None    # 업스러스트 봉 인덱스
    notes: tuple[str, ...] = ()

    @property
    def bias(self) -> Literal["buy", "sell"] | None:
        """채널식 'Buy only / Sell only' 결론."""
        if self.schematic == "accumulation" and self.phase in ("C", "D", "E"):
            return "buy"
        if self.schematic == "distribution" and self.phase in ("C", "D", "E"):
            return "sell"
        return None


def _quantile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    v = sorted(values)
    if len(v) == 1:
        return v[0]
    pos = q * (len(v) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(v) - 1)
    return v[lo] + (v[hi] - v[lo]) * (pos - lo)


def detect_range(
    candles: Sequence[Candle], lookback: int = 120, tol_atr: float = 0.5,
    min_touches: int = 2, max_width_atr: float = 12.0,
) -> TradingRange | None:
    """레인지 경계를 분위수로 잡는다.

    절대 최고/최저를 쓰면 스프링·업스러스트의 꼬리가 곧 경계가 되어
    정작 그 흔들기를 탐지할 수 없다. 그래서 상단은 95분위, 하단은 5분위로
    잡아 '몸통 레인지' 를 만들고, 그 밖으로 삐져나온 봉을 흔들기로 본다.
    """
    if len(candles) < 20:
        return None
    win = list(candles[-lookback:])
    start = len(candles) - len(win)
    a = atr(win)
    if a <= 0:
        return None
    top = _quantile([c.high for c in win], 0.95)
    bottom = _quantile([c.low for c in win], 0.05)
    if top <= bottom or (top - bottom) > max_width_atr * a:
        return None

    tol = tol_atr * a
    top_touches = sum(1 for c in win if c.high >= top - tol)
    bottom_touches = sum(1 for c in win if c.low <= bottom + tol)
    if top_touches < min_touches or bottom_touches < min_touches:
        return None
    return TradingRange(start, len(candles) - 1, top, bottom, top_touches, bottom_touches)


def find_spring(candles: Sequence[Candle], tr: TradingRange, max_pen_atr: float = 1.2) -> int | None:
    """레인지 하단을 뚫었다가 종가는 다시 안으로 들어온 봉 = Spring."""
    a = atr(list(candles[-200:]))
    if a <= 0:
        return None
    best = None
    for i in range(tr.start, tr.end + 1):
        c = candles[i]
        if c.low < tr.bottom and c.close > tr.bottom:
            if (tr.bottom - c.low) <= max_pen_atr * a:
                best = i
    return best


def find_upthrust(candles: Sequence[Candle], tr: TradingRange, max_pen_atr: float = 1.2) -> int | None:
    """레인지 상단을 뚫었다가 종가는 다시 안으로 들어온 봉 = Upthrust."""
    a = atr(list(candles[-200:]))
    if a <= 0:
        return None
    best = None
    for i in range(tr.start, tr.end + 1):
        c = candles[i]
        if c.high > tr.top and c.close < tr.top:
            if (c.high - tr.top) <= max_pen_atr * a:
                best = i
    return best


def analyze(
    candles: Sequence[Candle],
    lookback: int = 120,
    min_touches: int = 2,
    max_width_atr: float = 12.0,
    max_pen_atr: float = 1.2,
) -> WyckoffView:
    tr = detect_range(candles, lookback, min_touches=min_touches, max_width_atr=max_width_atr)
    if tr is None:
        return WyckoffView("undefined", "none", None, notes=("레인지 미형성 (추세 구간)",))

    spring = find_spring(candles, tr, max_pen_atr)
    upthrust = find_upthrust(candles, tr, max_pen_atr)
    last = candles[-1]
    pos = tr.position(last.close)
    notes: list[str] = []

    # 레인지 진입 이전 추세로 축적/분산을 가른다 (SC 하락 뒤 = 축적).
    pre = candles[max(0, tr.start - lookback):tr.start]
    schematic: Schematic = "undefined"
    if len(pre) >= 10:
        drift = pre[-1].close - pre[0].close
        if drift < 0:
            schematic = "accumulation"
            notes.append("레인지 직전 하락 → 축적 스키마")
        elif drift > 0:
            schematic = "distribution"
            notes.append("레인지 직전 상승 → 분산 스키마")

    if schematic == "undefined":
        # 폴백: 스프링/업스러스트 존재 여부로 판정
        if spring is not None and upthrust is None:
            schematic = "accumulation"
        elif upthrust is not None and spring is None:
            schematic = "distribution"

    a = atr(list(candles[-200:]))
    margin = a * 0.5
    phase: Phase = "B"
    if last.close > tr.top + margin:
        phase = "E" if schematic == "accumulation" else "D"
        notes.append("레인지 상단 이탈")
    elif last.close < tr.bottom - margin:
        phase = "E" if schematic == "distribution" else "D"
        notes.append("레인지 하단 이탈")
    elif schematic == "accumulation" and spring is not None and spring >= tr.end - lookback // 3:
        phase = "C" if pos < 0.5 else "D"
        notes.append(f"Spring 확인 (idx={spring})")
    elif schematic == "distribution" and upthrust is not None and upthrust >= tr.end - lookback // 3:
        phase = "C" if pos > 0.5 else "D"
        notes.append(f"Upthrust 확인 (idx={upthrust})")
    elif tr.top_touches + tr.bottom_touches <= 4:
        phase = "A"
        notes.append("AR/ST 형성 초기 (Phase A)")

    return WyckoffView(schematic, phase, tr, spring, upthrust, tuple(notes))
