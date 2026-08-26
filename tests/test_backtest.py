import unittest
from datetime import datetime, timedelta, timezone

from crowcode.backtest import Backtester, Trade
from crowcode.config import SCALP, preset
from crowcode.data import Candle, synthetic
from crowcode.risk import ManagedPosition, RiskState
from crowcode.signals import Signal

T0 = datetime(2024, 1, 8, 9, 0, tzinfo=timezone.utc)


def bar(i, o, h, l, c):
    return Candle(T0 + timedelta(minutes=i), o, h, l, c)


def dummy_signal(side="buy", entry=100.0, sl=99.0, tp=103.0):
    return Signal(ts=T0, symbol="X", side=side, entry=entry, sl=sl, tp=tp,
                  lots=0.10, risk_amount=10.0, rr=3.0)


class TestPositionLifecycle(unittest.TestCase):
    def setUp(self):
        self.bt = Backtester(SCALP, balance=1000.0, spread=0.0)
        self.risk = RiskState(balance=1000.0)

    def _run(self, pos, candles):
        tr = Trade(signal=dummy_signal(pos.side), opened_at=T0)
        for i, c in enumerate(candles):
            if self.bt._manage(pos, tr, c, self.risk):
                return tr
        return tr

    def test_take_profit_closes_positive(self):
        pos = ManagedPosition("buy", 100.0, 99.0, 103.0, 0.10, 1.0, T0)
        tr = self._run(pos, [bar(1, 100, 101, 99.8, 100.9), bar(2, 100.9, 103.5, 100.8, 103.2)])
        self.assertIn(tr.outcome, ("tp", "partial+tp"))
        self.assertGreater(tr.pnl, 0)
        self.assertGreater(tr.r_multiple, 0)

    def test_stop_loss_closes_at_minus_one_r(self):
        pos = ManagedPosition("buy", 100.0, 99.0, 103.0, 0.10, 1.0, T0)
        tr = self._run(pos, [bar(1, 100, 100.4, 98.5, 98.7)])
        self.assertEqual(tr.outcome, "sl")
        self.assertAlmostEqual(tr.r_multiple, -1.0, places=6)

    def test_sl_wins_when_both_touched_in_one_bar(self):
        pos = ManagedPosition("buy", 100.0, 99.0, 103.0, 0.10, 1.0, T0)
        tr = self._run(pos, [bar(1, 100, 104, 98.0, 101)])
        self.assertEqual(tr.outcome, "sl")

    def test_breakeven_protects_after_2r(self):
        pos = ManagedPosition("buy", 100.0, 99.0, 105.0, 0.10, 1.0, T0)
        tr = self._run(pos, [
            bar(1, 100, 102.2, 99.9, 102.0),   # 2R 터치 → 본절 이동
            bar(2, 102, 102.1, 99.5, 99.6),    # 되돌림 → 본절 청산
        ])
        self.assertEqual(tr.outcome, "breakeven")
        self.assertAlmostEqual(tr.pnl, 0.0, places=6)

    def test_sell_side_take_profit(self):
        pos = ManagedPosition("sell", 100.0, 101.0, 97.0, 0.10, 1.0, T0)
        tr = self._run(pos, [bar(1, 100, 100.2, 96.5, 96.8)])
        self.assertIn(tr.outcome, ("tp", "partial+tp"))
        self.assertGreater(tr.pnl, 0)

    def test_partial_then_tp_records_both(self):
        cfg = SCALP.with_(partial_at_r=2.0, partial_fraction=0.5, breakeven_at_r=1.0)
        bt = Backtester(cfg, balance=1000.0, spread=0.0)
        pos = ManagedPosition("buy", 100.0, 99.0, 104.0, 0.10, 1.0, T0)
        tr = Trade(signal=dummy_signal(), opened_at=T0)
        risk = RiskState(balance=1000.0)
        bt._manage(pos, tr, bar(1, 100, 102.5, 99.9, 102.2), risk)   # 2R → 분할
        self.assertTrue(pos.partial_done)
        self.assertAlmostEqual(pos.remaining, 0.5)
        bt._manage(pos, tr, bar(2, 102, 104.5, 102, 104.2), risk)    # TP
        self.assertEqual(tr.outcome, "partial+tp")
        self.assertGreater(tr.pnl, 0)


class TestFills(unittest.TestCase):
    def test_buy_limit_fills_with_spread(self):
        bt = Backtester(SCALP, spread=0.2)
        price = bt._try_fill(dummy_signal("buy", entry=100.0), bar(1, 101, 101, 99.5, 100.5))
        self.assertAlmostEqual(price, 100.2)

    def test_sell_limit_fills_with_spread(self):
        bt = Backtester(SCALP, spread=0.2)
        price = bt._try_fill(dummy_signal("sell", entry=100.0, sl=101.0, tp=97.0),
                             bar(1, 99, 100.5, 98.5, 99.2))
        self.assertAlmostEqual(price, 99.8)

    def test_no_fill_when_price_never_reaches(self):
        bt = Backtester(SCALP, spread=0.2)
        self.assertIsNone(bt._try_fill(dummy_signal("buy", entry=100.0),
                                       bar(1, 102, 103, 101, 102.5)))


class TestEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = Backtester(preset("scalp"), balance=5000.0, spread=0.20,
                                warmup=800, eval_every=10).run(synthetic(12000))

    def test_runs_and_records_equity(self):
        self.assertTrue(self.result.equity)
        self.assertEqual(self.result.start_balance, 5000.0)

    def test_rejection_summary_shows_filters_working(self):
        self.assertIn("session", self.result.rejections)

    def test_balance_matches_trade_pnl(self):
        expected = self.result.start_balance + sum(t.pnl for t in self.result.trades)
        self.assertAlmostEqual(self.result.end_balance, expected, places=6)

    def test_report_renders(self):
        text = self.result.report()
        self.assertIn("거래 수", text)
        self.assertIn("최대 낙폭", text)

    def test_drawdown_is_non_negative(self):
        self.assertGreaterEqual(self.result.max_drawdown_pct, 0.0)


class TestDailyLimits(unittest.TestCase):
    def test_backtest_never_exceeds_daily_trade_cap(self):
        cfg = preset("highrisk").with_(max_trades_per_day=2, max_consecutive_losses=99)
        res = Backtester(cfg, balance=5000.0, spread=0.20, warmup=800,
                         eval_every=10).run(synthetic(12000))
        per_day: dict = {}
        for t in res.trades:
            key = t.opened_at.date()
            per_day[key] = per_day.get(key, 0) + 1
        for day, n in per_day.items():
            self.assertLessEqual(n, cfg.max_trades_per_day, f"{day} 초과")


if __name__ == "__main__":
    unittest.main()


class TestGoldSwap(unittest.TestCase):
    """금은 스왑이 크게 마이너스라 며칠 보유하면 성과가 뒤집힌다."""

    def _pos(self, opened):
        return ManagedPosition("buy", 1950.0, 1945.0, 1975.0, 0.10, 5.0, opened)

    def test_no_swap_when_closed_same_day(self):
        bt = Backtester(SCALP, spread=0.0, swap_per_lot_night=-10.0)
        pos = self._pos(datetime(2024, 1, 8, 9, 0, tzinfo=timezone.utc))
        self.assertEqual(bt._swap_cost(pos, datetime(2024, 1, 8, 17, 0, tzinfo=timezone.utc)), 0.0)

    def test_one_night_charged_once(self):
        bt = Backtester(SCALP, spread=0.0, swap_per_lot_night=-10.0)
        pos = self._pos(datetime(2024, 1, 8, 9, 0, tzinfo=timezone.utc))   # 월 → 화
        self.assertAlmostEqual(
            bt._swap_cost(pos, datetime(2024, 1, 9, 9, 0, tzinfo=timezone.utc)), -1.0)

    def test_wednesday_rollover_is_tripled(self):
        bt = Backtester(SCALP, spread=0.0, swap_per_lot_night=-10.0)
        pos = self._pos(datetime(2024, 1, 9, 9, 0, tzinfo=timezone.utc))   # 화 → 수
        self.assertAlmostEqual(
            bt._swap_cost(pos, datetime(2024, 1, 10, 9, 0, tzinfo=timezone.utc)), -3.0)

    def test_swap_reduces_a_winning_trade(self):
        bt = Backtester(SCALP, spread=0.0, swap_per_lot_night=-10.0)
        pos = self._pos(datetime(2024, 1, 8, 9, 0, tzinfo=timezone.utc))
        tr = Trade(signal=dummy_signal(), opened_at=pos.opened_at)
        risk = RiskState(balance=1000.0)
        bt._close(pos, tr, 1955.0, datetime(2024, 1, 12, 9, 0, tzinfo=timezone.utc), risk, "tp")
        gross = 5.0 * 0.10 * SCALP.contract_size          # +50
        self.assertLess(tr.pnl, gross)
