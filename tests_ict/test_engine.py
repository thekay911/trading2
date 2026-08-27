"""사전계산 엔진.

Market 은 전 구간을 한 번 훑어 두고 조회만 하는 구조다. 속도 때문에
그렇게 만들었지만, 속도를 얻자고 미래를 보면 백테스트 숫자는 전부
거짓말이 된다. 여기서 검증하는 건 두 가지뿐이다.

  1. 사전계산 결과가 그 시점까지의 데이터만으로 나온 것과 같은가
  2. 캐시가 하루 경계·풀 소진을 제대로 처리하는가
"""

import unittest
from datetime import date

from ict import liquidity as liq
from ict.engine import Market
from ict.gold import STANDARD, STRICT
from ict.sample import gold
from ict.structure import BEAR, BULL
from ict.timeops import ny_date


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bars = list(gold(days=20, seed=7))
        cls.m = Market.build(cls.bars)


class TestBuild(Base):
    def test_length_matches_the_series(self):
        self.assertEqual(len(self.m), len(self.bars))

    def test_atr_is_positive_after_warmup(self):
        for i in range(50, len(self.m), 97):
            self.assertGreater(self.m.atr_at(i), 0.0)

    def test_every_mss_carries_displacement(self):
        """엔진은 require_displacement=True 로 만든다 — 변위 없는 MSS 는 없다."""
        for e in self.m.structure.events:
            if e.kind == "MSS":
                self.assertIsNotNone(e.displacement, f"{e.ts} 변위 없는 MSS")

    def test_volatility_tracks_the_local_price(self):
        v = self.m.volatility(500)
        self.assertAlmostEqual(v.price, self.bars[500].close)
        self.assertAlmostEqual(v.atr, self.m.atr_at(500))

    def test_daily_index_covers_every_bar(self):
        self.assertEqual(len(self.m._day_of_bar), len(self.bars))
        for i in (0, 500, len(self.bars) - 1):
            self.assertEqual(self.m._day_of_bar[i], ny_date(self.bars[i].ts))

    def test_profile_is_carried(self):
        self.assertIs(Market.build(self.bars[:2000], gold=STRICT).gold, STRICT)


class TestNoLookahead(Base):
    """같은 질문을 잘린 시계열에 던지면 같은 답이 나와야 한다."""

    CUTS = (1200, 2500, 4000)

    def test_last_mss_is_identical_on_truncated_history(self):
        for now in self.CUTS:
            cut = Market.build(self.bars[:now + 1])
            a = self.m.last_mss(now, 200)
            b = cut.last_mss(now, 200)
            self.assertEqual(a is None, b is None, f"봉 {now}")
            if a:
                self.assertEqual((a.index, a.direction, a.level),
                                 (b.index, b.direction, b.level), f"봉 {now}")

    def test_pools_are_identical_on_truncated_history(self):
        for now in self.CUTS:
            cut = Market.build(self.bars[:now + 1])
            a = [(p.kind, round(p.price, 6), p.label, p.taken_at)
                 for p in self.m.pools(now)]
            b = [(p.kind, round(p.price, 6), p.label, p.taken_at)
                 for p in cut.pools(now)]
            self.assertEqual(sorted(a), sorted(b), f"봉 {now}")

    def test_fresh_fvgs_are_identical_on_truncated_history(self):
        for now in self.CUTS:
            cut = Market.build(self.bars[:now + 1])
            for d in (BULL, BEAR):
                a = [(g.index, round(g.top, 6), round(g.bottom, 6))
                     for g in self.m.fresh_fvgs(now, d, now - 60)]
                b = [(g.index, round(g.top, 6), round(g.bottom, 6))
                     for g in cut.fresh_fvgs(now, d, now - 60)]
                self.assertEqual(a, b, f"봉 {now} {d}")

    def test_no_returned_object_is_dated_after_now(self):
        for now in self.CUTS:
            mss = self.m.last_mss(now, 500)
            if mss:
                self.assertLessEqual(mss.index, now)
            for p in self.m.pools(now):
                self.assertLessEqual(p.index, now)
                if p.taken_at is not None:
                    self.assertLessEqual(p.taken_at, now)
            for g in self.m.fresh_fvgs(now, BULL, 0):
                self.assertLessEqual(g.index, now)


class TestPools(Base):
    def test_a_pool_is_untaken_before_it_is_taken(self):
        """같은 풀이 시간이 지나면 taken_at 을 얻는다 — 그 전엔 None 이어야 한다."""
        found = False
        for now in range(400, len(self.m), 50):
            for p in self.m.pools(now):
                if p.taken_at is None:
                    continue
                self.assertLessEqual(p.taken_at, now)
                before = [q for q in self.m.pools(p.taken_at - 1)
                          if q.label == p.label and abs(q.price - p.price) < 1e-9]
                if before:
                    self.assertIsNone(before[0].taken_at,
                                      f"{p.label} 가 뚫리기 전인데 이미 소진 표시")
                    found = True
            if found:
                break
        self.assertTrue(found, "검증할 소진 풀을 못 찾음")

    def test_pdh_and_pdl_come_from_the_previous_ny_day(self):
        now = 3000
        d = self.m._day_of_bar[now]
        k = self.m.day_order.index(d)
        prev = self.m.day_order[k - 1]
        hi, lo, _ = self.m.daily[prev]
        by = {p.label: p for p in self.m.pools(now)}
        self.assertAlmostEqual(by["PDH"].price, hi)
        self.assertAlmostEqual(by["PDL"].price, lo)

    def test_bsl_is_above_and_ssl_below_their_own_reference(self):
        by = {}
        for p in self.m.pools(3000):
            by.setdefault(p.kind, []).append(p)
        self.assertTrue(by.get("BSL"))
        self.assertTrue(by.get("SSL"))
        for p in by["BSL"]:
            self.assertIsInstance(p, liq.Pool)

    def test_cache_returns_the_same_levels_for_the_same_day(self):
        d = self.m._day_of_bar[3000]
        same = [i for i in range(3000, 3200) if self.m._day_of_bar[i] == d]
        base = {(p.label, round(p.price, 6)) for p in self.m.pools(same[0])}
        for i in same[::20]:
            self.assertEqual({(p.label, round(p.price, 6)) for p in self.m.pools(i)},
                             base)

    def test_levels_change_across_the_day_boundary(self):
        i = 3000
        d = self.m._day_of_bar[i]
        j = next(k for k in range(i, len(self.m)) if self.m._day_of_bar[k] != d)
        self.assertNotEqual({(p.label, round(p.price, 6)) for p in self.m.pools(i)},
                            {(p.label, round(p.price, 6)) for p in self.m.pools(j)})

    def test_first_day_has_no_previous_day_levels(self):
        labels = {p.label for p in self.m.pools(5)}
        self.assertNotIn("PDH", labels)


class TestFvgQueries(Base):
    def test_direction_is_respected(self):
        for g in self.m.fresh_fvgs(3000, BULL, 2800):
            self.assertEqual(g.direction, BULL)
        for g in self.m.fresh_fvgs(3000, BEAR, 2800):
            self.assertEqual(g.direction, BEAR)

    def test_since_bounds_the_window(self):
        for g in self.m.fresh_fvgs(3000, BULL, 2900):
            self.assertGreaterEqual(g.index, 2900)

    def test_returned_gaps_are_still_unmitigated(self):
        """CE(중간값) 를 이미 건드린 갭은 신선하지 않다."""
        now = 3000
        for g in self.m.fresh_fvgs(now, BULL, 2700):
            for c in self.bars[g.index + 1:now + 1]:
                self.assertGreater(c.low, g.mid,
                                   f"{g.index} 갭의 CE 를 이미 건드렸다")

    def test_limit_is_honoured(self):
        self.assertLessEqual(len(self.m.fresh_fvgs(3000, BULL, 0, limit=2)), 2)

    def test_every_gap_clears_the_gold_floor(self):
        for g in self.m.fvgs:
            v = self.m.volatility(g.index)
            self.assertGreaterEqual(g.top - g.bottom,
                                    STANDARD.min_fvg(v) - 1e-9)


if __name__ == "__main__":
    unittest.main()
