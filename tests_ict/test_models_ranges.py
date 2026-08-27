import unittest

from crowcode.data import synthetic
from ict.backtest import run
from ict.models import Config, find_setup, scan
from ict.ranges import OTE_END, OTE_START, DealingRange, leg_range
from ict.structure import BEAR, BULL
from ict.timeops import active_windows


class TestDealingRange(unittest.TestCase):
    def setUp(self):
        self.dr = DealingRange(high=2000.0, low=1900.0, high_index=50, low_index=10)

    def test_equilibrium_is_the_midpoint(self):
        self.assertAlmostEqual(self.dr.equilibrium, 1950.0)

    def test_premium_and_discount(self):
        self.assertTrue(self.dr.is_discount(1920.0))
        self.assertTrue(self.dr.is_premium(1980.0))
        self.assertFalse(self.dr.is_discount(1980.0))

    def test_buy_ote_is_below_equilibrium(self):
        lo, hi = self.dr.ote("buy")
        self.assertAlmostEqual(hi, 2000 - 100 * OTE_START)     # 1938
        self.assertAlmostEqual(lo, 2000 - 100 * OTE_END)       # 1921
        self.assertTrue(self.dr.is_discount(hi))

    def test_sell_ote_is_above_equilibrium(self):
        lo, hi = self.dr.ote("sell")
        self.assertTrue(self.dr.is_premium(lo))

    def test_projection_extends_beyond_the_range(self):
        self.assertAlmostEqual(self.dr.projection(1.0, "buy"), 2100.0)
        self.assertAlmostEqual(self.dr.projection(1.0, "sell"), 1800.0)

    def test_direction_from_which_extreme_came_last(self):
        self.assertEqual(self.dr.direction, BULL)
        self.assertEqual(DealingRange(2000, 1900, 10, 50).direction, BEAR)


class TestSetupIntegrity(unittest.TestCase):
    """셋업이 나왔다면 ICT 규칙을 전부 만족해야 한다."""

    @classmethod
    def setUpClass(cls):
        cls.candles = list(synthetic(20000, minutes=5))
        cls.cfg = Config()
        cls.setups = scan(cls.candles, cls.cfg)

    def test_produces_setups(self):
        self.assertTrue(self.setups, "합성 데이터에서 셋업이 하나도 없다")

    def test_levels_are_ordered(self):
        for s in self.setups:
            if s.side == "buy":
                self.assertLess(s.stop, s.entry)
                self.assertLess(s.entry, s.target)
            else:
                self.assertGreater(s.stop, s.entry)
                self.assertGreater(s.entry, s.target)

    def test_min_rr_is_respected(self):
        for s in self.setups:
            self.assertGreaterEqual(s.rr + 1e-9, self.cfg.min_rr)

    def test_every_setup_is_in_an_allowed_window(self):
        for s in self.setups:
            self.assertTrue(any(w in self.cfg.allowed_windows
                                for w in active_windows(s.ts)),
                            f"{s.ts} 는 허용 창 밖이다")

    def test_every_mss_has_displacement(self):
        for s in self.setups:
            self.assertIsNotNone(s.mss.displacement)
            self.assertEqual(s.mss.kind, "MSS")

    def test_raid_is_on_the_correct_side(self):
        for s in self.setups:
            if s.raid is None:
                continue
            if s.side == "buy":
                self.assertEqual(s.raid.direction, BULL)
            else:
                self.assertEqual(s.raid.direction, BEAR)

    def test_raid_precedes_the_mss(self):
        for s in self.setups:
            if s.raid is not None:
                self.assertLessEqual(s.raid.index, s.mss.index)

    def test_no_duplicate_mss(self):
        keys = [(s.mss_index, s.side) for s in self.setups]
        self.assertEqual(len(keys), len(set(keys)), "같은 MSS 가 중복으로 잡혔다")

    def test_entry_sits_inside_its_pd_array(self):
        for s in self.setups:
            self.assertTrue(s.array.contains(s.entry) or
                            abs(s.array.mid - s.entry) < 1e-6)


class TestGatesActuallyGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.candles = list(synthetic(12000, minutes=5))

    def _count(self, **kw):
        return len(scan(self.candles, Config(**kw)))

    def test_killzone_filter_reduces_setups(self):
        self.assertLessEqual(self._count(require_killzone=True),
                             self._count(require_killzone=False))

    def test_raid_requirement_reduces_setups(self):
        self.assertLessEqual(self._count(require_raid=True),
                             self._count(require_raid=False))

    def test_premium_discount_filter_reduces_setups(self):
        self.assertLessEqual(self._count(require_discount_premium=True),
                             self._count(require_discount_premium=False))

    def test_higher_min_rr_reduces_setups(self):
        self.assertLessEqual(self._count(min_rr=5.0), self._count(min_rr=1.5))


class TestNoLookahead(unittest.TestCase):
    def test_truncated_history_gives_the_same_setup(self):
        candles = list(synthetic(12000, minutes=5))
        cfg = Config()
        found = None
        for i in range(500, 6000):
            s = find_setup(candles, i, cfg)
            if s:
                found = (i, s)
                break
        self.assertIsNotNone(found, "비교할 셋업을 찾지 못함")
        i, full = found
        cut = find_setup(candles[:i + 1], i, cfg)
        self.assertIsNotNone(cut, "잘린 시계열에서 셋업이 사라짐 → 룩어헤드")
        self.assertAlmostEqual(full.entry, cut.entry, places=6)
        self.assertAlmostEqual(full.stop, cut.stop, places=6)


class TestBacktest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.res = run(list(synthetic(12000, minutes=5)), Config(), spread=0.25)

    def test_runs(self):
        self.assertGreaterEqual(self.res.setups, 0)
        self.assertLessEqual(self.res.n, self.res.setups)

    def test_losses_are_about_minus_one_r(self):
        for t in self.res.trades:
            if t.outcome == "stop":
                self.assertLess(t.r, 0)
                self.assertGreater(t.r, -1.6)   # 스프레드 때문에 -1 을 조금 넘는다

    def test_report_renders(self):
        text = self.res.report()
        self.assertIn("승률", text)
        self.assertIn("킬존별", text)


if __name__ == "__main__":
    unittest.main()
