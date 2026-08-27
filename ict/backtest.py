"""ICT 셋업 백테스터.

한 봉 안에서 손절·목표가 모두 닿으면 항상 **손절 우선**으로 처리한다.
낙관 편향을 없애기 위한 것이고, 실제로도 손절이 먼저 닿았을 확률이 높다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Sequence

from crowcode.data import Candle
from ict.engine import Market
from ict.gold import STANDARD, GoldProfile
from ict.models import Config, Setup
from ict.plays import ACTIVE, PLAYS, Play
from ict.strategy import scan


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
            for name, counter in (("모델", Counter(t.setup.model for t in self.trades)),
                                  ("킬존", Counter(t.setup.killzone for t in self.trades)),
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
    return {"모델": t.setup.model, "킬존": t.setup.killzone, "방향": t.setup.side,
            "진입근거": t.setup.array.kind}[name]


def run(candles: Sequence[Candle], cfg: Config = Config(),
        spread: float | None = None, max_hold: int = 288, start: int = 200,
        setups: Sequence[Setup] | None = None,
        gold: GoldProfile = STANDARD,
        models: Sequence[str] | None = None,
        use_plays: bool = True) -> Result:
    """셋업을 훑고, 지정가 체결 → 손절/목표까지 추적한다.

    한 봉 안에서 손절과 목표가 모두 닿으면 **손절 우선**이다.

    use_plays: 모델별 청산 계획(ict.plays)을 적용한다. 목표 R, 보유한도,
               본전 이동이 모델마다 달라진다. False 면 셋업이 들고 있는
               유동성 목표를 그대로 쓰고 max_hold 를 전부에 적용한다.
    max_hold:  계획이 없는 모델의 기본 보유한도 (288 = M5 하루).
    setups:    미리 뽑아 둔 셋업 (ict.strategy.scan 결과). 없으면 여기서 훑는다.
    gold:      XAUUSD 보정 프로파일 (setups 를 직접 넘기면 무시된다).
    models:    쓸 모델 이름들. None 이면 기본 실행 목록(ict.plays.ACTIVE).
    """
    candles = list(candles)
    # 보유 한도는 봉 수가 아니라 시간이다. 30분봉 데이터에서 M5 봉 수를
    # 그대로 쓰면 6배를 들고 있게 된다.
    bar_min = bar_minutes(candles)
    fixed_spread = spread
    if setups is None:
        setups = scan(Market.build(candles, gold=gold), cfg,
                      models=list(models) if models else list(ACTIVE), start=start)
    trades: list[Trade] = []

    for s in setups:
        pl: Play | None = PLAYS.get(s.model) if use_plays else None
        hold = pl.bars_to_hold(bar_min) if pl else max_hold
        risk0 = s.risk
        if risk0 <= 0:
            continue
        # 계획이 있으면 목표는 그 R 이다. 셋업이 들고 있는 유동성 목표로
        # 자르지 않는다 — 21년 실측에서 목표를 1~1.5R 로 짧게 자르면 전
        # 모델이 음수가 됐다. 유동성 풀은 방향의 근거지 익절 지점이 아니다.
        target = s.target
        if pl:
            target = (s.entry + risk0 * pl.target_rr if s.side == "buy"
                      else s.entry - risk0 * pl.target_rr)

        fill = None
        stop = s.stop
        moved = False
        for i in range(s.index + 1, min(s.index + 1 + hold, len(candles))):
            c = candles[i]
            if fill is None:
                touched = (s.side == "buy" and c.low <= s.entry) or \
                          (s.side == "sell" and c.high >= s.entry)
                if not touched:
                    continue
                sp = fixed_spread if fixed_spread is not None else gold.spread_at(s.entry)
                fill = s.entry + sp if s.side == "buy" else s.entry - sp
                if abs(fill - stop) <= 0:
                    break
                continue

            # 본전 이동은 손절 판정보다 먼저. 같은 봉에서 목표에 닿았다면
            # 이미 그 전에 be_at 을 지났다는 뜻이므로 순서가 맞다.
            if pl and pl.be_at > 0 and not moved:
                prog = ((c.high - fill) if s.side == "buy" else (fill - c.low)) / risk0
                if prog >= pl.be_at:
                    stop = fill
                    moved = True

            hit_stop = c.low <= stop if s.side == "buy" else c.high >= stop
            hit_tp = c.high >= target if s.side == "buy" else c.low <= target
            if hit_stop:                                  # 보수적으로 손절 우선
                r = ((stop - fill) if s.side == "buy" else (fill - stop)) / risk0
                trades.append(Trade(s, i, stop, c.ts,
                                    "breakeven" if moved else "stop", r))
                break
            if hit_tp:
                r = ((target - fill) if s.side == "buy" else (fill - target)) / risk0
                trades.append(Trade(s, i, target, c.ts, "target", r))
                break
        else:
            if fill is not None:
                last = candles[min(s.index + hold, len(candles) - 1)]
                r = ((last.close - fill) if s.side == "buy" else (fill - last.close)) / risk0
                trades.append(Trade(s, last_index(candles, last), last.close, last.ts,
                                    "open", r))

    return Result(trades, len(setups),
                  fixed_spread if fixed_spread is not None else gold.spread)


def bar_minutes(candles: Sequence[Candle]) -> float:
    """시계열의 봉 간격(분). 휴장 구멍에 안 흔들리게 중앙값을 쓴다."""
    if len(candles) < 3:
        return 5.0
    gaps = sorted((candles[i + 1].ts - candles[i].ts).total_seconds() / 60.0
                  for i in range(min(len(candles) - 1, 500)))
    m = gaps[len(gaps) // 2]
    return m if m > 0 else 5.0


def last_index(candles: Sequence[Candle], bar: Candle) -> int:
    for i in range(len(candles) - 1, -1, -1):
        if candles[i].ts == bar.ts:
            return i
    return len(candles) - 1
