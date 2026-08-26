import unittest
from datetime import datetime, timedelta, timezone

from crowcode.data import Candle
from crowcode.liquidity import (
    POI, collect_pois, fair_value_gaps, find_sweeps, invalidated,
    last_sweep, liquidity_pools, order_blocks,
)
from crowcode.structure import analyze_structure, swing_points

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def c(i, o, h, l, cl):
    return Candle(T0 + timedelta(minutes=i), o, h, l, cl)


class TestSweeps(unittest.TestCase):
    def test_sweep_below_prior_low(self):
        # 저점 형성 → 꼬리로 뚫고 종가는 회복
        candles = [
            c(0, 100, 100.5, 99.5, 100),
            c(1, 100, 100.2, 98.0, 98.5),   # 저점
            c(2, 98.5, 99.5, 98.4, 99.4),
            c(3, 99.4, 100.0, 99.3, 99.9),
            c(4, 99.9, 100.1, 97.5, 99.8),  # 스윕: 98.0 아래 꼬리, 종가 회복
        ]
        sw = swing_points(candles, 1, 1)
        sweeps = find_sweeps(candles, sw, lookback=10)
        self.assertTrue(any(s.direction == "below" and s.index == 4 for s in sweeps))

    def test_clean_break_is_not_a_sweep(self):
        candles = [
            c(0, 100, 100.5, 99.5, 100),
            c(1, 100, 100.2, 98.0, 98.5),
            c(2, 98.5, 99.5, 98.4, 99.4),
            c(3, 99.4, 100.0, 99.3, 99.9),
            c(4, 99.9, 100.0, 97.0, 97.1),  # 종가까지 아래 → 돌파지 스윕이 아님
        ]
        sw = swing_points(candles, 1, 1)
        self.assertFalse(find_sweeps(candles, sw, lookback=10))

    def test_last_sweep_direction_matching(self):
        candles = [
            c(0, 100, 100.5, 99.5, 100),
            c(1, 100, 100.2, 98.0, 98.5),
            c(2, 98.5, 99.5, 98.4, 99.4),
            c(3, 99.4, 100.0, 99.3, 99.9),
            c(4, 99.9, 100.1, 97.5, 99.8),
        ]
        sw = swing_points(candles, 1, 1)
        sweeps = find_sweeps(candles, sw, lookback=10)
        self.assertIsNotNone(last_sweep(sweeps, "buy", 4, 10))   # 아래 스윕 = 매수 근거
        self.assertIsNone(last_sweep(sweeps, "sell", 4, 10))


class TestFVG(unittest.TestCase):
    def test_bullish_gap(self):
        candles = [c(0, 100, 101, 99, 100.8), c(1, 100.8, 103, 100.6, 102.9),
                   c(2, 102.9, 104, 102, 103.5)]
        gaps = fair_value_gaps(candles)
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0].side, "buy")
        self.assertAlmostEqual(gaps[0].bottom, 101)
        self.assertAlmostEqual(gaps[0].top, 102)

    def test_bearish_gap(self):
        candles = [c(0, 104, 105, 103, 103.2), c(1, 103.2, 103.4, 100, 100.2),
                   c(2, 100.2, 102, 99, 99.5)]
        gaps = fair_value_gaps(candles)
        self.assertEqual(gaps[0].side, "sell")


class TestOrderBlocks(unittest.TestCase):
    def test_last_down_candle_before_bullish_break(self):
        candles = [
            c(0, 100, 101, 99.5, 100.5),
            c(1, 100.5, 101.2, 100, 100.2),
            c(2, 100.2, 100.4, 99, 99.2),    # 마지막 음봉 = 오더블록
            c(3, 99.2, 103, 99.1, 102.8),    # 임펄스 돌파
            c(4, 102.8, 103.5, 102, 103.2),
        ]
        st = analyze_structure(candles, 1, 1)
        obs = [o for o in order_blocks(candles, st.events) if o.side == "buy"]
        self.assertTrue(obs)
        self.assertEqual(obs[0].index, 2)


class TestPOI(unittest.TestCase):
    def test_touch_does_not_invalidate_but_close_through_does(self):
        poi = POI("order_block", "buy", top=101.0, bottom=100.0, index=0)
        touch = [c(0, 100.5, 101, 100, 100.8), c(1, 100.8, 101, 100.2, 100.9)]
        self.assertFalse(invalidated(touch, poi, 1))
        through = touch + [c(2, 100.9, 101, 99.0, 99.2)]
        self.assertTrue(invalidated(through, poi, 2))

    def test_entry_price_modes(self):
        buy = POI("fvg", "buy", top=101.0, bottom=100.0, index=0)
        self.assertEqual(buy.entry_price("proximal"), 101.0)
        self.assertEqual(buy.entry_price("distal"), 100.0)
        self.assertEqual(buy.entry_price("mid"), 100.5)
        sell = POI("fvg", "sell", top=101.0, bottom=100.0, index=0)
        self.assertEqual(sell.entry_price("proximal"), 100.0)

    def test_split_entries_span_the_zone(self):
        buy = POI("fvg", "buy", top=101.0, bottom=100.0, index=0)
        e = buy.split_entries(2)
        self.assertEqual(e, [101.0, 100.0])


class TestPools(unittest.TestCase):
    def test_equal_highs_cluster(self):
        candles = [
            c(0, 100, 102.0, 99, 101), c(1, 101, 101.5, 100, 100.5),
            c(2, 100.5, 102.02, 100, 101), c(3, 101, 101.4, 100, 100.4),
            c(4, 100.4, 101.99, 100, 100.8), c(5, 100.8, 101.0, 100, 100.2),
        ]
        sw = swing_points(candles, 1, 1)
        pools = liquidity_pools(sw, tol=0.1)
        self.assertTrue(any(p.kind == "equal_highs" and p.strength >= 2 for p in pools))


if __name__ == "__main__":
    unittest.main()
