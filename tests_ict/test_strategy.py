"""모델별 규칙.

각 모델은 ICT 가 말한 조건을 그대로 코드로 옮긴 것이다. 여기서 보는 건
"셋업이 나왔다"가 아니라 "나온 셋업이 그 모델의 정의를 어겼는가" 다.
정의를 어긴 셋업은 이름만 ICT 인 다른 전략이다.
"""

import unittest

from ict.engine import Market
from ict.gold import STANDARD
from ict.models import Config
from ict.sample import gold
from ict.strategy import (MODELS, ict2022, judas_swing, ote, scan,
                          silver_bullet, turtle_soup, unicorn)
from ict.structure import BEAR, BULL
from ict.timeops import (SILVER_BULLET_AM, SILVER_BULLET_PM, ny_clock, ny_date)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bars = list(gold(days=90, seed=5))
        cls.m = Market.build(cls.bars)
        cls.cfg = Config()
        # 모델별 규칙을 검증하는 파일이므로 기본 실행 목록이 아니라 전부 돌린다
        cls.setups = scan(cls.m, cls.cfg, models=list(MODELS))
        cls.by_model = {}
        for s in cls.setups:
            cls.by_model.setdefault(s.model, []).append(s)

    def model(self, name):
        got = self.by_model.get(name, [])
        if not got:
            self.skipTest(f"{name} 셋업이 이 표본에 없다")
        return got


class TestCoverage(Base):
    def test_all_six_models_are_registered(self):
        self.assertEqual(set(MODELS), {"ICT2022", "SilverBullet", "TurtleSoup",
                                       "JudasSwing", "OTE", "Unicorn"})

    def test_the_sample_exercises_most_models(self):
        """한두 모델만 도는 표본으로는 아무것도 검증 못 한다."""
        self.assertGreaterEqual(len(self.by_model), 4,
                                f"돌아간 모델: {sorted(self.by_model)}")


class TestSharedRules(Base):
    def test_never_trades_the_rollover(self):
        """뉴욕 17~20시는 금 스프레드가 벌어지고 호가가 사라진다."""
        for s in self.setups:
            self.assertFalse(STANDARD.in_rollover(s.ts), s.describe())

    def test_stop_is_on_the_losing_side_of_entry(self):
        for s in self.setups:
            if s.side == "buy":
                self.assertLess(s.stop, s.entry, s.describe())
            else:
                self.assertGreater(s.stop, s.entry, s.describe())

    def test_target_pool_matches_the_stated_target(self):
        for s in self.setups:
            if s.target_pool is not None:
                self.assertAlmostEqual(s.target, s.target_pool.price, places=6,
                                       msg=s.describe())

    def test_target_pool_is_on_the_right_side(self):
        for s in self.setups:
            if s.target_pool is None:
                continue
            if s.side == "buy":
                self.assertEqual(s.target_pool.kind, "BSL", s.describe())
                self.assertGreater(s.target_pool.price, s.entry, s.describe())
            else:
                self.assertEqual(s.target_pool.kind, "SSL", s.describe())
                self.assertLess(s.target_pool.price, s.entry, s.describe())

    def test_entry_is_never_absurdly_far_from_price(self):
        for s in self.setups:
            v = self.m.volatility(s.index)
            gap = abs(s.entry - self.bars[s.index].close)
            self.assertLessEqual(gap, v.atr * STANDARD.max_entry_distance_atr + 1e-6,
                                 s.describe())

    def test_every_setup_has_a_reason_written_down(self):
        for s in self.setups:
            self.assertTrue(s.notes, s.describe())
            self.assertIn("진입", s.describe())


class TestIct2022(Base):
    """1) 편향 2) 습격 3) 변위 동반 MSS 4) PD Array 복귀 5) 반대편 유동성."""

    def test_side_follows_the_mss(self):
        for s in self.model("ICT2022"):
            want = "buy" if s.mss.direction == BULL else "sell"
            self.assertEqual(s.side, want, s.describe())

    def test_entry_is_the_consequent_encroachment(self):
        """ICT 는 FVG 의 50%(CE) 에 지정가를 건다."""
        for s in self.model("ICT2022"):
            self.assertAlmostEqual(s.entry, s.array.mid, places=6, msg=s.describe())

    def test_entry_is_a_limit_order_not_a_chase(self):
        """매수는 종가보다 아래, 매도는 위 — 되돌림을 기다린다."""
        for s in self.model("ICT2022"):
            c = self.bars[s.index].close
            if s.side == "buy":
                self.assertLessEqual(s.entry, c, s.describe())
            else:
                self.assertGreaterEqual(s.entry, c, s.describe())

    def test_the_fvg_came_from_the_displacement(self):
        for s in self.model("ICT2022"):
            d = s.mss.displacement
            self.assertIsNotNone(d, s.describe())
            self.assertGreaterEqual(s.array.index, d.start, s.describe())


class TestSilverBullet(Base):
    """뉴욕 10~11시 창 안에서 생긴 FVG 만 쓴다."""

    def test_only_fires_in_a_silver_bullet_window(self):
        for s in self.model("SilverBullet"):
            self.assertTrue(SILVER_BULLET_AM.contains(s.ts) or
                            SILVER_BULLET_PM.contains(s.ts), s.describe())

    def test_the_gap_was_made_inside_the_window(self):
        for s in self.model("SilverBullet"):
            self.assertTrue(SILVER_BULLET_AM.contains(self.bars[s.array.index].ts) or
                            SILVER_BULLET_PM.contains(self.bars[s.array.index].ts),
                            s.describe())

    def test_the_gap_is_same_day_as_the_entry(self):
        for s in self.model("SilverBullet"):
            self.assertEqual(ny_date(s.ts), ny_date(self.bars[s.array.index].ts),
                             s.describe())

    def test_direct_call_outside_the_window_returns_nothing(self):
        off = [i for i in range(400, len(self.m))
               if not SILVER_BULLET_AM.contains(self.bars[i].ts)
               and not SILVER_BULLET_PM.contains(self.bars[i].ts)]
        for i in off[::311]:
            self.assertIsNone(silver_bullet(self.m, i, self.cfg))


class TestTurtleSoup(Base):
    """가짜 돌파: 풀을 뚫고 그 안으로 종가가 되돌아오면 반대로 간다."""

    def test_raid_closed_back_inside(self):
        for s in self.model("TurtleSoup"):
            self.assertIsNotNone(s.raid, s.describe())
            self.assertTrue(s.raid.closed_back, s.describe())

    def test_entry_is_the_swept_level_itself(self):
        for s in self.model("TurtleSoup"):
            self.assertAlmostEqual(s.entry, s.raid.pool.price, places=6,
                                   msg=s.describe())

    def test_stop_is_beyond_the_wick(self):
        for s in self.model("TurtleSoup"):
            if s.side == "buy":
                self.assertLess(s.stop, s.raid.extreme, s.describe())
            else:
                self.assertGreater(s.stop, s.raid.extreme, s.describe())

    def test_direction_opposes_the_raid(self):
        for s in self.model("TurtleSoup"):
            if s.side == "buy":
                self.assertEqual(s.raid.pool.kind, "SSL", s.describe())
            else:
                self.assertEqual(s.raid.pool.kind, "BSL", s.describe())


class TestJudasSwing(Base):
    """PO3: 아시아 레인지를 한쪽으로 털고(조작) 반대로 간다(분배)."""

    def test_only_fires_in_the_manipulation_window(self):
        for s in self.model("JudasSwing"):
            self.assertTrue(2.0 <= ny_clock(s.ts) < 5.0, s.describe())

    def test_stop_is_beyond_the_manipulation_extreme(self):
        for s in self.model("JudasSwing"):
            ar = self.m._asia_cache[ny_date(s.ts)]
            hi, lo = ar
            if s.side == "sell":
                self.assertGreater(s.stop, hi, s.describe())
            else:
                self.assertLess(s.stop, lo, s.describe())

    def test_mss_agrees_with_the_reversal_direction(self):
        for s in self.model("JudasSwing"):
            want = BULL if s.side == "buy" else BEAR
            self.assertEqual(s.mss.direction, want, s.describe())

    def test_only_one_side_of_the_asian_range_was_swept(self):
        for s in self.model("JudasSwing"):
            hi, lo = self.m._asia_cache[ny_date(s.ts)]
            since = [c for c in self.bars[max(0, s.index - 60):s.index + 1]
                     if ny_date(c.ts) == ny_date(s.ts) and ny_clock(c.ts) >= 2.0]
            swept_hi = max(c.high for c in since) > hi
            swept_lo = min(c.low for c in since) < lo
            self.assertNotEqual(swept_hi, swept_lo, s.describe())


class TestOte(Base):
    """변위 레그의 62~79% 되돌림."""

    def test_entry_is_inside_the_ote_band(self):
        for s in self.model("OTE"):
            lo, hi = s.array.bottom, s.array.top
            self.assertGreaterEqual(s.entry, lo - 1e-6, s.describe())
            self.assertLessEqual(s.entry, hi + 1e-6, s.describe())

    def test_entry_is_the_market_close_not_a_limit(self):
        """OTE 는 이미 그 구간에 와 있을 때 잡는 모델이다."""
        for s in self.model("OTE"):
            self.assertAlmostEqual(s.entry, self.bars[s.index].close, places=6,
                                   msg=s.describe())

    def test_buy_ote_sits_in_discount(self):
        for s in self.model("OTE"):
            d = s.mss.displacement
            from ict.ranges import leg_range
            dr = leg_range(self.bars, d.start, d.end)
            if s.side == "buy":
                self.assertTrue(dr.is_discount(s.entry), s.describe())
            else:
                self.assertTrue(dr.is_premium(s.entry), s.describe())

    def test_stop_is_beyond_the_leg_extreme(self):
        for s in self.model("OTE"):
            from ict.ranges import leg_range
            d = s.mss.displacement
            dr = leg_range(self.bars, d.start, d.end)
            if s.side == "buy":
                self.assertLess(s.stop, dr.low, s.describe())
            else:
                self.assertGreater(s.stop, dr.high, s.describe())


class TestUnicorn(Base):
    """브레이커와 FVG 가 겹치는 자리."""

    def test_array_is_an_overlap(self):
        for s in self.model("Unicorn"):
            self.assertGreater(s.array.top, s.array.bottom, s.describe())

    def test_entry_is_the_overlap_midpoint(self):
        for s in self.model("Unicorn"):
            self.assertAlmostEqual(s.entry, s.array.mid, places=6, msg=s.describe())


class TestScan(Base):
    def test_cooldown_spaces_out_same_model_setups(self):
        cd = 12
        got = scan(self.m, self.cfg, cooldown=cd)
        last = {}
        for s in got:
            if s.model in last:
                self.assertGreaterEqual(s.index - last[s.model], cd, s.describe())
            last[s.model] = s.index

    def test_start_bar_is_respected(self):
        for s in scan(self.m, self.cfg, start=2000):
            self.assertGreaterEqual(s.index, 2000)

    def test_unknown_model_name_raises(self):
        with self.assertRaises(KeyError):
            scan(self.m, self.cfg, models=["없는모델"], start=400)

    def test_direct_model_calls_agree_with_the_scan(self):
        """스캔은 모델 함수를 그대로 부른다 — 결과가 달라지면 안 된다."""
        for s in self.setups[:40]:
            again = MODELS[s.model](self.m, s.index, self.cfg)
            self.assertIsNotNone(again, s.describe())
            self.assertAlmostEqual(again.entry, s.entry, places=6)
            self.assertAlmostEqual(again.stop, s.stop, places=6)


if __name__ == "__main__":
    unittest.main()
