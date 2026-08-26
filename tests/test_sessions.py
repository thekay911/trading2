import unittest
from datetime import datetime, timezone

from crowcode.config import ASIA, LONDON, NEWYORK
from crowcode.sessions import NewsEvent, friday_close_block, in_session, news_blackout


def t(y=2024, m=1, d=8, h=0, mi=0):
    return datetime(y, m, d, h, mi, tzinfo=timezone.utc)


class TestSessions(unittest.TestCase):
    def test_london_and_newyork(self):
        self.assertEqual(in_session(t(h=8), (LONDON, NEWYORK)).name, "London")
        self.assertEqual(in_session(t(h=14), (LONDON, NEWYORK)).name, "NewYork")

    def test_asia_excluded_by_default_windows(self):
        self.assertIsNone(in_session(t(h=3), (LONDON, NEWYORK)))
        self.assertEqual(in_session(t(h=3), (ASIA,)).name, "Asia")

    def test_boundaries(self):
        self.assertIsNotNone(in_session(t(h=7, mi=0), (LONDON,)))
        self.assertIsNone(in_session(t(h=12, mi=0), (LONDON,)))

    def test_naive_datetime_treated_as_utc(self):
        self.assertIsNotNone(in_session(datetime(2024, 1, 8, 8, 0), (LONDON,)))


class TestNews(unittest.TestCase):
    def setUp(self):
        self.nfp = [NewsEvent(t(h=12, mi=30), "NFP", "high")]

    def test_blocked_before_and_after(self):
        self.assertIsNotNone(news_blackout(t(h=12, mi=20), self.nfp, 15, 30))
        self.assertIsNotNone(news_blackout(t(h=12, mi=55), self.nfp, 15, 30))

    def test_clear_outside_window(self):
        self.assertIsNone(news_blackout(t(h=11, mi=0), self.nfp, 15, 30))
        self.assertIsNone(news_blackout(t(h=13, mi=30), self.nfp, 15, 30))

    def test_low_impact_ignored(self):
        low = [NewsEvent(t(h=12, mi=30), "소매판매", "low")]
        self.assertIsNone(news_blackout(t(h=12, mi=30), low, 15, 30))


class TestFriday(unittest.TestCase):
    def test_friday_evening_blocked(self):
        self.assertTrue(friday_close_block(t(2024, 1, 12, 20)))   # 금요일

    def test_friday_morning_open(self):
        self.assertFalse(friday_close_block(t(2024, 1, 12, 9)))

    def test_other_weekday_open(self):
        self.assertFalse(friday_close_block(t(2024, 1, 11, 20)))


if __name__ == "__main__":
    unittest.main()
