import unittest
from datetime import datetime, timedelta, timezone

from crowcode.data import Candle, synthetic
from ict.liquidity import Pool, Raid, draw_targets, equal_levels, find_raids, reference_pools
from ict.pdarrays import (
    balanced_price_ranges, breaker, fair_value_gaps, inversion_fvgs,
    is_filled, order_block, unicorn,
)
from ict.structure import BEAR, BULL, swings

T0 = datetime(2024, 1, 15, 8, 0, tzinfo=timezone.utc)


def bars(spec):
    return [Candle(T0 + timedelta(minutes=5 * i), *v) for i, v in enumerate(spec)]


class TestRaidsHappenOnce(unittest.TestCase):
    """ICT 의 핵심 — 스톱은 한 번 털리면 사라진다."""

    def setUp(self):
        # 저점 아래로 뚫고 계속 그 아래에 머무는 흐름
        self.c = bars([
            (100, 100.5, 99.5, 100), (100, 100.2, 98.0, 98.5),
            (98.5, 99.0, 97.0, 97.5), (97.5, 98.0, 96.5, 97.0),
            (97.0, 97.5, 96.0, 96.5),
        ])
        self.pool = Pool("SSL", 99.0, "PDL", 0)

    def test_only_the_first_breach_counts(self):
        raids = find_raids(self.c, [self.pool], require_close_back=False)
        self.assertEqual(len(raids), 1, "이미 뚫린 풀을 매 봉 습격으로 세고 있다")
        self.assertEqual(raids[0].index, 1)

    def test_direction_of_a_low_raid_is_bullish(self):
        r = find_raids(self.c, [self.pool], require_close_back=False)[0]
        self.assertEqual(r.direction, BULL)

    def test_close_back_requirement(self):
        c = bars([(100, 100.5, 99.5, 100), (100, 100.2, 98.0, 99.8)])   # 종가 회복
        self.assertTrue(find_raids(c, [self.pool], require_close_back=True))
        c2 = bars([(100, 100.5, 99.5, 100), (100, 100.2, 98.0, 98.2)])  # 회복 못함
        self.assertFalse(find_raids(c2, [self.pool], require_close_back=True))


class TestReferencePools(unittest.TestCase):
    def test_finds_pdh_pdl(self):
        s = list(synthetic(6000, minutes=15))
        labels = {p.label for p in reference_pools(s, len(s) - 1)}
        self.assertIn("PDH", labels)
        self.assertIn("PDL", labels)

    def test_targets_are_on_the_right_side(self):
        s = list(synthetic(6000, minutes=15))
        pools = reference_pools(s, len(s) - 1)
        price = s[-1].close
        for p in draw_targets(pools, "buy", price):
            self.assertGreater(p.price, price)
        for p in draw_targets(pools, "sell", price):
            self.assertLess(p.price, price)

    def test_equal_levels_need_repetition(self):
        sw = swings(list(synthetic(3000, minutes=5)))
        few = equal_levels(sw, tol=0.01, min_count=5)
        many = equal_levels(sw, tol=0.5, min_count=2)
        self.assertLessEqual(len(few), len(many))


class TestFvg(unittest.TestCase):
    def test_bullish_gap(self):
        c = bars([(100, 101, 99, 100.8), (100.8, 103, 100.6, 102.9),
                  (102.9, 104, 102, 103.5)])
        g = fair_value_gaps(c)
        self.assertEqual(len(g), 1)
        self.assertEqual(g[0].direction, BULL)
        self.assertAlmostEqual(g[0].bottom, 101)
        self.assertAlmostEqual(g[0].top, 102)

    def test_consequent_encroachment_is_the_midpoint(self):
        c = bars([(100, 101, 99, 100.8), (100.8, 103, 100.6, 102.9),
                  (102.9, 104, 102, 103.5)])
        self.assertAlmostEqual(fair_value_gaps(c)[0].mid, 101.5)

    def test_fill_detection(self):
        c = bars([(100, 101, 99, 100.8), (100.8, 103, 100.6, 102.9),
                  (102.9, 104, 102, 103.5), (103.5, 103.6, 101.4, 101.6)])
        g = fair_value_gaps(c)[0]
        self.assertTrue(is_filled(c, g, 3, full=False))    # CE 터치
        self.assertFalse(is_filled(c, g, 3, full=True))    # 완전 메움은 아님

    def test_inversion(self):
        c = bars([(100, 101, 99, 100.8), (100.8, 103, 100.6, 102.9),
                  (102.9, 104, 102, 103.5), (103.5, 103.6, 100.0, 100.2)])
        inv = inversion_fvgs(c, fair_value_gaps(c), 3)
        self.assertTrue(inv)
        self.assertEqual(inv[0].direction, BEAR)


class TestOrderBlockAndBreaker(unittest.TestCase):
    def test_last_opposite_candle(self):
        c = bars([(100, 101, 99.5, 100.5), (100.5, 101, 100, 100.2),
                  (100.2, 100.4, 99, 99.2),      # 마지막 음봉 = 강세 OB
                  (99.2, 103, 99.1, 102.8)])
        ob = order_block(c, 3, BULL)
        self.assertEqual(ob.index, 2)

    def test_breaker_flips_direction(self):
        c = bars([(100, 101, 99.5, 100.5), (100.5, 100.6, 99, 99.2),
                  (99.2, 103, 99.1, 102.8), (102.8, 103, 98.0, 98.2)])
        ob = order_block(c, 2, BULL)
        br = breaker(c, ob, 3)
        self.assertIsNotNone(br)
        self.assertEqual(br.direction, BEAR)


class TestCombinations(unittest.TestCase):
    def test_bpr_requires_opposite_gaps(self):
        s = list(synthetic(3000, minutes=5))
        gaps = fair_value_gaps(s, min_size=0.1)
        for b in balanced_price_ranges(gaps[:200]):
            self.assertGreater(b.top, b.bottom)

    def test_unicorn_needs_breaker_and_fvg(self):
        self.assertIsNone(unicorn([]))


if __name__ == "__main__":
    unittest.main()
