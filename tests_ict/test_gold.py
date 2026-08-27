"""XAUUSD 보정 계층.

핵심은 하나다: 2018년 금은 $1,300, 지금은 $3,300 이다. 달러로 고정한
임계값은 어느 한쪽에서 반드시 틀린다. 여기 있는 값은 전부 ATR 배수이거나
bp(가격의 1/10,000) 라서 가격대를 따라간다.
"""

import unittest
from datetime import datetime, timedelta, timezone

from ict.gold import (PROFILES, RAW, STANDARD, STRICT, GoldProfile, Volatility,
                      bp_to_price, describe, money, pips, price_to_bp, profile)
from ict.sample import gold


def v(price: float, atr: float) -> Volatility:
    return Volatility(price=price, atr=atr, atr_bp=price_to_bp(atr, price))


class TestUnits(unittest.TestCase):
    def test_bp_round_trips(self):
        self.assertAlmostEqual(bp_to_price(10.0, 3300.0), 3.30)
        self.assertAlmostEqual(price_to_bp(3.30, 3300.0), 10.0)

    def test_one_pip_is_ten_cents(self):
        """엑스네스 금 호가 기준: 1핍 = $0.10."""
        self.assertAlmostEqual(pips(1.0), 10.0)
        self.assertAlmostEqual(pips(2.5), 25.0)

    def test_money_matches_the_users_arithmetic(self):
        """0.01랏, 25핍($2.50) 손절 = $2.50 리스크 → $100 계좌의 2.5%."""
        self.assertAlmostEqual(money(0.01, 2.50), 2.50)
        self.assertAlmostEqual(money(0.01, 2.00), 2.00)   # 20핍 = $2
        self.assertAlmostEqual(money(0.01, 6.00), 6.00)   # 1:3 → 60핍 = $6
        self.assertAlmostEqual(money(1.0, 1.0), 100.0)    # 1랏 = 100온스

    def test_measure_reads_a_real_series(self):
        m = Volatility.measure(list(gold(days=5, price=3300.0)))
        self.assertGreater(m.atr, 0)
        self.assertAlmostEqual(m.atr_bp, price_to_bp(m.atr, m.price), places=6)


class TestScaling(unittest.TestCase):
    def test_scaled_takes_the_larger_of_atr_and_bp(self):
        low_vol = v(3300.0, 0.10)          # 죽은 구간 → bp 바닥이 이긴다
        self.assertAlmostEqual(low_vol.scaled(1.5, 8.0), bp_to_price(8.0, 3300.0))
        high_vol = v(3300.0, 5.00)         # 뉴스 → ATR 이 이긴다
        self.assertAlmostEqual(high_vol.scaled(1.5, 8.0), 7.50)

    def test_thresholds_follow_the_price_level(self):
        """같은 프로파일이 2018년 금과 지금 금에서 다른 달러값을 낸다."""
        then = STANDARD.displacement(v(1300.0, 0.5))
        now = STANDARD.displacement(v(3300.0, 0.5))
        self.assertLess(then, now)
        self.assertAlmostEqual(now / then, 3300.0 / 1300.0, places=6)

    def test_nothing_is_a_fixed_dollar_amount(self):
        """달러 상수로 굳은 임계값이 하나라도 있으면 가격대가 바뀔 때 깨진다."""
        a, b = v(1300.0, 0.5), v(3300.0, 0.5)
        for name in ("displacement", "equal_tolerance", "stop_buffer"):
            f = getattr(STANDARD, name)
            self.assertNotAlmostEqual(f(a), f(b), msg=f"{name} 가 가격대를 안 따라간다")


class TestFvgFloor(unittest.TestCase):
    def test_min_fvg_never_drops_below_twice_the_spread(self):
        """스프레드보다 작은 갭은 갭이 아니라 호가 잡음이다."""
        dead = v(3300.0, 0.01)
        self.assertGreaterEqual(STANDARD.min_fvg(dead), STANDARD.spread * 2)

    def test_raw_account_allows_smaller_gaps(self):
        dead = v(3300.0, 0.01)
        self.assertLess(RAW.min_fvg(dead), STANDARD.min_fvg(dead))


class TestSpreadGate(unittest.TestCase):
    def test_tight_stops_are_rejected(self):
        """$0.25 스프레드가 손절폭의 15% 를 넘으면 통계가 스프레드에 먹힌다."""
        self.assertFalse(STANDARD.spread_ok(1.00))       # 25%
        self.assertTrue(STANDARD.spread_ok(2.00))        # 12.5%
        self.assertAlmostEqual(STANDARD.spread / STANDARD.max_spread_to_stop, 1.6667, places=3)

    def test_raw_spread_lets_tighter_stops_through(self):
        self.assertTrue(RAW.spread_ok(1.30))
        self.assertFalse(STANDARD.spread_ok(1.30))

    def test_disabled_gate_passes_everything(self):
        self.assertTrue(STANDARD.with_(max_spread_to_stop=0.0).spread_ok(0.05))


class TestRollover(unittest.TestCase):
    """뉴욕 17:00~20:00 은 금 스프레드가 벌어지고 유동성이 사라진다."""

    def _at(self, ny_hour: int, month: int = 1) -> datetime:
        # 1월 뉴욕은 UTC-5
        return datetime(2024, month, 15, (ny_hour + 5) % 24, 0, tzinfo=timezone.utc) + \
               (timedelta(days=1) if ny_hour + 5 >= 24 else timedelta(0))

    def test_rollover_hours_are_blocked(self):
        for h in (17, 18, 19):
            self.assertTrue(STANDARD.in_rollover(self._at(h)), f"NY {h}시")

    def test_trading_hours_are_not(self):
        for h in (2, 8, 10, 14, 20, 21):
            self.assertFalse(STANDARD.in_rollover(self._at(h)), f"NY {h}시")

    def test_can_be_switched_off(self):
        self.assertFalse(STANDARD.with_(avoid_rollover=False).in_rollover(self._at(18)))


class TestProfiles(unittest.TestCase):
    def test_lookup_by_name(self):
        self.assertIs(profile("standard"), STANDARD)
        self.assertIs(profile("raw"), RAW)
        self.assertIs(profile("strict"), STRICT)

    def test_unknown_name_says_what_is_available(self):
        with self.assertRaises(ValueError) as e:
            profile("없는프로파일")
        for name in PROFILES:
            self.assertIn(name, str(e.exception))

    def test_strict_demands_more_displacement(self):
        x = v(3300.0, 1.0)
        self.assertGreater(STRICT.displacement(x), STANDARD.displacement(x))

    def test_profiles_are_frozen(self):
        with self.assertRaises(Exception):
            STANDARD.spread = 0.99          # type: ignore[misc]

    def test_with_returns_a_copy(self):
        p = STANDARD.with_(spread=0.40)
        self.assertAlmostEqual(p.spread, 0.40)
        self.assertAlmostEqual(STANDARD.spread, 0.25)
        self.assertIsInstance(p, GoldProfile)

    def test_describe_renders_current_thresholds(self):
        text = describe(v(3300.0, 0.66), STANDARD)
        self.assertIn("금", text)
        self.assertIn("변위", text)


if __name__ == "__main__":
    unittest.main()
