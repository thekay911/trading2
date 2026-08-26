import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from crowcode.data import Candle, MTFView, Series, atr, load_csv, resample, synthetic, tf_minutes

T0 = datetime(2024, 1, 8, 0, 0, tzinfo=timezone.utc)


class TestResample(unittest.TestCase):
    def setUp(self):
        self.m1 = Series(
            [Candle(T0 + timedelta(minutes=i), 100 + i, 100 + i + 1, 100 + i - 1, 100 + i + 0.5)
             for i in range(60)],
            "X", "M1",
        )

    def test_bucket_count(self):
        self.assertEqual(len(resample(self.m1, "M15")), 4)
        self.assertEqual(len(resample(self.m1, "M5")), 12)

    def test_ohlc_aggregation(self):
        m5 = resample(self.m1, "M5")
        first = m5[0]
        src = list(self.m1)[:5]
        self.assertEqual(first.open, src[0].open)
        self.assertEqual(first.close, src[-1].close)
        self.assertEqual(first.high, max(c.high for c in src))
        self.assertEqual(first.low, min(c.low for c in src))

    def test_unknown_timeframe(self):
        with self.assertRaises(ValueError):
            tf_minutes("M7")


class TestMTFView(unittest.TestCase):
    def setUp(self):
        self.base = synthetic(600)

    def test_only_closed_bars_returned(self):
        view = MTFView(self.base, ["M15", "M5"])
        ts = self.base[100].ts                      # M1 100번째 봉 마감 시각
        m15 = view.slice("M15", ts)
        for c in m15:
            self.assertLessEqual(c.ts.timestamp() + 15 * 60, ts.timestamp() + 60)

    def test_slice_grows_monotonically(self):
        view = MTFView(self.base, ["M5"])
        a = len(view.slice("M5", self.base[100].ts, max_bars=1000))
        b = len(view.slice("M5", self.base[200].ts, max_bars=1000))
        self.assertGreater(b, a)

    def test_max_bars_caps_length(self):
        view = MTFView(self.base, ["M5"])
        self.assertLessEqual(len(view.slice("M5", self.base[-1].ts, max_bars=10)), 10)


class TestCsv(unittest.TestCase):
    def test_roundtrip(self):
        path = os.path.join(tempfile.mkdtemp(), "t.csv")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("time,open,high,low,close,volume\n")
            fh.write("2024-01-08T09:00:00Z,1950,1951,1949,1950.5,10\n")
            fh.write("2024-01-08T09:01:00Z,1950.5,1952,1950,1951.5,12\n")
        s = load_csv(path, "XAUUSD", "M1")
        self.assertEqual(len(s), 2)
        self.assertEqual(s[0].close, 1950.5)
        self.assertEqual(s[1].ts.tzinfo, timezone.utc)

    def test_unix_timestamps(self):
        path = os.path.join(tempfile.mkdtemp(), "u.csv")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("time,open,high,low,close\n1704704400,1,2,0.5,1.5\n")
        self.assertEqual(len(load_csv(path)), 1)


class TestAtr(unittest.TestCase):
    def test_positive_for_real_data(self):
        self.assertGreater(atr(list(synthetic(200))), 0)

    def test_zero_for_single_candle(self):
        self.assertEqual(atr([Candle(T0, 1, 1, 1, 1)]), 0.0)


class TestCandle(unittest.TestCase):
    def test_body_and_direction(self):
        c = Candle(T0, 100, 103, 99, 102)
        self.assertTrue(c.bullish)
        self.assertEqual(c.body, 2)
        self.assertEqual(c.range, 4)
        self.assertEqual(c.body_top, 102)
        self.assertEqual(c.body_bottom, 100)


if __name__ == "__main__":
    unittest.main()
