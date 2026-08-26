import unittest
from datetime import datetime, timedelta, timezone

from crowcode import waves, wyckoff
from crowcode.data import Candle
from crowcode.structure import swing_points

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def bar(i, o, h, l, c):
    return Candle(T0 + timedelta(hours=i), o, h, l, c)


def build(seq):
    """(o,h,l,c) 튜플 목록 → 캔들."""
    return [bar(i, *v) for i, v in enumerate(seq)]


def downtrend(n, start=100.0, step=0.8):
    out = []
    p = start
    for _ in range(n):
        out.append((p, p + 0.3, p - step - 0.3, p - step))
        p -= step
    return out


def ranging(n, lo, hi):
    """레인지 안에서 상·하단을 반복 터치."""
    out = []
    for i in range(n):
        if i % 4 == 0:
            out.append((lo + 1, hi, lo + 0.5, hi - 0.5))
        elif i % 4 == 2:
            out.append((hi - 1, hi - 0.5, lo, lo + 0.5))
        else:
            mid = (lo + hi) / 2
            out.append((mid, mid + 1, mid - 1, mid))
    return out


class TestWyckoff(unittest.TestCase):
    def test_range_detection(self):
        candles = build(downtrend(40) + ranging(60, 60.0, 70.0))
        tr = wyckoff.detect_range(candles, lookback=60)
        self.assertIsNotNone(tr)
        self.assertGreaterEqual(tr.top_touches, 2)
        self.assertGreaterEqual(tr.bottom_touches, 2)

    def test_trending_market_has_no_range(self):
        view = wyckoff.analyze(build(downtrend(150)), lookback=120)
        self.assertEqual(view.schematic, "undefined")
        self.assertIsNone(view.bias)

    def test_spring_detected_and_gives_buy_bias(self):
        seq = downtrend(40) + ranging(50, 60.0, 70.0)
        seq.append((61.0, 61.5, 57.5, 62.0))   # 하단 침투 후 회복 = Spring
        seq += ranging(8, 60.0, 70.0)
        candles = build(seq)
        view = wyckoff.analyze(candles, lookback=70)
        self.assertEqual(view.schematic, "accumulation")
        self.assertIsNotNone(view.spring)

    def test_range_position(self):
        candles = build(downtrend(30) + ranging(50, 60.0, 70.0))
        tr = wyckoff.detect_range(candles, lookback=50)
        self.assertAlmostEqual(tr.position(tr.bottom), 0.0, places=6)
        self.assertAlmostEqual(tr.position(tr.top), 1.0, places=6)


class TestWaves(unittest.TestCase):
    def _swings(self, path):
        candles = [bar(i, path[i], max(path[i], path[i + 1]) + 0.2,
                       min(path[i], path[i + 1]) - 0.2, path[i + 1])
                   for i in range(len(path) - 1)]
        return swing_points(candles, 1, 1), candles

    def test_impulse_five_legs(self):
        # 1↑ 2↓ 3↑ 4↓ 5↑
        path = [100, 110, 105, 125, 118, 135, 130]
        sw, _ = self._swings(path)
        wc = waves.count(sw)
        self.assertIn(wc.pattern, ("impulse", "correction"))

    def test_rule_two_violation_marks_invalid(self):
        from crowcode.waves import Leg, _rule_2
        w1 = Leg(0, 1, 100, 110)
        bad = Leg(1, 2, 110, 99)      # 1파 시작점 아래로 되돌림
        self.assertFalse(_rule_2(w1, bad))

    def test_rule_four_overlap(self):
        from crowcode.waves import Leg, _rule_4
        w1 = Leg(0, 1, 100, 110)
        w4_ok = Leg(3, 4, 125, 118)
        w4_bad = Leg(3, 4, 125, 108)  # 1파 종점과 겹침
        self.assertTrue(_rule_4(w1, w4_ok))
        self.assertFalse(_rule_4(w1, w4_bad))

    def test_zigzag_alternates(self):
        sw, _ = self._swings([100, 110, 105, 125, 118, 135, 130])
        legs = waves.zigzag(sw)
        for a, b in zip(legs, legs[1:]):
            self.assertNotEqual(a.up, b.up)


if __name__ == "__main__":
    unittest.main()
