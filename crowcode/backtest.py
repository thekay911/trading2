"""이벤트 기반 백테스터.

채널 규칙을 그대로 시뮬레이션한다.
  · 지정가 주문 + 미체결 만료
  · 2R 본절 이동, 3R 분할 청산
  · 손절은 뒤로 밀지 않음
  · 연속 손절 / 일일 한도 도달 시 그날 매매 중단
  · 스프레드 반영 ("M1 은 스프레드에 죽는다" 를 수치로 확인)
  · 스왑(보유 비용) 반영 — 금은 스왑이 크게 마이너스라 스윙 성과를 좌우한다

같은 봉 안에서 SL·TP 가 모두 닿으면 항상 SL 을 먼저 체결한 것으로 본다
(낙관적 결과 방지).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Sequence

from crowcode.config import CrowConfig, DEFAULT
from crowcode.data import Series, tf_minutes
from crowcode.risk import ManagedPosition, RiskState
from crowcode.sessions import NewsEvent
from crowcode.signals import Side, Signal
from crowcode.strategy import CrowStrategy


@dataclass
class Trade:
    signal: Signal
    opened_at: datetime
    closed_at: datetime | None = None
    exit_price: float | None = None
    pnl: float = 0.0
    r_multiple: float = 0.0
    outcome: str = "open"        # tp / sl / breakeven / partial+tp / expired
    notes: list[str] = field(default_factory=list)


@dataclass
class PendingOrder:
    signal: Signal
    placed_index: int


@dataclass
class BacktestResult:
    trades: list[Trade]
    equity: list[tuple[datetime, float]]
    start_balance: float
    end_balance: float
    rejections: dict[str, int]

    @property
    def n(self) -> int:
        return len(self.trades)

    @property
    def wins(self) -> int:
        return sum(1 for t in self.trades if t.pnl > 0)

    @property
    def losses(self) -> int:
        return sum(1 for t in self.trades if t.pnl < 0)

    @property
    def win_rate(self) -> float:
        return self.wins / self.n if self.n else 0.0

    @property
    def total_r(self) -> float:
        return sum(t.r_multiple for t in self.trades)

    @property
    def expectancy_r(self) -> float:
        return self.total_r / self.n if self.n else 0.0

    @property
    def return_pct(self) -> float:
        if self.start_balance <= 0:
            return 0.0
        return (self.end_balance / self.start_balance - 1.0) * 100.0

    @property
    def max_drawdown_pct(self) -> float:
        peak = self.start_balance
        dd = 0.0
        for _, eq in self.equity:
            peak = max(peak, eq)
            if peak > 0:
                dd = max(dd, (peak - eq) / peak * 100.0)
        return dd

    def report(self) -> str:
        return "\n".join([
            "=" * 56,
            " Crow Concept 백테스트 결과",
            "=" * 56,
            f" 거래 수        : {self.n}",
            f" 승 / 패        : {self.wins} / {self.losses}",
            f" 승률           : {self.win_rate:.1%}",
            f" 총 R           : {self.total_r:+.2f}R",
            f" 기대값         : {self.expectancy_r:+.3f}R / 거래",
            f" 잔고           : {self.start_balance:,.2f} → {self.end_balance:,.2f}",
            f" 수익률         : {self.return_pct:+.2f}%",
            f" 최대 낙폭      : {self.max_drawdown_pct:.2f}%",
            "-" * 56,
            " 필터별 기각 횟수:",
            *[f"   {k:<14} {v}" for k, v in self.rejections.items()],
            "=" * 56,
        ])


class Backtester:
    def __init__(
        self,
        cfg: CrowConfig = DEFAULT,
        balance: float = 1000.0,
        symbol: str = "XAUUSD",
        spread: float = 0.20,
        swap_per_lot_night: float = 0.0,   # 금 스왑은 보통 음수 (보유 비용)
        news: Sequence[NewsEvent] = (),
        warmup: int = 400,
        eval_every: int = 1,
    ):
        self.cfg = cfg
        self.start_balance = balance
        self.symbol = symbol
        self.spread = spread
        self.swap_per_lot_night = swap_per_lot_night
        self.news = list(news)
        self.warmup = warmup
        self.eval_every = max(1, eval_every)

    def run(self, base: Series) -> BacktestResult:
        cfg = self.cfg
        strat = CrowStrategy(cfg, self.symbol, self.news)
        risk = RiskState(balance=self.start_balance)

        view = strat.view(base)

        trades: list[Trade] = []
        equity: list[tuple[datetime, float]] = []
        pending: PendingOrder | None = None
        pos: ManagedPosition | None = None
        cur: Trade | None = None

        candles = list(base)
        base_min = tf_minutes(base.timeframe) if base.timeframe else 1
        # 만료는 LTF 봉 기준이므로 베이스 봉 수로 환산한다.
        expiry_scale = max(1, tf_minutes(cfg.ltf) // base_min)

        for i in range(self.warmup, len(candles)):
            c = candles[i]

            # 1) 열린 포지션 관리
            if pos is not None and cur is not None:
                closed = self._manage(pos, cur, c, risk)
                if closed:
                    trades.append(cur)
                    pos, cur = None, None

            # 2) 미체결 지정가 처리
            if pos is None and pending is not None:
                if i - pending.placed_index > pending.signal.expiry_bars * expiry_scale:
                    pending = None
                else:
                    fill = self._try_fill(pending.signal, c)
                    if fill is not None:
                        s = pending.signal
                        pos = ManagedPosition(
                            side=s.side, entry=fill, sl=s.sl, tp=s.tp, lots=s.lots,
                            initial_risk=abs(fill - s.sl), opened_at=c.ts,
                        )
                        cur = Trade(signal=s, opened_at=c.ts)
                        risk.register_open(c.ts)
                        pending = None

            # 3) 신규 시그널 탐색
            if pos is None and pending is None and (i % self.eval_every == 0):
                sig = strat.evaluate(view, risk.balance, risk, now_ts=c.ts)
                if sig is not None and self._spread_too_costly(sig):
                    sig = None
                if sig is not None:
                    if sig.order_type == "market":
                        entry = c.close + (self.spread if sig.side == "buy" else -self.spread)
                        pos = ManagedPosition(sig.side, entry, sig.sl, sig.tp, sig.lots,
                                              abs(entry - sig.sl), c.ts)
                        cur = Trade(signal=sig, opened_at=c.ts)
                        risk.register_open(c.ts)
                    else:
                        pending = PendingOrder(sig, i)

            equity.append((c.ts, risk.balance))

        return BacktestResult(trades, equity, self.start_balance, risk.balance,
                              strat.rejection_summary())

    # ------------------------------------------------------------------
    def _spread_too_costly(self, sig: Signal) -> bool:
        """스프레드가 손절폭 대비 과하면 그 셋업은 애초에 기댓값이 없다."""
        ratio = self.cfg.max_spread_ratio
        if ratio <= 0 or self.spread <= 0:
            return False
        return self.spread > sig.risk_per_unit * ratio

    def _try_fill(self, s: Signal, c) -> float | None:
        """지정가 체결. 매수는 ask, 매도는 bid 기준으로 스프레드를 얹는다."""
        if s.side == "buy" and c.low <= s.entry:
            return s.entry + self.spread
        if s.side == "sell" and c.high >= s.entry:
            return s.entry - self.spread
        return None

    def _manage(self, pos: ManagedPosition, tr: Trade, c, risk: RiskState) -> bool:
        cfg = self.cfg
        hit_sl = c.low <= pos.sl if pos.side == "buy" else c.high >= pos.sl
        hit_tp = c.high >= pos.tp if pos.side == "buy" else c.low <= pos.tp

        # 보수적으로 SL 우선
        if hit_sl:
            self._close(pos, tr, pos.sl, c.ts, risk,
                        "breakeven" if pos.moved_to_be else "sl")
            return True
        if hit_tp:
            self._close(pos, tr, pos.tp, c.ts, risk,
                        "partial+tp" if pos.partial_done else "tp")
            return True

        # 미청산이면 봉의 유리한 극점으로 관리 규칙 적용
        favorable = c.high if pos.side == "buy" else c.low
        acts = pos.update(favorable, cfg)
        if acts:
            tr.notes.extend(acts)
            if pos.partial_done and cfg.partial_fraction > 0 and "partial" not in tr.outcome:
                # 분할 청산분 실현
                part_price = pos.entry + (pos.initial_risk * cfg.partial_at_r) * (1 if pos.side == "buy" else -1)
                pnl = self._pnl(pos, part_price, cfg.partial_fraction)
                tr.pnl += pnl
                risk.balance += pnl
                risk.pnl_today += pnl
        return False

    def _swap_cost(self, pos: ManagedPosition, ts: datetime) -> float:
        """보유 일수만큼의 스왑. 수요일은 3배(주말 이자 선반영)로 계산한다."""
        if self.swap_per_lot_night == 0.0:
            return 0.0
        nights = 0
        day = pos.opened_at.date()
        end = ts.date()
        while day < end:
            day = day + timedelta(days=1)
            nights += 3 if day.weekday() == 2 else 1   # 수요일 롤오버는 3배
        return self.swap_per_lot_night * pos.lots * nights

    def _close(self, pos: ManagedPosition, tr: Trade, price: float,
               ts: datetime, risk: RiskState, outcome: str) -> None:
        pnl = self._pnl(pos, price, pos.remaining) + self._swap_cost(pos, ts)
        tr.pnl += pnl
        tr.exit_price = price
        tr.closed_at = ts
        tr.outcome = outcome
        risk_money = pos.initial_risk * pos.lots * self.cfg.contract_size
        tr.r_multiple = tr.pnl / risk_money if risk_money > 0 else 0.0
        risk.register_close(pnl, self.cfg)

    def _pnl(self, pos: ManagedPosition, price: float, fraction: float) -> float:
        d = price - pos.entry if pos.side == "buy" else pos.entry - price
        return d * pos.lots * fraction * self.cfg.contract_size
