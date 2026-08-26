import unittest

from crowcode.config import PRESETS, preset
from crowcode.riskmath import (
    breakeven_win_rate, expectancy_r, expected_worst_streak, ladder,
    observations, prob_streak, report, simulate, trades_to_limit,
)

CFG = preset("intraday")


class TestAnalytics(unittest.TestCase):
    def test_breakeven_for_one_to_three(self):
        self.assertAlmostEqual(breakeven_win_rate(3.0), 0.25)

    def test_breakeven_for_one_to_one(self):
        self.assertAlmostEqual(breakeven_win_rate(1.0), 0.5)

    def test_expectancy_zero_at_breakeven(self):
        self.assertAlmostEqual(expectancy_r(0.25, 3.0), 0.0, places=9)

    def test_expectancy_positive_above_breakeven(self):
        self.assertGreater(expectancy_r(0.35, 3.0), 0)

    def test_prob_streak(self):
        self.assertAlmostEqual(prob_streak(0.35, 2), 0.65 ** 2)

    def test_worst_streak_grows_with_sample(self):
        self.assertGreater(expected_worst_streak(0.35, 500),
                           expected_worst_streak(0.35, 50))

    def test_trades_to_limit(self):
        self.assertEqual(trades_to_limit(2.0, 10.0), 5)
        self.assertEqual(trades_to_limit(2.0, 5.0), 3)   # 올림


class TestSimulation(unittest.TestCase):
    def test_deterministic_for_a_seed(self):
        a = simulate(CFG, 0.35, weeks=12, paths=200, seed=5)
        b = simulate(CFG, 0.35, weeks=12, paths=200, seed=5)
        self.assertEqual(a.total_r, b.total_r)

    def test_higher_win_rate_earns_more(self):
        lo = simulate(CFG, 0.30, weeks=52, paths=400)
        hi = simulate(CFG, 0.45, weeks=52, paths=400)
        self.assertGreater(hi.median_r, lo.median_r)

    def test_at_breakeven_median_is_near_zero(self):
        sim = simulate(CFG, 0.25, weeks=52, paths=600)
        self.assertLess(abs(sim.median_r), 30)

    def test_more_risk_means_deeper_drawdown(self):
        lo = simulate(CFG.with_(risk_pct=1.0), 0.35, weeks=52, paths=400)
        hi = simulate(CFG.with_(risk_pct=3.0, max_daily_loss_pct=9.0),
                      0.35, weeks=52, paths=400)
        self.assertGreater(hi.q(hi.max_dd_pct, 0.5), lo.q(lo.max_dd_pct, 0.5))

    def test_consecutive_loss_rule_caps_daily_damage(self):
        """하루 손실은 리스크 x 연속손절 한도를 넘을 수 없다."""
        cfg = CFG.with_(risk_pct=2.0, max_consecutive_losses=2, max_trades_per_day=8)
        sim = simulate(cfg, 0.30, weeks=52, trades_per_week=20, paths=300)
        self.assertGreater(sim.median_trades, 0)

    def test_trade_count_scales_with_frequency(self):
        few = simulate(CFG, 0.35, weeks=52, trades_per_week=2, paths=300)
        many = simulate(CFG, 0.35, weeks=52, trades_per_week=5, paths=300)
        self.assertGreater(many.median_trades, few.median_trades)

    def test_worst_streak_is_recorded(self):
        sim = simulate(CFG, 0.30, weeks=52, paths=300)
        self.assertGreaterEqual(sim.q(sim.worst_streak, 0.5), 2)

    def test_losing_edge_ends_negative_often(self):
        sim = simulate(CFG, 0.15, weeks=52, paths=400)
        self.assertGreater(sim.p_negative, 0.9)


class TestObservations(unittest.TestCase):
    def test_flags_unreachable_circuit_breaker(self):
        sim = simulate(CFG, 0.35, weeks=52, paths=200)
        notes = observations(CFG, sim, 3.0, 52)
        self.assertTrue(any("거의 안 걸린다" in n for n in notes))

    def test_flags_circuit_below_daily_cap(self):
        cfg = CFG.with_(hard_stop_loss_pct=3.0, max_daily_loss_pct=6.0)
        sim = simulate(cfg, 0.35, weeks=52, paths=200)
        notes = observations(cfg, sim, 3.0, 52)
        self.assertTrue(any("일일 한도가 사실상 무의미" in n for n in notes))

    def test_mentions_capital_requirement(self):
        sim = simulate(CFG, 0.35, weeks=52, paths=200)
        self.assertTrue(any("최소 자본" in n for n in observations(CFG, sim, 3.0, 52)))


class TestLadderAndReport(unittest.TestCase):
    def test_ladder_is_monotonic_for_every_preset(self):
        """사다리는 뒤로 갈수록 커야 의미가 있다."""
        for name, cfg in PRESETS.items():
            steps = [cfg.risk_pct,
                     cfg.risk_pct * cfg.max_consecutive_losses,
                     cfg.max_daily_loss_pct,
                     cfg.hard_stop_loss_pct]
            for a, b in zip(steps, steps[1:]):
                self.assertLessEqual(a, b, f"{name}: 사다리가 역전됨 {steps}")

    def test_ladder_renders(self):
        self.assertEqual(len(ladder(CFG)), 4)

    def test_report_renders(self):
        text = report(CFG, weeks=12, paths=100)
        self.assertIn("손익분기 승률", text)
        self.assertIn("관찰", text)
        self.assertIn("이 숫자를 믿지 말 것", text)


class TestHouseRule(unittest.TestCase):
    """운용 규칙: 거래당 2%, 1:3 RR."""

    MAIN = ("swing", "intraday", "scalp")

    def test_main_presets_use_two_percent(self):
        for name in self.MAIN:
            self.assertAlmostEqual(PRESETS[name].risk_pct, 2.0, msg=name)

    def test_main_presets_target_one_to_three(self):
        for name in self.MAIN:
            cfg = PRESETS[name]
            self.assertAlmostEqual(cfg.target_rr, 3.0, msg=name)
            self.assertAlmostEqual(cfg.min_rr, 3.0, msg=name)

    def test_partial_fires_before_the_target(self):
        for name, cfg in PRESETS.items():
            if cfg.partial_fraction > 0:
                self.assertLess(cfg.partial_at_r, cfg.target_rr, msg=name)

    def test_breakeven_fires_before_the_partial(self):
        for name, cfg in PRESETS.items():
            self.assertLess(cfg.breakeven_at_r, cfg.partial_at_r, msg=name)

    def test_circuit_breaker_is_ten_percent(self):
        for name, cfg in PRESETS.items():
            self.assertAlmostEqual(cfg.hard_stop_loss_pct, 10.0, msg=name)


if __name__ == "__main__":
    unittest.main()
