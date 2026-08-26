import unittest
from datetime import datetime, timedelta, timezone

from crowcode.config import SCALP, preset
from crowcode.risk import (
    ManagedPosition, RiskState, max_lots_by_leverage, position_size,
    round_lots, split_capital, validate_rr,
)

T0 = datetime(2024, 1, 8, 9, 0, tzinfo=timezone.utc)


class TestSizing(unittest.TestCase):
    def test_risk_percent_is_respected(self):
        cfg = SCALP.with_(risk_pct=1.0, max_leverage=500)
        lots, risk = position_size(10_000, 1.0, 1950.0, 1945.0, cfg)
        # 5달러 스톱, 1랏 = 100oz → 1랏당 500달러 리스크 → 100달러 리스크 = 0.2랏
        self.assertAlmostEqual(lots, 0.2, places=2)
        self.assertAlmostEqual(risk, 100.0, delta=1.0)

    def test_leverage_cap_shrinks_size(self):
        cfg = SCALP.with_(max_leverage=20)
        capped, _ = position_size(1000, 6.0, 1950.0, 1949.0, cfg)
        limit = max_lots_by_leverage(1000, 20, cfg.contract_size, 1950.0)
        self.assertLessEqual(capped, limit + 1e-9)

    def test_below_min_lot_returns_zero(self):
        lots, _ = position_size(50, 0.5, 1950.0, 1900.0, SCALP)
        self.assertEqual(lots, 0.0)

    def test_round_lots_floors_to_step(self):
        self.assertEqual(round_lots(0.1749, 0.01, 0.01, 50), 0.17)

    def test_zero_stop_is_rejected(self):
        self.assertEqual(position_size(1000, 1.0, 1950.0, 1950.0, SCALP), (0.0, 0.0))


class TestRR(unittest.TestCase):
    def test_valid_buy(self):
        ok, rr = validate_rr(100, 99, 103, "buy", 2.0)
        self.assertTrue(ok)
        self.assertAlmostEqual(rr, 3.0)

    def test_inverted_levels_rejected(self):
        ok, _ = validate_rr(100, 101, 103, "buy", 2.0)
        self.assertFalse(ok)

    def test_below_min_rr_rejected(self):
        ok, rr = validate_rr(100, 99, 101.5, "buy", 2.0)
        self.assertFalse(ok)
        self.assertAlmostEqual(rr, 1.5)


class TestManagedPosition(unittest.TestCase):
    def _pos(self):
        return ManagedPosition("buy", 100.0, 99.0, 103.0, 0.1, 1.0, T0)

    def test_breakeven_moves_at_2r(self):
        p = self._pos()
        self.assertEqual(p.update(101.5, SCALP), [])       # 1.5R — 아직
        acts = p.update(102.0, SCALP)                      # 2R
        self.assertTrue(acts)
        self.assertEqual(p.sl, 100.0)
        self.assertTrue(p.moved_to_be)

    def test_sl_never_moves_backward(self):
        p = self._pos()
        p.update(102.0, SCALP)
        p.sl = 101.0                                       # 이미 앞으로 옮긴 상태
        p.moved_to_be = False
        p.update(102.5, SCALP)
        self.assertEqual(p.sl, 101.0)                      # 100.0 으로 되돌리지 않는다

    def test_partial_at_3r(self):
        p = self._pos()
        p.update(103.0, SCALP)
        self.assertTrue(p.partial_done)
        self.assertAlmostEqual(p.remaining, 0.5)

    def test_r_multiple_sign_for_sell(self):
        p = ManagedPosition("sell", 100.0, 101.0, 97.0, 0.1, 1.0, T0)
        self.assertAlmostEqual(p.r_multiple(98.0), 2.0)


class TestRiskState(unittest.TestCase):
    def test_two_consecutive_losses_stop_the_day(self):
        st = RiskState(balance=1000.0)
        st.roll_day(T0)
        st.register_close(-10, SCALP)
        st.register_close(-10, SCALP)
        ok, why = st.can_trade(T0, SCALP)
        self.assertFalse(ok)
        self.assertIn("연속 손절", why)

    def test_win_resets_streak(self):
        st = RiskState(balance=1000.0)
        st.roll_day(T0)
        st.register_close(-10, SCALP)
        st.register_close(+30, SCALP)
        self.assertEqual(st.consecutive_losses, 0)
        self.assertTrue(st.can_trade(T0, SCALP)[0])

    def test_new_day_resets_counters(self):
        st = RiskState(balance=1000.0)
        st.roll_day(T0)
        st.trades_today = SCALP.max_trades_per_day
        self.assertFalse(st.can_trade(T0, SCALP)[0])
        st.consecutive_losses = 0
        self.assertTrue(st.can_trade(T0 + timedelta(days=1), SCALP)[0])

    def test_daily_loss_cap(self):
        cfg = SCALP.with_(max_daily_loss_pct=3.0, max_consecutive_losses=99)
        st = RiskState(balance=1000.0)
        st.roll_day(T0)
        st.register_close(-31, cfg)
        self.assertFalse(st.can_trade(T0, cfg)[0])


class TestAccountSplit(unittest.TestCase):
    def test_split_sums_to_capital(self):
        sp = split_capital(5000, SCALP)
        self.assertAlmostEqual(sp.swing + sp.scalp + sp.high_risk, 5000, places=2)
        self.assertGreater(sp.swing, sp.scalp)
        self.assertGreater(sp.scalp, sp.high_risk)

    def test_presets_have_expected_risk_ordering(self):
        self.assertLess(preset("scalp").risk_pct, preset("highrisk").risk_pct)
        self.assertLessEqual(preset("highrisk").max_leverage, preset("swing").max_leverage)


if __name__ == "__main__":
    unittest.main()
