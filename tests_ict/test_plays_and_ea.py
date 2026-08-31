"""청산 계획과, 그 계획이 MT5 EA 에 그대로 들어갔는지.

전에 EA 기본값과 프리셋이 서로 달라서, .set 을 안 불러오면 완전히 다른
설정으로 돌아간 적이 있다. 사람이 눈으로 맞추는 건 반드시 어긋난다.
"""

import pathlib
import re
import unittest

from ict.backtest import run
from ict.engine import Market
from ict.models import Config
from ict.plays import ACTIVE, PLAYS, Play, play, table
from ict.sample import gold
from ict.strategy import MODELS, scan

EA = pathlib.Path("mql5/Experts/ICTGold.mq5")
PRESET = pathlib.Path("mql5/Presets/ICTGold-default.set")


def ea_inputs() -> dict[str, str]:
    src = EA.read_text()
    return {m[2]: m[3].strip()
            for m in re.finditer(r'^input\s+(\S+)\s+(\w+)\s*=\s*([^;]+);', src, re.M)}


def preset() -> dict[str, str]:
    out = {}
    for line in PRESET.read_text().splitlines():
        if line.startswith(";") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


class TestPlays(unittest.TestCase):
    def test_every_model_has_a_play(self):
        self.assertEqual(set(PLAYS), set(MODELS))

    def test_active_models_are_enabled_ones(self):
        self.assertEqual(set(ACTIVE), {n for n, p in PLAYS.items() if p.enabled})

    def test_active_list_matches_the_ea(self):
        self.assertEqual(set(ACTIVE), {"Unicorn", "TurtleSoup"})

    def test_risk_stays_in_the_one_to_two_percent_band(self):
        """형이 정한 범위. 여기를 넘으면 계획이 아니라 사고다."""
        for p in PLAYS.values():
            self.assertGreaterEqual(p.risk_pct, 1.0, p.model)
            self.assertLessEqual(p.risk_pct, 2.0, p.model)

    def test_targets_and_holds_are_sane(self):
        for p in PLAYS.values():
            self.assertGreater(p.target_rr, 0, p.model)
            self.assertGreater(p.max_hold, 0, p.model)
            self.assertLessEqual(p.max_hold, 288, f"{p.model}: 하루를 넘긴다")

    def test_breakeven_fires_before_the_target(self):
        """본전 이동이 목표보다 늦으면 영원히 안 옮겨진다."""
        for p in PLAYS.values():
            if p.be_at > 0:
                self.assertLess(p.be_at, p.target_rr, p.model)

    def test_breakeven_is_off_everywhere(self):
        """21년 실측: 6개 모델 전부에서 기대값을 낮추고 낙폭을 키웠다."""
        for p in PLAYS.values():
            self.assertEqual(p.be_at, 0.0, f"{p.model}: 본전 이동은 실측에서 손해다")

    def test_active_plays_carry_their_measurement(self):
        for name in ACTIVE:
            p = PLAYS[name]
            self.assertGreater(p.trades, 1000,
                               f"{name}: 표본 {p.trades}거래로는 못 켠다")
            # 기대값은 체결 봉 손절 버그가 있던 시절 값이라 검증하지 않는다.
            # plays.py 상단 경고 참조.

    def test_the_ea_has_an_atr_stop_floor(self):
        """손절이 ATR 안쪽이면 셋업이 아니라 그 캔들이 결과를 정한다.
        이 셋업의 96%가 1xATR 미만이었다."""
        ea = ea_inputs()
        self.assertIn("InpMinStopATR", ea)
        self.assertGreaterEqual(float(ea["InpMinStopATR"]), 1.0)
        self.assertIn("InpMinStopATR > 0", EA.read_text())

    def test_only_the_measured_survivor_is_on_in_the_ea(self):
        ea = ea_inputs()
        self.assertEqual(ea["InpUseUnicorn"], "true")
        for key in ("InpUseTurtleSoup", "InpUseJudasSwing", "InpUseOTE",
                    "InpUseTJR"):
            self.assertEqual(ea[key], "false", key)

    def test_the_session_is_narrowed_to_new_york_am(self):
        """세 킬존을 다 쓰면 +0.005R, 뉴욕 오전만 쓰면 +0.138R 이었다."""
        ea, src = ea_inputs(), EA.read_text()
        self.assertEqual(ea["InpNyAmOnly"], "true")
        self.assertIn("if(InpNyAmOnly) return (h >= 7.0 && h < 10.0);", src)

    def test_disabled_plays_say_why(self):
        for name, p in PLAYS.items():
            if not p.enabled:
                self.assertGreater(len(p.why), 40, name)

    def test_hold_is_timeframe_independent(self):
        """같은 계획이 M5/M15/M30 에서 같은 '시간'을 들고 있어야 한다."""
        for p in PLAYS.values():
            for tf in (5, 15, 30, 60):
                self.assertAlmostEqual(p.bars_to_hold(tf) * tf, p.hold_minutes,
                                       delta=tf, msg=f"{p.model} {tf}분봉")

    def test_every_play_records_why(self):
        for p in PLAYS.values():
            self.assertGreater(len(p.why), 40, f"{p.model}: 근거가 없다")

    def test_unknown_model_raises(self):
        with self.assertRaises(KeyError):
            play("없는모델")

    def test_table_renders_every_model(self):
        t = table()
        for name in PLAYS:
            self.assertIn(name, t)

    def test_plays_are_frozen(self):
        with self.assertRaises(Exception):
            PLAYS["OTE"].risk_pct = 9.0    # type: ignore[misc]


class TestPlaysChangeTheBacktest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bars = list(gold(days=60, seed=5))
        # 기본 실행 목록이 비어 있으므로 검증용으로 모델을 지정한다
        cls.setups = scan(Market.build(cls.bars), Config(),
                          models=["Unicorn", "TurtleSoup"])

    def test_scan_defaults_to_the_active_models(self):
        got = scan(Market.build(self.bars), Config())
        self.assertEqual({s.model for s in got} - set(ACTIVE), set())

    def test_named_models_still_work(self):
        self.assertTrue(self.setups)

    def test_all_models_is_a_superset(self):
        every = scan(Market.build(self.bars), Config(), models=list(MODELS))
        self.assertGreaterEqual(len(every), len(self.setups))

    def test_target_is_capped_at_the_plays_rr(self):
        r = run(self.bars, setups=self.setups, use_plays=True)
        for t in r.trades:
            if t.outcome == "target":
                self.assertLessEqual(t.r, PLAYS[t.setup.model].target_rr + 1e-6,
                                     t.setup.describe())

    def test_hold_limit_is_enforced_per_model(self):
        r = run(self.bars, setups=self.setups, use_plays=True)
        for t in r.trades:
            held = t.exit_index - t.setup.index
            self.assertLessEqual(held, PLAYS[t.setup.model].max_hold,
                                 t.setup.describe())

    def test_breakeven_exits_are_not_full_losses(self):
        r = run(self.bars, setups=self.setups, use_plays=True)
        for t in r.trades:
            if t.outcome == "breakeven":
                self.assertGreater(t.r, -0.6, t.setup.describe())

    def test_disabling_plays_changes_the_result(self):
        a = run(self.bars, setups=self.setups, use_plays=True)
        b = run(self.bars, setups=self.setups, use_plays=False)
        self.assertNotAlmostEqual(a.total_r, b.total_r, places=3)


@unittest.skipUnless(EA.exists(), "EA 파일 없음")
class TestEaMatchesPlays(unittest.TestCase):
    """EA 기본값이 파이썬 계획과 같은 숫자여야 한다."""

    PREFIX = {"Unicorn": "InpUNI_", "JudasSwing": "InpJS_",
              "TurtleSoup": "InpTS_", "OTE": "InpOTE_", "TJR": "InpTJR_"}

    def setUp(self):
        self.ea = ea_inputs()

    def test_active_models_have_ea_inputs(self):
        for name in ACTIVE:
            self.assertIn(name, self.PREFIX, f"{name} 이 EA 에 없다")

    def test_target_rr_matches(self):
        for name, pre in self.PREFIX.items():
            self.assertAlmostEqual(float(self.ea[pre + "TargetRR"]),
                                   PLAYS[name].target_rr, msg=name)

    def test_hold_minutes_match(self):
        """EA 는 분으로, 파이썬은 M5 봉 수로 들고 있다. 같은 시간이어야 한다."""
        for name, pre in self.PREFIX.items():
            self.assertEqual(int(self.ea[pre + "HoldMin"]),
                             PLAYS[name].hold_minutes, name)

    def test_hold_is_expressed_in_minutes_not_bars(self):
        """봉 수로 두면 M5 차트와 M30 차트에서 보유시간이 6배 달라진다."""
        src = EA.read_text()
        self.assertNotIn("HoldBars", src)
        self.assertIn("holdMin", src)

    def test_breakeven_matches(self):
        for name, pre in self.PREFIX.items():
            self.assertAlmostEqual(float(self.ea[pre + "BreakevenR"]),
                                   PLAYS[name].be_at, msg=name)

    def test_risk_matches(self):
        for name, pre in self.PREFIX.items():
            self.assertAlmostEqual(float(self.ea[pre + "RiskPct"]),
                                   PLAYS[name].risk_pct, msg=name)

    def test_enabled_flags_match(self):
        flag = {"Unicorn": "InpUseUnicorn", "JudasSwing": "InpUseJudasSwing",
                "TurtleSoup": "InpUseTurtleSoup", "OTE": "InpUseOTE",
                "TJR": "InpUseTJR"}
        for name, key in flag.items():
            self.assertEqual(self.ea[key] == "true", PLAYS[name].enabled, name)

    def test_gold_calibration_matches(self):
        from ict.gold import STANDARD
        pairs = [("InpDisplacementATR", STANDARD.displacement_atr),
                 ("InpDisplacementBP", STANDARD.displacement_bp),
                 ("InpMinFvgATR", STANDARD.min_fvg_atr),
                 ("InpMinFvgBP", STANDARD.min_fvg_bp),
                 ("InpFvgSpreadMult", STANDARD.fvg_spread_multiple),
                 ("InpStopBufferATR", STANDARD.stop_buffer_atr),
                 ("InpStopBufferBP", STANDARD.stop_buffer_bp),
                 ("InpMaxEntryDistATR", STANDARD.max_entry_distance_atr),
                 ("InpMaxSpreadToStop", STANDARD.max_spread_to_stop),
                 ("InpRolloverStart", STANDARD.rollover_start),
                 ("InpRolloverEnd", STANDARD.rollover_end)]
        for key, want in pairs:
            self.assertAlmostEqual(float(self.ea[key]), want, msg=key)


@unittest.skipUnless(PRESET.exists(), "프리셋 없음")
class TestPresetMatchesEaDefaults(unittest.TestCase):
    """.set 을 안 불러와도 같은 설정으로 돌아야 한다."""

    def test_every_input_is_in_the_preset(self):
        ea, ps = ea_inputs(), preset()
        self.assertEqual(set(ea), set(ps))

    def test_values_are_identical(self):
        ea, ps = ea_inputs(), preset()
        for k, v in ea.items():
            a, b = ps[k], v
            try:
                self.assertAlmostEqual(float(a), float(b), msg=k)
            except ValueError:
                self.assertEqual(a, b, k)


@unittest.skipUnless(EA.exists(), "EA 파일 없음")
class TestEaFileHealth(unittest.TestCase):
    def test_is_pure_ascii(self):
        """한글 주석이 들어가면 MetaEditor 인코딩에서 깨진다."""
        raw = EA.read_bytes()
        try:
            raw.decode("ascii")
        except UnicodeDecodeError as e:
            self.fail(f"ASCII 아님: 바이트 {e.start}")

    def test_braces_balance(self):
        src = EA.read_text()
        self.assertEqual(src.count("{"), src.count("}"))
        self.assertEqual(src.count("("), src.count(")"))

    def test_dry_run_is_off_by_default(self):
        """이것 때문에 전에 몇 년치 백테스트가 통째로 무거래였다."""
        self.assertEqual(ea_inputs()["InpDryRun"], "false")

    def test_tester_ignores_dry_run(self):
        self.assertIn("MQL_TESTER", EA.read_text())

    def test_spread_guard_is_in_price_not_points(self):
        """3자리 금 호가에서 points 로 비교하면 전부 걸러진다."""
        src = EA.read_text()
        self.assertIn("InpMaxSpreadPrice", src)
        self.assertNotIn("InpMaxSpreadPoints", src)

    def test_over_trading_gates_exist(self):
        """이게 없으면 EA 가 모델보다 28배 자주 주문한다. 실제로 그랬다."""
        ea = ea_inputs()
        for key in ("InpRequireKillzone", "InpRequireContext",
                    "InpCooldownBars", "InpMaxTradesPerDay", "InpHardCapPerDay"):
            self.assertIn(key, ea, key)
        self.assertEqual(ea["InpRequireKillzone"], "true")
        self.assertEqual(ea["InpRequireContext"], "true")
        self.assertGreater(int(ea["InpCooldownBars"]), 0)

    def test_daily_cap_is_a_handful_not_a_flood(self):
        ea = ea_inputs()
        self.assertLessEqual(int(ea["InpMaxTradesPerDay"]), 3)
        self.assertLessEqual(int(ea["InpHardCapPerDay"]), 10)

    def test_a_raid_is_only_counted_once(self):
        """습격은 그 레벨이 처음 뚫린 봉이다. 뒤따르는 봉마다 재진입하면 안 된다."""
        self.assertIn("FirstBreak", EA.read_text())

    def test_turtlesoup_waits_for_the_retest(self):
        """레벨로 되돌아올 때 지정가로 잡는다. 시장가로 쫓으면 다른 전략이다."""
        src = EA.read_text()
        i = src.index("bool TurtleSoup")
        body = src[i:src.index("//====", i + 10)]
        self.assertIn("s.isLimit = true", body)
        self.assertNotIn("s.isLimit = false", body)

    def test_hard_money_guards_exist(self):
        """손절이 안 걸린 포지션 하나가 계좌를 가져간다."""
        ea, src = ea_inputs(), EA.read_text()
        for key in ("InpMinStopPrice", "InpMaxLots", "InpMaxRiskPctHard"):
            self.assertIn(key, ea, key)
        self.assertIn("SYMBOL_TRADE_STOPS_LEVEL", src)
        self.assertIn("NO STOP LOSS", src)
        self.assertLessEqual(float(ea["InpMaxRiskPctHard"]), 3.0)

    def test_server_offset_is_not_zero_by_default(self):
        """0 으로 두면 킬존이 통째로 2~3시간 밀린다. 실제로 그렇게 돌아갔다."""
        ea = ea_inputs()
        self.assertIn("InpServerGmtOffset", ea)
        self.assertNotEqual(float(ea["InpServerGmtOffset"]), 0.0)

    def test_tester_refuses_a_zero_offset(self):
        """말로 '2로 바꾸세요' 하는 건 강제가 아니다. 코드가 막아야 한다."""
        src = EA.read_text()
        self.assertIn("INIT_PARAMETERS_INCORRECT", src)
        self.assertIn("InpAutoDetectOffset", src)

    def test_limit_expiry_is_in_minutes(self):
        """봉 수로 두면 M5 에서 2시간, H1 에서 24시간이 된다.
        하루 묵은 지정가는 가격이 레벨을 뚫고 지나갈 때 체결되고 바로 손절된다."""
        src = EA.read_text()
        self.assertIn("InpLimitExpiryMin", ea_inputs())
        self.assertNotIn("InpLimitExpiryBars", src)
        self.assertIn("InpLimitExpiryMin * 60", src)

    def test_the_hold_clock_starts_when_the_order_was_placed(self):
        """5시간 뒤에 체결된 지정가는 남은 3시간을 받아야지 새 8시간이 아니다.
        모델을 그렇게 쟀다."""
        self.assertIn("g_open.opened  = g_pend.placed", EA.read_text())

    def test_limit_expiry_does_not_exceed_the_hold(self):
        ea = ea_inputs()
        exp = int(ea["InpLimitExpiryMin"])
        for pre in ("InpUNI_", "InpTS_"):
            self.assertLessEqual(exp, int(ea[pre + "HoldMin"]),
                                 "만료가 보유한도보다 길면 계획이 끝난 뒤 체결된다")

    def test_limit_fills_inherit_their_plan(self):
        """지정가로 들어간 포지션에도 보유한도가 붙어야 한다.
        전에는 시장가 주문에만 붙어서, 지정가 모델은 시간 청산이 없었다."""
        src = EA.read_text()
        self.assertIn("g_pend.holdMin", src)
        self.assertIn("g_open.holdMin = g_pend.holdMin", src)

    def test_warns_on_an_unmeasured_timeframe(self):
        self.assertIn("PERIOD_M15", EA.read_text())


if __name__ == "__main__":
    unittest.main()


class TestBacktesterIsHonest(unittest.TestCase):
    """체결된 그 봉에서도 손절을 확인해야 한다.

    안 하면 손절이 좁을수록 성적이 좋아진다. 실제로 그렇게 나왔었고,
    그 숫자를 근거로 모델을 골라 사용자에게 보냈다.
    """

    def test_a_stop_hit_on_the_fill_bar_is_a_loss(self):
        from datetime import datetime, timedelta, timezone
        from crowcode.data import Candle
        from ict.backtest import run
        from ict.models import Setup
        from ict import liquidity as liq, pdarrays as pda
        from ict.structure import BULL, StructureEvent

        t0 = datetime(2025, 1, 6, 14, 0, tzinfo=timezone.utc)
        bars = [Candle(t0 + timedelta(minutes=5 * i), 100.0, 100.5, 99.5, 100.0)
                for i in range(10)]
        # 체결 봉: 진입가에 닿고 같은 봉에서 손절까지 뚫는다
        bars[3] = Candle(bars[3].ts, 100.0, 100.2, 90.0, 99.0)
        arr = pda.PDArray("FVG", BULL, 100.0, 99.0, 2)
        mss = StructureEvent(2, bars[2].ts, "MSS", BULL, 100.0, 1)
        s = Setup(ts=bars[2].ts, index=2, model="TurtleSoup", side="buy",
                  entry=100.0, stop=99.0, target=104.0, array=arr, raid=None,
                  mss=mss, target_pool=None, killzone="LondonKZ")
        r = run(bars, setups=[s], spread=0.0, use_plays=False, max_hold=8)
        self.assertEqual(len(r.trades), 1)
        self.assertEqual(r.trades[0].outcome, "stop",
                         "체결 봉의 손절을 놓쳤다 — 좁은 손절이 공짜로 통과한다")
        self.assertLess(r.trades[0].r, 0)


class TestValueAreaGate(unittest.TestCase):
    """자료(TPO)에서 유일하게 실측을 통과한 규칙이 EA 에 들어갔는가."""

    def test_the_gate_exists_and_is_on(self):
        ea = ea_inputs()
        self.assertIn("InpRequireValidVA", ea)
        self.assertEqual(ea["InpRequireValidVA"], "true")

    def test_the_ea_computes_a_value_area(self):
        src = EA.read_text()
        self.assertIn("LastSessionClosedInsideVA", src)
        self.assertIn("0.70", src, "VA 는 전체 TPO 의 70% 구간이다")

    def test_unknown_history_does_not_block(self):
        """앞 세션을 못 찾으면 막지 않는다 — 모르는 것과 나쁜 것은 다르다."""
        src = EA.read_text()
        i = src.index("bool LastSessionClosedInsideVA")
        body = src[i:src.index("//====", i + 10)]
        self.assertIn("return true;  // unknown: do not block", body)

    def test_python_side_can_compute_the_same_thing(self):
        from tpo.profile import build
        from tpo.sessions import split
        from ict.sample import gold
        bars = list(gold(days=6, seed=3))
        ses = split(bars)
        self.assertTrue(ses)
        p = build(ses[0].bars, tick=1.0)
        lo, hi = p.value_area()
        self.assertLessEqual(lo, p.poc)
        self.assertGreaterEqual(hi, p.poc)
        self.assertEqual(p.close_outside_va(), not (lo <= p.close <= hi))
