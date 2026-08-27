"""ICT 셋업 백테스터.

한 봉 안에서 손절·목표가 모두 닿으면 항상 **손절 우선**으로 처리한다.
낙관 편향을 없애기 위한 것이고, 실제로도 손절이 먼저 닿았을 확률이 높다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Sequence

from crowcode.data import Candle
from ict.models import Config, Setup, scan


@dataclass
class Trade:
    setup: Setup
    exit_index: int
    exit_price: float
    exit_ts: datetime
    outcome: str                  # target / stop / open
    r: float

    @property
    def won(self) -> bool:
        return self.r > 0


@dataclass
class Result:
    trades: list[Trade]
    setups: int
    spread: float

    @property
    def n(self) -> int:
        return len(self.trades)

    @property
    def wins(self) -> int:
        return sum(1 for t in self.trades if t.won)

    @property
    def win_rate(self) -> float:
        return self.wins / self.n if self.n else 0.0

    @property
    def total_r(self) -> float:
        return sum(t.r for t in self.trades)

    @property
    def expectancy(self) -> float:
        return self.total_r / self.n if self.n else 0.0

    @property
    def max_dd_r(self) -> float:
        peak = run = dd = 0.0
        for t in self.trades:
            run += t.r
            peak = max(peak, run)
            dd = max(dd, peak - run)
        return dd

    def report(self, title: str = "ICT 2022 모델") -> str:
        lines = [
            "=" * 62,
            f" {title}",
            "=" * 62,
            f" 셋업 / 체결   : {self.setups} / {self.n}",
            f" 승 / 패        : {self.wins} / {self.n - self.wins}   "
            f"(승률 {self.win_rate:.1%})",
            f" 총 R           : {self.total_r:+.1f}R",
            f" 기대값         : {self.expectancy:+.3f}R / 거래",
            f" 최대 낙폭      : {self.max_dd_r:.1f}R",
            f" 스프레드 가정  : {self.spread:.2f}",
        ]
        if self.n:
            from collections import Counter
            for name, counter in (("킬존", Counter(t.setup.killzone for t in self.trades)),
                                  ("방향", Counter(t.setup.side for t in self.trades)),
                                  ("진입근거", Counter(t.setup.array.kind for t in self.trades))):
                lines.append("-" * 62)
                lines.append(f" {name}별")
                for k, c in counter.most_common():
                    sub = [t for t in self.trades if _key(t, name) == k]
                    wr = sum(1 for t in sub if t.won) / len(sub)
                    tr = sum(t.r for t in sub)
                    lines.append(f"   {k:<18} {c:>4}건  승률 {wr:>5.1%}  {tr:+7.1f}R")
        lines.append("=" * 62)
        return "\n".join(lines)


def _key(t: Trade, name: str) -> str:
    return {"킬존": t.setup.killzone, "방향": t.setup.side,
            "진입근거": t.setup.array.kind}[name]


def run(candles: Sequence[Candle], cfg: Config = Config(),
        spread: float = 0.25, max_hold: int = 288, start: int = 200) -> Result:
    """셋업을 훑고, 지정가 체결 → 손절/목표까지 추적한다.

    max_hold: 이 봉 수를 넘기면 청산 (기본 288 = M5 하루).
    """
    candles = list(candles)
    setups = scan(candles, cfg, start=start)
    trades: list[Trade] = []

    for s in setups:
        fill = None
        for i in range(s.index + 1, min(s.index + 1 + max_hold, len(candles))):
            c = candles[i]
            if fill is None:
                touched = (s.side == "buy" and c.low <= s.entry) or \
                          (s.side == "sell" and c.high >= s.entry)
                if not touched:
                    continue
                fill = s.entry + spread if s.side == "buy" else s.entry - spread
                risk = abs(fill - s.stop)
                if risk <= 0:
                    break
                continue

            risk = abs(fill - s.stop)
            hit_stop = c.low <= s.stop if s.side == "buy" else c.high >= s.stop
            hit_tp = c.high >= s.target if s.side == "buy" else c.low <= s.target
            if hit_stop:                                  # 보수적으로 손절 우선
                r = (s.stop - fill) / risk if s.side == "buy" else (fill - s.stop) / risk
                trades.append(Trade(s, i, s.stop, c.ts, "stop", r))
                break
            if hit_tp:
                r = (s.target - fill) / risk if s.side == "buy" else (fill - s.target) / risk
                trades.append(Trade(s, i, s.target, c.ts, "target", r))
                break
        else:
            if fill is not None:
                last = candles[min(s.index + max_hold, len(candles) - 1)]
                risk = abs(fill - s.stop)
                r = ((last.close - fill) if s.side == "buy" else (fill - last.close)) / risk
                trades.append(Trade(s, last_index(candles, last), last.close, last.ts, "open", r))

    return Result(trades, len(setups), spread)


def last_index(candles: Sequence[Candle], bar: Candle) -> int:
    for i in range(len(candles) - 1, -1, -1):
        if candles[i].ts == bar.ts:
            return i
    return len(candles) - 1
