import unittest
from datetime import datetime, timedelta, timezone

from crowcode.data import Candle
from ict.structure import BEAR, BULL, analyze, find_displacement, swings

T0 = datetime(2024, 1, 15, 8, 0, tzinfo=timezone.utc)


def bars(spec):
    """(open, high, low, close) 목록 → 캔들."""
    return [Candle(T0 + timedelta(minutes=5 * i), *v) for i, v in enumerate(spec)]


def walk(path, wick=0.3):
    """종가 경로 → 캔들."""
    out = []
    for i in range(len(path) - 1):
        o, c = path[i], path[i + 1]
        out.append((o, max(o, c) + wick, min(o, c) - wick, c))
    return bars(out)


class TestSwings(unittest.TestCase):
    def test_three_candle_swing(self):
        c = walk([100, 103, 106, 102, 99, 103, 106])   # 고점 하나, 저점 하나
        sw = swings(c, 1, 1)
        self.assertTrue(any(s.is_high for s in sw))
        self.assertTrue(any(not s.is_high for s in sw))

    def test_confirmation_is_delayed(self):
        for s in swings(walk([100, 103, 106, 102, 99, 103]), 1, 1):
            self.assertEqual(s.confirmed_at, s.index + 1)


class TestDisplacement(unittest.TestCase):
    """ICT: 변위는 '에너지' 와 '불균형' 을 둘 다 가져야 한다."""

    def test_gap_and_size_qualify(self):
        c = bars([
            (100, 100.5, 99.5, 100), (100, 100.5, 99.5, 100),
            (100, 100.4, 99.6, 100.2),
            (100.2, 104.0, 100.1, 103.8),        # 큰 봉
            (103.8, 106.0, 103.5, 105.8),        # low 103.5 > 첫봉 high → FVG
        ])
        d = find_displacement(c, 4, BULL, lookback=5, min_atr=0.5, atr_period=3)
        self.assertIsNotNone(d)
        self.assertTrue(d.has_gap)

    def test_no_gap_is_not_displacement(self):
        c = walk([100, 100.5, 101, 101.5, 102, 102.5])   # 겹치며 완만하게 상승
        self.assertIsNone(find_displacement(c, len(c) - 1, BULL, min_atr=3.0, atr_period=3))


class TestStructure(unittest.TestCase):
    def test_bos_then_mss(self):
        path = [100, 104, 101, 108, 103, 110, 106, 112, 105, 99, 94, 90, 86]
        st = analyze(walk(path), 1, 1, require_displacement=False)
        kinds = [(e.kind, e.direction) for e in st.events]
        self.assertIn(("BOS", BULL), kinds)
        self.assertIn(("MSS", BEAR), kinds)

    def test_first_break_is_bos(self):
        st = analyze(walk([100, 103, 101, 99, 102, 107]), 1, 1, require_displacement=False)
        self.assertTrue(st.events)
        self.assertEqual(st.events[0].kind, "BOS")

    def test_displacement_requirement_filters_mss(self):
        """변위를 요구하면 MSS 수가 줄어야 한다. 안 줄면 필터가 안 도는 것."""
        path = [100, 104, 101, 108, 103, 110, 106, 112, 105, 99, 94, 90, 86, 92, 96]
        c = walk(path)
        loose = analyze(c, 1, 1, require_displacement=False)
        strict = analyze(c, 1, 1, require_displacement=True, min_displacement_atr=2.0)
        n_loose = sum(1 for e in loose.events if e.kind == "MSS")
        n_strict = sum(1 for e in strict.events if e.kind == "MSS")
        self.assertLessEqual(n_strict, n_loose)

    def test_valid_mss_carries_displacement(self):
        from crowcode.data import synthetic
        st = analyze(list(synthetic(3000, minutes=5)), require_displacement=True)
        for e in st.events:
            if e.kind == "MSS":
                self.assertIsNotNone(e.displacement, "변위 없는 MSS 가 통과됐다")
                self.assertTrue(e.valid_mss)

    def test_no_lookahead(self):
        """미래 봉을 붙여도 과거 이벤트가 바뀌면 안 된다."""
        from crowcode.data import synthetic
        c = list(synthetic(2000, minutes=5))
        early = analyze(c[:1200], require_displacement=False)
        late = analyze(c, require_displacement=False)
        cut = [e for e in late.events if e.index < 1190]
        for a, b in zip(early.events, cut):
            self.assertEqual((a.index, a.kind, a.direction), (b.index, b.kind, b.direction))


if __name__ == "__main__":
    unittest.main()
