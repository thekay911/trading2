import unittest
from datetime import datetime, timedelta, timezone

from crowcode.data import Candle
from crowcode.structure import analyze_structure, swing_points

T0 = datetime(2024, 1, 1, tzinfo=timezone.utc)


def mk(prices, spread=0.5):
    """(open, close) 쌍 목록으로 캔들 생성."""
    out = []
    for i, (o, c) in enumerate(prices):
        out.append(Candle(T0 + timedelta(minutes=i), o, max(o, c) + spread, min(o, c) - spread, c))
    return out


def ladder(values):
    return mk([(values[i], values[i + 1]) for i in range(len(values) - 1)])


class TestSwings(unittest.TestCase):
    def test_detects_high_and_low(self):
        c = ladder([100, 101, 105, 102, 100, 99, 103, 104])
        sw = swing_points(c, 2, 2)
        self.assertTrue(any(s.kind == "high" for s in sw))
        self.assertTrue(any(s.kind == "low" for s in sw))

    def test_confirmation_is_delayed(self):
        c = ladder([100, 101, 105, 102, 100, 99, 103, 104])
        for s in swing_points(c, 2, 2):
            self.assertEqual(s.confirmed_at, s.index + 2)


class TestStructure(unittest.TestCase):
    def test_uptrend_gives_bos_then_choch(self):
        # 상승(고점 갱신 = BOS) 후 붕괴(저점 이탈 = CHOCH)
        path = [100, 104, 101, 108, 103, 110, 106, 112, 105, 99, 95, 90]
        st = analyze_structure(ladder(path), 1, 1)
        kinds = [(e.kind, e.direction) for e in st.events]
        self.assertIn(("BOS", "bullish"), kinds)
        self.assertIn(("CHOCH", "bearish"), kinds)
        self.assertEqual(st.bias, "bearish")

    def test_first_break_is_bos_not_choch(self):
        st = analyze_structure(ladder([100, 103, 101, 99, 102, 107]), 1, 1)
        self.assertTrue(st.events)
        self.assertEqual(st.events[0].kind, "BOS")

    def test_no_lookahead(self):
        """미래 봉을 붙여도 과거 이벤트가 바뀌지 않아야 한다."""
        path = [100, 104, 101, 108, 103, 110, 106, 112, 105, 99, 95, 90]
        a = analyze_structure(ladder(path[:8]), 1, 1)
        b = analyze_structure(ladder(path), 1, 1)
        early = [e for e in b.events if e.index < len(path[:8]) - 1 - 1]
        for x, y in zip(a.events, early):
            self.assertEqual((x.index, x.kind, x.direction), (y.index, y.kind, y.direction))


if __name__ == "__main__":
    unittest.main()
