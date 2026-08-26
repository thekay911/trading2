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

    def test_min_viable_balance(self):
        # 0.5% 리스크, 손절 $5, 0.01랏 최소 → 최소 리스크 $5 → 잔고 $1,000
        cfg = SCALP.with_(risk_pct=0.5)
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
        cfg = preset("scalp")
        sigs, _ = self._signals(cfg)
        self.assertTrue(sigs, "테스트할 시그널이 없음")
        for s in sigs:
            self.assertGreaterEqual(s.risk_per_unit, cfg.min_sl_price - 1e-9)
            self.assertLessEqual(s.risk_per_unit, cfg.max_sl_price + 1e-9)

    def test_impossible_guard_rejects_everything(self):
        cfg = preset("scalp").with_(min_sl_price=500.0, max_sl_price=900.0)
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
