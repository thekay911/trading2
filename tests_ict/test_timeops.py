import unittest
from datetime import datetime, timezone

from ict.timeops import (
    ASIAN_RANGE, LONDON_KZ, NY_AM_KZ, SILVER_BULLET_AM,
    active_windows, in_killzone, in_macro, ny_clock, ny_offset_hours,
    session_of, to_ny,
)


def utc(iso: str) -> datetime:
    return datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)


class TestDaylightSaving(unittest.TestCase):
    """ICT 는 전부 뉴욕 시간이다. 서머타임을 틀리면 킬존이 통째로 밀린다."""

    def test_winter_is_est(self):
        self.assertEqual(ny_offset_hours(utc("2024-01-15T12:00:00")), -5)

    def test_summer_is_edt(self):
        self.assertEqual(ny_offset_hours(utc("2024-07-15T12:00:00")), -4)

    def test_march_transition(self):
        self.assertEqual(ny_offset_hours(utc("2024-03-09T12:00:00")), -5)
        self.assertEqual(ny_offset_hours(utc("2024-03-11T12:00:00")), -4)

    def test_november_transition(self):
        self.assertEqual(ny_offset_hours(utc("2024-11-01T12:00:00")), -4)
        self.assertEqual(ny_offset_hours(utc("2024-11-05T12:00:00")), -5)

    def test_same_ny_hour_different_utc(self):
        """겨울 15:00Z 와 여름 14:00Z 는 둘 다 뉴욕 10시다."""
        self.assertAlmostEqual(ny_clock(utc("2024-01-15T15:00:00")), 10.0)
        self.assertAlmostEqual(ny_clock(utc("2024-07-15T14:00:00")), 10.0)


class TestKillzones(unittest.TestCase):
    def test_london_killzone(self):
        self.assertEqual(in_killzone(utc("2024-01-15T07:30:00")).name, "LondonKZ")

    def test_silver_bullet_am(self):
        self.assertIn("SilverBulletAM", active_windows(utc("2024-01-15T15:30:00")))

    def test_silver_bullet_overlaps_london_close(self):
        w = active_windows(utc("2024-01-15T15:30:00"))
        self.assertIn("SilverBulletAM", w)
        self.assertIn("LondonCloseKZ", w)

    def test_outside_any_killzone(self):
        self.assertIsNone(in_killzone(utc("2024-01-15T05:00:00")))   # NY 자정

    def test_asian_range_crosses_midnight(self):
        self.assertTrue(ASIAN_RANGE.contains(utc("2024-01-16T02:00:00")))   # NY 21:00
        self.assertTrue(ASIAN_RANGE.contains(utc("2024-01-16T04:30:00")))   # NY 23:30

    def test_boundaries_are_half_open(self):
        self.assertTrue(LONDON_KZ.contains(utc("2024-01-15T07:00:00")))     # NY 02:00
        self.assertFalse(LONDON_KZ.contains(utc("2024-01-15T10:00:00")))    # NY 05:00


class TestMacroAndSession(unittest.TestCase):
    def test_macro_window(self):
        self.assertTrue(in_macro(utc("2024-01-15T15:55:00")))    # :55
        self.assertTrue(in_macro(utc("2024-01-15T16:05:00")))    # :05
        self.assertFalse(in_macro(utc("2024-01-15T16:30:00")))   # :30

    def test_sessions(self):
        self.assertEqual(session_of(utc("2024-01-15T07:30:00")), "london")
        self.assertEqual(session_of(utc("2024-01-15T14:00:00")), "ny_am")
        self.assertEqual(session_of(utc("2024-01-15T19:00:00")), "ny_pm")


if __name__ == "__main__":
    unittest.main()
