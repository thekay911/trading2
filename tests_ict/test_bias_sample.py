"""일일 편향(DOL) 과 금 형태의 표본 생성기.

편향은 ICT 2022 모델의 1단계다. 여기가 틀리면 그 뒤 네 단계가 전부
반대 방향을 향한다.

표본 생성기는 전략이 아니라 시험대다. 세션 구조(런던·뉴욕이 크고
롤오버가 죽어 있음)가 없는 난수 시계열에서는 킬존 모델을 검증할 수
없다 — 킬존이 다른 시간과 구별되지 않기 때문이다.
"""

import statistics
import unittest
from datetime import datetime, timezone

from ict.bias import Bias, evaluate, midnight_open
from ict.sample import gold
from ict.structure import BEAR, BULL
from ict.timeops import ny_clock, ny_date, to_ny


class TestMidnightOpen(unittest.TestCase):
    def test_is_the_first_bar_of_the_ny_day(self):
        bars = list(gold(days=6, seed=2))
        day = ny_date(bars[len(bars) // 2].ts)
        same = [c for c in bars if ny_date(c.ts) == day]
        self.assertAlmostEqual(midnight_open(bars, day), same[0].open)
        self.assertLess(ny_clock(same[0].ts), 0.2, "그날 첫 봉이 자정이 아니다")

    def test_missing_day_gives_none(self):
        bars = list(gold(days=3, seed=2))
        self.assertIsNone(midnight_open(bars, ny_date(bars[0].ts).replace(year=1999)))


class TestBias(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bars = list(gold(days=30, seed=4))

    def test_short_history_is_neutral_not_a_crash(self):
        b = evaluate(self.bars[:20], 19)
        self.assertEqual(b.lean, "neutral")
        self.assertIsNone(b.direction)

    def test_lean_matches_direction(self):
        for now in range(500, len(self.bars), 700):
            b = evaluate(self.bars, now)
            self.assertEqual(b.direction, {"bullish": BULL, "bearish": BEAR,
                                           "neutral": None}[b.lean])

    def test_side_maps_to_an_order_side(self):
        for now in range(500, len(self.bars), 700):
            b = evaluate(self.bars, now)
            self.assertIn(b.side, ("buy", "sell", None))
            self.assertEqual(b.side is None, b.direction is None)

    def test_uses_only_bars_up_to_now(self):
        """편향이 미래를 보면 백테스트 전체가 무의미해진다."""
        for now in (900, 3000, 6000):
            full = evaluate(self.bars, now)
            cut = evaluate(self.bars[:now + 1], now)
            self.assertEqual((full.lean, round(full.score, 6)),
                             (cut.lean, round(cut.score, 6)), f"봉 {now}")

    def test_price_is_the_current_close(self):
        b = evaluate(self.bars, 3000)
        self.assertAlmostEqual(b.price, self.bars[3000].close)

    def test_target_lies_in_the_leaning_direction(self):
        for now in range(600, len(self.bars), 173):
            b = evaluate(self.bars, now)
            if b.target is None:
                continue
            if b.direction == BULL:
                self.assertGreater(b.target.price, b.price)
                self.assertEqual(b.target.kind, "BSL")
            else:
                self.assertLess(b.target.price, b.price)
                self.assertEqual(b.target.kind, "SSL")

    def test_neutral_has_no_target(self):
        for now in range(600, len(self.bars), 173):
            b = evaluate(self.bars, now)
            if b.lean == "neutral":
                self.assertIsNone(b.target)

    def test_every_lean_is_backed_by_a_written_reason(self):
        for now in range(600, len(self.bars), 173):
            b = evaluate(self.bars, now)
            if b.lean != "neutral":
                self.assertTrue(b.reasons, b.describe())

    def test_describe_renders(self):
        text = evaluate(self.bars, 3000).describe()
        self.assertIn("현재", text)
        self.assertIn("점수", text)

    def test_scores_are_not_all_identical(self):
        """항상 같은 점수가 나오면 편향 엔진이 아무 일도 안 하는 것이다."""
        scores = {evaluate(self.bars, n).score for n in range(600, 8000, 400)}
        self.assertGreater(len(scores), 2, f"점수 종류 {scores}")


class TestSample(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bars = list(gold(days=40, price=3300.0, daily_range=32.0, seed=9))

    def test_bar_count_and_spacing(self):
        self.assertEqual(len(self.bars), 40 * 288)
        gap = self.bars[1].ts - self.bars[0].ts
        self.assertEqual(gap.total_seconds(), 300)

    def test_ohlc_is_internally_consistent(self):
        for c in self.bars:
            self.assertGreaterEqual(c.high, max(c.open, c.close))
            self.assertLessEqual(c.low, min(c.open, c.close))
            self.assertGreater(c.low, 0)

    def test_no_gaps_between_bars(self):
        for a, b in zip(self.bars, self.bars[1:]):
            self.assertAlmostEqual(a.close, b.open, places=6)

    def test_price_stays_in_a_plausible_gold_range(self):
        lo = min(c.low for c in self.bars)
        hi = max(c.high for c in self.bars)
        self.assertGreater(lo, 3300.0 * 0.7)
        self.assertLess(hi, 3300.0 * 1.3)

    def test_daily_range_is_in_the_right_ballpark(self):
        """금은 $3,300 대에서 하루 $30~45 정도 움직인다."""
        by_day = {}
        for c in self.bars:
            d = ny_date(c.ts)
            hi, lo = by_day.get(d, (c.high, c.low))
            by_day[d] = (max(hi, c.high), min(lo, c.low))
        med = statistics.median(hi - lo for hi, lo in by_day.values())
        self.assertGreater(med, 18.0, f"일중 변동폭 중앙값 {med:.1f} — 너무 작다")
        self.assertLess(med, 60.0, f"일중 변동폭 중앙값 {med:.1f} — 너무 크다")

    def test_killzones_are_more_volatile_than_the_rollover(self):
        """이게 깨지면 킬존 모델을 검증할 표본이 아니다."""
        buckets = {}
        for c in self.bars:
            buckets.setdefault(int(ny_clock(c.ts)), []).append(c.high - c.low)
        avg = {h: sum(v) / len(v) for h, v in buckets.items()}
        london = statistics.mean(avg[h] for h in (2, 3, 4))
        ny_am = statistics.mean(avg[h] for h in (7, 8, 9))
        rollover = statistics.mean(avg[h] for h in (17, 18, 19))
        self.assertGreater(london, rollover * 3, f"런던 {london:.2f} vs 롤오버 {rollover:.2f}")
        self.assertGreater(ny_am, rollover * 3, f"뉴욕 {ny_am:.2f} vs 롤오버 {rollover:.2f}")
        self.assertGreater(ny_am, avg[22], "뉴욕 오전이 아시아보다 조용하다")

    def test_same_seed_same_series(self):
        a = list(gold(days=3, seed=1))
        b = list(gold(days=3, seed=1))
        self.assertEqual([c.close for c in a], [c.close for c in b])

    def test_different_seed_different_series(self):
        a = list(gold(days=3, seed=1))
        b = list(gold(days=3, seed=2))
        self.assertNotEqual([c.close for c in a], [c.close for c in b])

    def test_starts_at_ny_midnight(self):
        self.assertLess(ny_clock(self.bars[0].ts), 0.2)

    def test_price_level_is_configurable(self):
        cheap = list(gold(days=3, price=1300.0, daily_range=13.0, seed=1))
        self.assertLess(max(c.high for c in cheap), 1300.0 * 1.2)
        self.assertGreater(min(c.low for c in cheap), 1300.0 * 0.8)

    def test_timestamps_are_utc_aware(self):
        for c in self.bars[:5]:
            self.assertIsNotNone(c.ts.tzinfo)
            self.assertEqual(to_ny(c.ts).utcoffset().total_seconds() % 3600, 0)


if __name__ == "__main__":
    unittest.main()
