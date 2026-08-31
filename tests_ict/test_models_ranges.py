"""딜링 레인지와, 엔진 위에서 나온 셋업의 무결성."""

import unittest

from ict.backtest import run
from ict.engine import Market
from ict.models import Config
from ict.ranges import OTE_END, OTE_START, DealingRange, leg_range
from ict.sample import gold
from ict.strategy import MODELS, scan
from ict.structure import BEAR, BULL
from ict.timeops import active_windows


class TestDealingRange(unittest.TestCase):
    def setUp(self):
        self.r = DealingRange(high=200.0, low=100.0, high_index=9, low_index=5)

    def test_equilibrium_is_the_midpoint(self):
        self.assertAlmostEqual(self.r.equilibrium, 150.0)

    def test_premium_and_discount(self):
        self.assertTrue(self.r.is_discount(120.0))
        self.assertFalse(self.r.is_premium(120.0))
        self.assertTrue(self.r.is_premium(180.0))

    def test_buy_ote_is_below_equilibrium(self):
        """매수 OTE 는 고점에서 62~79% 되돌린 자리 — 항상 균형점 아래다."""
        lo, hi = self.r.ote("buy")
        self.assertLess(hi, self.r.equilibrium)
        self.assertAlmostEqual(hi, 200.0 - 100.0 * OTE_START)
        self.assertAlmostEqual(lo, 200.0 - 100.0 * OTE_END)
        self.assertTrue(self.r.in_ote(130.0, "buy"))
        self.assertFalse(self.r.in_ote(160.0, "buy"))

    def test_sell_ote_is_above_equilibrium(self):
        lo, hi = self.r.ote("sell")
        self.assertGreater(lo, self.r.equilibrium)
        self.assertAlmostEqual(lo, 100.0 + 100.0 * OTE_START)
        self.assertAlmostEqual(hi, 100.0 + 100.0 * OTE_END)

    def test_projection_extends_beyond_the_range(self):
        self.assertAlmostEqual(self.r.projection(1.0, "buy"), 300.0)
        self.assertAlmostEqual(self.r.projection(1.0, "sell"), 0.0)

    def test_position_maps_the_range_to_zero_one(self):
        self.assertAlmostEqual(self.r.position(100.0), 0.0)
        self.assertAlmostEqual(self.r.position(200.0), 1.0)
        self.assertAlmostEqual(self.r.position(150.0), 0.5)

    def test_direction_from_which_extreme_came_last(self):
        self.assertEqual(DealingRange(200.0, 100.0, 9, 5).direction, BULL)
        self.assertEqual(DealingRange(200.0, 100.0, 5, 9).direction, BEAR)

    def test_leg_range_spans_the_displacement_leg(self):
        bars = list(gold(days=3, seed=1))
        r = leg_range(bars, 100, 140)
        self.assertIsNotNone(r)
        self.assertGreaterEqual(r.high, max(b.high for b in bars[100:141]) - 1e-9)
        self.assertLessEqual(r.low, min(b.low for b in bars[100:141]) + 1e-9)
        self.assertIsNone(leg_range(bars, 140, 100), "끝이 시작보다 앞이면 레인지가 없다")


class Fixture(unittest.TestCase):
    """엔진 한 번만 만들어 두고 전 테스트가 공유한다."""

    DAYS = 90

    @classmethod
    def setUpClass(cls):
        cls.candles = list(gold(days=cls.DAYS, seed=5))
        cls.market = Market.build(cls.candles)
        cls.cfg = Config()
        cls.setups = scan(cls.market, cls.cfg, models=list(MODELS))


class TestSetupIntegrity(Fixture):
    """셋업이 나왔다면 ICT 규칙을 전부 만족해야 한다."""

    def test_produces_setups(self):
        self.assertTrue(self.setups, "합성 금 데이터에서 셋업이 하나도 없다")

    def test_levels_are_ordered(self):
        for s in self.setups:
            if s.side == "buy":
                self.assertLess(s.stop, s.entry, s.describe())
                self.assertLess(s.entry, s.target, s.describe())
            else:
                self.assertGreater(s.stop, s.entry, s.describe())
                self.assertGreater(s.entry, s.target, s.describe())

    def test_min_rr_is_respected(self):
        for s in self.setups:
            self.assertGreaterEqual(s.rr, self.cfg.min_rr - 1e-9, s.describe())

    def test_max_rr_caps_the_target(self):
        for s in self.setups:
            self.assertLessEqual(s.rr, self.cfg.max_rr + 1e-9, s.describe())

    def test_every_setup_is_in_an_allowed_window(self):
        for s in self.setups:
            self.assertTrue(active_windows(s.ts), s.describe())

    def test_every_attached_mss_has_displacement(self):
        """변위 없는 MSS 는 ICT 기준으로 전환이 아니다 — 엔진이 애초에 안 준다."""
        for s in self.setups:
            if s.mss is not None:
                self.assertTrue(s.mss.valid_mss, s.describe())

    def test_raid_is_on_the_correct_side(self):
        for s in self.setups:
            if s.raid is None:
                continue
            if s.side == "buy":
                self.assertEqual(s.raid.pool.kind, "SSL", s.describe())
            else:
                self.assertEqual(s.raid.pool.kind, "BSL", s.describe())

    def test_ict2022_raid_precedes_the_mss(self):
        """2022 모델은 순서가 전부다: 습격 → MSS. (다른 모델은 MSS 를 문맥으로만 단다)"""
        for s in self.setups:
            if s.model == "ICT2022" and s.raid is not None and s.mss_index >= 0:
                self.assertLessEqual(s.raid.index, s.mss_index, s.describe())

    def test_entry_sits_inside_its_pd_array(self):
        for s in self.setups:
            self.assertGreaterEqual(s.entry, s.array.bottom - 1e-6, s.describe())
            self.assertLessEqual(s.entry, s.array.top + 1e-6, s.describe())

    def test_model_names_are_known(self):
        for s in self.setups:
            self.assertIn(s.model, MODELS)

    def test_setups_are_in_time_order(self):
        idx = [s.index for s in self.setups]
        self.assertEqual(idx, sorted(idx))

    #: 습격 극점이 손절 자리를 정하는 모델들. iFVG/CISD 는 습격을 문맥으로만
    #: 달고 손절은 각자의 배열(뚫린 갭, 되돌린 구간)에서 잡는다.
    RAID_STOP_MODELS = {"TurtleSoup", "ICT2022", "TJR", "SilverBullet"}

    def test_stop_clears_the_raid_extreme(self):
        for s in self.setups:
            if s.raid is None or s.model not in self.RAID_STOP_MODELS:
                continue
            if s.side == "buy":
                self.assertLessEqual(s.stop, s.raid.extreme, s.describe())
            else:
                self.assertGreaterEqual(s.stop, s.raid.extreme, s.describe())

    def test_spread_is_never_a_large_share_of_the_stop(self):
        p = self.market.gold
        for s in self.setups:
            self.assertTrue(p.spread_ok(s.risk, self.candles[s.index].close),
                            s.describe())


class TestGatesActuallyGate(Fixture):
    DAYS = 60

    def _count(self, **kw):
        return len(scan(self.market, Config(**kw), models=list(MODELS)))

    def test_killzone_filter_reduces_setups(self):
        self.assertLessEqual(self._count(require_killzone=True),
                             self._count(require_killzone=False))

    def test_raid_requirement_is_enforced_on_the_2022_model(self):
        """require_raid 는 단순 필터가 아니다 — 손절 기준점도 습격 극점으로 바꾼다.
        그래서 셋업 수는 단조롭지 않고, 검증할 건 규칙 자체다."""
        on = [s for s in scan(self.market, Config(require_raid=True),
                              models=["ICT2022"])]
        self.assertTrue(on)
        for s in on:
            self.assertIsNotNone(s.raid, s.describe())
            self.assertLessEqual(s.raid.index, s.mss_index, s.describe())
        off = [s for s in scan(self.market, Config(require_raid=False),
                               models=["ICT2022"])]
        self.assertTrue(any(s.raid is None for s in off),
                        "require_raid=False 인데 전부 습격을 달고 있다")

    def test_premium_discount_filter_reduces_setups(self):
        self.assertLessEqual(self._count(require_discount_premium=True),
                             self._count(require_discount_premium=False))

    def test_higher_min_rr_reduces_setups(self):
        self.assertLessEqual(self._count(min_rr=5.0), self._count(min_rr=1.5))

    def test_model_selection_is_a_subset(self):
        one = scan(self.market, self.cfg, models=["SilverBullet"])
        self.assertTrue(all(s.model == "SilverBullet" for s in one))
        self.assertLessEqual(len(one), len(self.setups))

    def test_no_model_list_means_the_active_default(self):
        """models 를 안 주면 실측에서 살아남은 모델만 돈다."""
        from ict.plays import ACTIVE
        got = scan(self.market, self.cfg, models=None)
        self.assertEqual({s.model for s in got} - set(ACTIVE), set())


class TestNoLookahead(unittest.TestCase):
    """잘린 시계열에서 같은 셋업이 나와야 한다 — 미래를 안 본다는 뜻."""

    def test_truncated_history_gives_the_same_setup(self):
        candles = list(gold(days=40, seed=3))
        cfg = Config()
        full = scan(Market.build(candles), cfg, models=list(MODELS))
        self.assertTrue(full, "비교할 셋업이 없다")
        s = full[len(full) // 2]
        cut = scan(Market.build(candles[:s.index + 1]), cfg,
                   models=list(MODELS), start=300)
        match = [x for x in cut if x.index == s.index and x.model == s.model]
        self.assertTrue(match, f"잘린 시계열에서 셋업이 사라짐 → 룩어헤드\n{s.describe()}")
        self.assertAlmostEqual(s.entry, match[0].entry, places=6)
        self.assertAlmostEqual(s.stop, match[0].stop, places=6)
        self.assertAlmostEqual(s.target, match[0].target, places=6)


class TestBacktest(Fixture):
    DAYS = 60

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.res = run(cls.candles, cls.cfg, spread=0.25, setups=cls.setups)

    def test_runs(self):
        self.assertGreaterEqual(self.res.setups, 0)
        self.assertLessEqual(self.res.n, self.res.setups)

    def test_losses_are_about_minus_one_r(self):
        for t in self.res.trades:
            if t.outcome == "stop":
                self.assertLess(t.r, 0)
                self.assertGreater(t.r, -1.6)   # 스프레드 때문에 -1 을 조금 넘는다

    def test_wins_pay_the_plays_target_not_the_liquidity_target(self):
        """목표는 계획의 R 이다. 셋업이 들고 있는 유동성 목표로 자르지 않는다 —
        21년 실측에서 1~1.5R 로 짧게 자르면 전 모델이 음수가 됐다."""
        from ict.plays import PLAYS
        for t in self.res.trades:
            if t.outcome == "target":
                want = PLAYS[t.setup.model].target_rr
                self.assertAlmostEqual(t.r, want, delta=0.5, msg=t.setup.describe())

    def test_exit_never_precedes_entry(self):
        for t in self.res.trades:
            self.assertGreater(t.exit_index, t.setup.index)

    def test_report_renders(self):
        text = self.res.report()
        self.assertIn("승률", text)

    def test_run_without_setups_builds_its_own_engine(self):
        r = run(self.candles[:6000], self.cfg, spread=0.25)
        self.assertGreaterEqual(r.setups, 0)


if __name__ == "__main__":
    unittest.main()
