import os
import re
import unittest
from datetime import timezone

from crowcode.config import PRESETS, SCALP, preset
from crowcode.data import synthetic
from crowcode.gold import (
    CANONICAL, REFERENCE, min_viable_balance, money_per_price_unit,
    movers_table, parse_news, preflight, resolve_symbol,
)
from crowcode.mt5.paper import XAUUSD

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MQL = os.path.join(ROOT, "mql5", "Experts", "CrowConcept.mq5")
SETS = os.path.join(ROOT, "mql5", "Presets")


class TestSymbolResolution(unittest.TestCase):
    def test_plain_name(self):
        self.assertEqual(resolve_symbol(["EURUSD", "XAUUSD", "GBPUSD"]), "XAUUSD")

    def test_broker_suffixes(self):
        self.assertEqual(resolve_symbol(["EURUSD", "XAUUSD.m"]), "XAUUSD.m")
        self.assertEqual(resolve_symbol(["XAUUSDm", "EURUSD"]), "XAUUSDm")
        self.assertEqual(resolve_symbol(["XAUUSD-ECN"]), "XAUUSD-ECN")

    def test_unknown_suffix_falls_back_to_prefix(self):
        self.assertEqual(resolve_symbol(["XAUUSD.zz9", "EURUSD"]), "XAUUSD.zz9")

    def test_gold_alias(self):
        self.assertEqual(resolve_symbol(["EURUSD", "GOLD"]), "GOLD")

    def test_prefers_shortest_variant(self):
        self.assertEqual(resolve_symbol(["XAUUSD.pro.x", "XAUUSD.m"]), "XAUUSD.m")

    def test_none_when_absent(self):
        self.assertIsNone(resolve_symbol(["EURUSD", "GBPJPY"]))
        self.assertIsNone(resolve_symbol([]))

    def test_case_insensitive(self):
        self.assertEqual(resolve_symbol(["xauusd"]), "xauusd")


class TestGoldMath(unittest.TestCase):
    def test_money_per_price_unit_from_tick_specs(self):
        self.assertAlmostEqual(money_per_price_unit(XAUUSD), 100.0)

    def test_one_cent_lot_moves_one_dollar(self):
        self.assertAlmostEqual(REFERENCE.money_at(0.01, 1.0), 1.0)

    def test_min_viable_balance_uses_the_risk_ceiling(self):
        """진입을 막는 것은 목표 리스크%가 아니라 그 상한이다.

        상한 안이면 최소 랏으로라도 들어가기 때문이다.
        손절 $5, 0.01랏 → 최소 리스크 $5. 상한 2.5% 면 잔고 $200 이 필요하다.
        """
        cfg = SCALP.with_(risk_pct=0.5, max_risk_pct=2.5)
        self.assertAlmostEqual(min_viable_balance(cfg, 5.0, XAUUSD), 200.0)

    def test_min_viable_balance_falls_back_to_risk_pct(self):
        cfg = SCALP.with_(risk_pct=0.5, max_risk_pct=0.0)
        self.assertAlmostEqual(min_viable_balance(cfg, 5.0, XAUUSD), 1000.0)

    def test_wider_stop_needs_more_capital(self):
        cfg = preset("intraday")
        self.assertGreater(min_viable_balance(cfg, 20.0, XAUUSD),
                           min_viable_balance(cfg, 5.0, XAUUSD))

    def test_zero_risk_is_safe(self):
        self.assertEqual(min_viable_balance(SCALP.with_(risk_pct=0.0), 5.0, XAUUSD), 0.0)


class TestPreflight(unittest.TestCase):
    def test_tiny_account_fails_capital_check(self):
        rep = preflight(preset("swing"), XAUUSD, 100.0, 0.25)
        self.assertTrue(rep.failed)
        self.assertTrue(any("자본" in c.title and c.level == "fail" for c in rep.checks))

    def test_adequate_account_passes(self):
        rep = preflight(preset("intraday"), XAUUSD, 5000.0, 0.20)
        self.assertFalse(rep.failed)

    def test_broker_stop_distance_wider_than_min_sl_fails(self):
        wide = XAUUSD.__class__(**{**XAUUSD.__dict__, "stops_level_points": 400})  # $4
        rep = preflight(preset("intraday"), wide, 5000.0, 0.20)
        self.assertTrue(any("이격" in c.title and c.level == "fail" for c in rep.checks))

    def test_non_gold_contract_size_warns(self):
        fx = XAUUSD.__class__(**{**XAUUSD.__dict__, "tick_value": 1.0, "tick_size": 0.00001})
        rep = preflight(preset("intraday"), fx, 5000.0, 0.0002)
        self.assertTrue(any("계약 사양" in c.title and c.level == "warn" for c in rep.checks))

    def test_report_renders(self):
        text = preflight(preset("scalp"), XAUUSD, 2000.0, 0.25).report()
        self.assertIn("XAUUSD 사전 점검", text)


class TestNewsParsing(unittest.TestCase):
    def test_named_and_plain_entries(self):
        events = parse_news("CPI@2026-09-11 12:30, 2026-10-02 12:30")
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].name, "CPI")
        self.assertEqual(events[0].ts.tzinfo, timezone.utc)

    def test_separators(self):
        self.assertEqual(len(parse_news("2026-09-11 12:30; 2026-09-12 12:30")), 2)
        self.assertEqual(len(parse_news("2026-09-11 12:30\n2026-09-12 12:30")), 2)

    def test_garbage_is_skipped(self):
        self.assertEqual(parse_news("not a date, also junk"), [])

    def test_empty(self):
        self.assertEqual(parse_news(""), [])

    def test_movers_table_renders(self):
        self.assertIn("NFP", movers_table())


class TestPresetConsistency(unittest.TestCase):
    def test_no_preset_contradicts_itself(self):
        for name, cfg in PRESETS.items():
            for w in cfg.validate():
                self.assertFalse(w.startswith("[모순]"), f"{name}: {w}")

    def test_daily_cap_allows_the_consecutive_loss_rule(self):
        """일일 한도가 거래당 리스크보다 낮으면 연속 손절 규칙이 죽는다."""
        for name, cfg in PRESETS.items():
            self.assertGreaterEqual(cfg.max_daily_loss_pct, cfg.risk_pct,
                                    f"{name}: 첫 손절에서 하루가 끝난다")

    def test_stop_guards_are_ordered_and_scale_with_timeframe(self):
        order = ["highrisk", "scalp", "intraday", "swing"]
        prev = 0.0
        for name in order:
            cfg = PRESETS[name]
            self.assertLess(cfg.min_sl_price, cfg.max_sl_price, name)
            self.assertGreaterEqual(cfg.min_sl_price, prev, f"{name} 손절 하한이 역전됨")
            prev = cfg.min_sl_price

    def test_tighter_stops_tolerate_more_relative_spread(self):
        self.assertGreater(PRESETS["scalp"].max_spread_ratio,
                           PRESETS["swing"].max_spread_ratio)


class TestStopGuardsAreEnforced(unittest.TestCase):
    def _signals(self, cfg):
        from crowcode.strategy import CrowStrategy

        series = synthetic(6000)
        st = CrowStrategy(cfg, CANONICAL)
        view = st.view(series)
        out = []
        for i in range(800, len(series), 7):
            s = st.evaluate(view, 20000.0, None, now_ts=series[i].ts)
            if s:
                out.append(s)
        return out, st

    def test_no_signal_violates_the_stop_guards(self):
        cfg = preset("intraday")
        sigs, _ = self._signals(cfg)
        self.assertTrue(sigs, "테스트할 시그널이 없음")
        for s in sigs:
            self.assertGreaterEqual(s.risk_per_unit, cfg.min_sl_price - 1e-9)
            self.assertLessEqual(s.risk_per_unit, cfg.max_sl_price + 1e-9)

    def test_impossible_guard_rejects_everything(self):
        cfg = preset("intraday").with_(min_sl_price=500.0, max_sl_price=900.0)
        sigs, st = self._signals(cfg)
        self.assertEqual(sigs, [])
        self.assertIn("sl_too_tight", st.rejection_summary())


class TestSetFilesMatchPresets(unittest.TestCase):
    """생성된 .set 파일이 파이썬 프리셋과 어긋나지 않는지 확인한다.

    EA 는 여기서 컴파일할 수 없으므로, 최소한 입력값 이름과 값이
    실제 소스와 맞는지는 기계적으로 검증한다.
    """

    @classmethod
    def setUpClass(cls):
        with open(MQL, encoding="utf-8") as fh:
            cls.source = fh.read()
        cls.inputs = set(re.findall(r"^input\s+\S+\s+(\w+)\s*=", cls.source, re.M))

    def test_every_preset_has_a_set_file(self):
        for name in PRESETS:
            self.assertTrue(os.path.exists(os.path.join(SETS, f"CrowConcept-{name}.set")), name)

    def test_set_keys_exist_as_ea_inputs(self):
        for name in PRESETS:
            path = os.path.join(SETS, f"CrowConcept-{name}.set")
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith(";"):
                        continue
                    key = line.split("=", 1)[0]
                    self.assertIn(key, self.inputs, f"{name}.set 의 {key} 가 EA 입력에 없다")

    def test_risk_values_match_python_presets(self):
        for name, cfg in PRESETS.items():
            path = os.path.join(SETS, f"CrowConcept-{name}.set")
            with open(path, encoding="utf-8") as fh:
                kv = dict(l.strip().split("=", 1) for l in fh
                          if "=" in l and not l.strip().startswith(";"))
            self.assertAlmostEqual(float(kv["InpRiskPercent"]), cfg.risk_pct, msg=name)
            self.assertAlmostEqual(float(kv["InpMinSLPrice"]), cfg.min_sl_price, msg=name)
            self.assertAlmostEqual(float(kv["InpMaxSLPrice"]), cfg.max_sl_price, msg=name)
            self.assertAlmostEqual(float(kv["InpMaxDailyLossPct"]), cfg.max_daily_loss_pct, msg=name)

    def test_dry_run_is_on_in_every_preset_file(self):
        for name in PRESETS:
            with open(os.path.join(SETS, f"CrowConcept-{name}.set"), encoding="utf-8") as fh:
                self.assertIn("InpDryRun=true", fh.read(), name)


if __name__ == "__main__":
    unittest.main()


class TestSafeDefaults(unittest.TestCase):
    """주문이 나가는 스위치는 항상 '꺼짐'이 기본이어야 한다."""

    def test_ea_source_defaults_to_dry_run(self):
        with open(MQL, encoding="utf-8") as fh:
            src = fh.read()
        m = re.search(r"input\s+bool\s+InpDryRun\s*=\s*(\w+)", src)
        self.assertIsNotNone(m, "InpDryRun 입력을 찾지 못함")
        self.assertEqual(m.group(1), "true",
                         "EA 를 .set 없이 차트에 붙이면 바로 실주문이 나간다")

    def test_python_runner_defaults_to_dry_run(self):
        from crowcode.mt5.runner import LiveConfig
        self.assertTrue(LiveConfig().dry_run)


class TestPipHandling(unittest.TestCase):
    """금에서 '핍' 은 브로커마다 10배 차이 난다. 그 혼동이 계산까지 번지면 안 된다."""

    def test_pip_conversion_round_trips(self):
        cfg = preset("intraday").with_(pip_size=1.0)
        self.assertAlmostEqual(cfg.price_to_pips(cfg.pips_to_price(20)), 20.0)

    def test_same_pips_mean_different_prices(self):
        base = preset("intraday")
        big = base.with_sl_pips(20, 25, 1.0)
        small = base.with_sl_pips(20, 25, 0.10)
        self.assertAlmostEqual(big.min_sl_price, 20.0)
        self.assertAlmostEqual(small.min_sl_price, 2.0)
        self.assertEqual(big.sl_pips, small.sl_pips)     # 핍으로는 같다

    def test_label_shows_both_units(self):
        label = preset("intraday").with_sl_pips(20, 25, 1.0).sl_label()
        self.assertIn("20~25핍", label)
        self.assertIn("$20~$25", label)

    def test_narrow_band_is_flagged(self):
        cfg = preset("intraday").with_sl_pips(20, 25, 1.0)
        self.assertTrue(any("너무 좁다" in w for w in cfg.validate()))

    def test_wide_band_is_flagged(self):
        cfg = preset("intraday").with_sl_pips(2, 40, 1.0)
        self.assertTrue(any("너무 넓다" in w for w in cfg.validate()))


class TestStopClamping(unittest.TestCase):
    """clamp 는 넓히기만 한다. 좁히면 구조 안쪽에 손절을 두는 셈이 된다."""

    def _signals(self, cfg, bars=9000, minutes=5, step=3):
        from crowcode.data import synthetic
        from crowcode.strategy import CrowStrategy

        s = synthetic(bars, minutes=minutes)
        st = CrowStrategy(cfg, CANONICAL)
        view = st.view(s)
        out = []
        for i in range(800, len(s), step):
            sig = st.evaluate(view, 50000.0, None, now_ts=s[i].ts)
            if sig:
                out.append(sig)
        return out, st

    def test_clamp_widens_a_tight_structural_stop(self):
        cfg = preset("intraday").with_sl_pips(20, 25, 1.0).with_(sl_mode="clamp")
        sigs, _ = self._signals(cfg)
        self.assertTrue(sigs, "clamp 인데 시그널이 하나도 없다")
        for s in sigs:
            self.assertGreaterEqual(s.risk_per_unit, cfg.min_sl_price - 1e-6)

    def test_clamp_never_narrows_below_structure(self):
        """구조가 상한보다 넓은 손절을 요구하면 좁히지 말고 버려야 한다."""
        cfg = preset("intraday").with_sl_pips(0.2, 0.5, 1.0).with_(sl_mode="clamp")
        sigs, st = self._signals(cfg)
        self.assertEqual(sigs, [])
        self.assertIn("sl_too_wide", st.rejection_summary())

    def test_filter_mode_rejects_instead_of_widening(self):
        cfg = preset("intraday").with_sl_pips(20, 25, 1.0)   # 기본 filter
        sigs, st = self._signals(cfg)
        self.assertEqual(sigs, [])
        self.assertIn("sl_too_tight", st.rejection_summary())

    def test_target_follows_the_actual_stop(self):
        """손절을 넓혔으면 목표도 그에 맞춰 1:3 이어야 한다."""
        cfg = preset("intraday").with_sl_pips(20, 25, 1.0).with_(sl_mode="clamp")
        sigs, _ = self._signals(cfg)
        self.assertTrue(sigs)
        for s in sigs:
            self.assertGreaterEqual(s.rr + 1e-9, cfg.min_rr)

    def test_clamp_is_recorded_in_the_reasons(self):
        cfg = preset("intraday").with_sl_pips(20, 25, 1.0).with_(sl_mode="clamp")
        sigs, _ = self._signals(cfg)
        self.assertTrue(any("확대" in r for s in sigs for r in s.reasons))


class TestEaDefaultsMatchThePreset(unittest.TestCase):
    """EA 는 파일 하나로 끝나야 한다 — .set 없이도 기본값이 곧 운용 설정이다.

    기본값이 프리셋과 어긋나면 .set 을 안 불러온 사람은 다른 시스템을
    돌리게 된다. 그래서 기계적으로 대조한다.
    """

    TF = {"M1": "PERIOD_M1", "M5": "PERIOD_M5", "M15": "PERIOD_M15",
          "M30": "PERIOD_M30", "H1": "PERIOD_H1", "H4": "PERIOD_H4", "D1": "PERIOD_D1"}

    @classmethod
    def setUpClass(cls):
        with open(MQL, encoding="utf-8") as fh:
            src = fh.read()
        cls.defaults = dict(re.findall(r"^input\s+\S+\s+(\w+)\s*=\s*([^;]+);", src, re.M))

    def _val(self, name):
        return self.defaults[name].strip()

    def test_timeframes(self):
        cfg = preset("scalp")
        self.assertEqual(self._val("InpHTF"), self.TF[cfg.htf])
        self.assertEqual(self._val("InpMTF"), self.TF[cfg.mtf])
        self.assertEqual(self._val("InpLTF"), self.TF[cfg.ltf])

    def test_risk_and_targets(self):
        cfg = preset("scalp")
        for name, want in (("InpRiskPercent", cfg.risk_pct),
                           ("InpMinRR", cfg.min_rr),
                           ("InpTargetRR", cfg.target_rr),
                           ("InpBreakevenAtR", cfg.breakeven_at_r),
                           ("InpPartialAtR", cfg.partial_at_r),
                           ("InpPartialFraction", cfg.partial_fraction)):
            self.assertAlmostEqual(float(self._val(name)), want, msg=name)

    def test_stop_guards(self):
        cfg = preset("scalp")
        self.assertAlmostEqual(float(self._val("InpMinSLPrice")), cfg.min_sl_price)
        self.assertAlmostEqual(float(self._val("InpMaxSLPrice")), cfg.max_sl_price)
        self.assertAlmostEqual(float(self._val("InpFixedSLPrice")), cfg.min_sl_price)
        self.assertAlmostEqual(float(self._val("InpMaxSpreadRatio")), cfg.max_spread_ratio)

    def test_daily_limits(self):
        cfg = preset("scalp")
        self.assertEqual(int(self._val("InpMaxTradesPerDay")), cfg.max_trades_per_day)
        self.assertEqual(int(self._val("InpMaxConsecLosses")), cfg.max_consecutive_losses)
        self.assertAlmostEqual(float(self._val("InpMaxDailyLossPct")), cfg.max_daily_loss_pct)
        self.assertAlmostEqual(float(self._val("InpHardStopPct")), cfg.hard_stop_loss_pct)

    def test_stop_mode_matches(self):
        want = {"filter": "STOP_STRUCTURE", "clamp": "STOP_CLAMP", "fixed": "STOP_FIXED"}
        self.assertEqual(self._val("InpStopMode"), want[preset("scalp").sl_mode])

    def test_sessions(self):
        cfg = preset("scalp")
        self.assertAlmostEqual(float(self._val("InpSession1Start")), cfg.sessions[0].start_hour)
        self.assertAlmostEqual(float(self._val("InpSession2End")), cfg.sessions[1].end_hour)

    def test_ea_is_self_contained(self):
        """CTrade 외의 include 가 있으면 파일 하나로 끝나지 않는다."""
        with open(MQL, encoding="utf-8") as fh:
            includes = re.findall(r"^#include\s+<([^>]+)>", fh.read(), re.M)
        self.assertEqual(includes, ["Trade/Trade.mqh"], f"추가 include: {includes}")
