import unittest
from datetime import datetime, timedelta, timezone

from crowcode.config import SCALP, preset
from crowcode.data import synthetic
from crowcode.risk import RiskState
from crowcode.sessions import NewsEvent, in_session
from crowcode.strategy import CrowStrategy


def first_signal(series, cfg, start=800, step=7, stop=None):
    """합성 데이터에서 최초 시그널 (index, Signal) 을 찾는다."""
    st = CrowStrategy(cfg)
    view = st.view(series)
    for i in range(start, stop or len(series), step):
        s = st.evaluate(view, 5000.0, None, now_ts=series[i].ts)
        if s:
            return i, s
    return None, None


class TestGates(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.series = synthetic(6000)
        cls.cfg = preset("scalp")

    def test_no_signal_outside_session(self):
        st = CrowStrategy(self.cfg)
        view = st.view(self.series)
        sigs = []
        for i in range(800, len(self.series), 7):
            s = st.evaluate(view, 5000.0, None, now_ts=self.series[i].ts)
            if s:
                sigs.append(s)
        for s in sigs:
            self.assertIsNotNone(in_session(s.ts, self.cfg.sessions))

    def test_news_blackout_blocks(self):
        st = CrowStrategy(self.cfg)
        view = st.view(self.series)
        ts = None
        for i in range(800, len(self.series), 7):
            s = st.evaluate(view, 5000.0, None, now_ts=self.series[i].ts)
            if s:
                ts = s.ts
                break
        self.assertIsNotNone(ts, "테스트용 시그널을 찾지 못함")

        blocked = CrowStrategy(self.cfg, news=[NewsEvent(ts, "NFP", "high")])
        self.assertIsNone(blocked.evaluate(blocked.view(self.series), 5000.0, None, now_ts=ts))
        self.assertEqual(blocked.rejections[-1].rule, "news")

    def test_risk_gate_blocks_after_consecutive_losses(self):
        _, sig = first_signal(self.series, self.cfg)
        self.assertIsNotNone(sig, "테스트용 시그널을 찾지 못함")
        st = CrowStrategy(self.cfg)
        view = st.view(self.series)
        risk = RiskState(balance=5000.0)
        ts = sig.ts
        risk.roll_day(ts)
        risk.register_close(-50, self.cfg)
        risk.register_close(-50, self.cfg)
        self.assertIsNone(st.evaluate(view, 5000.0, risk, now_ts=ts))
        self.assertEqual(st.rejections[-1].rule, "risk_gate")


class TestSignalIntegrity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.series = synthetic(6000)
        cls.cfg = preset("scalp")
        st = CrowStrategy(cls.cfg)
        view = st.view(cls.series)
        cls.signals = []
        for i in range(800, len(cls.series), 7):
            s = st.evaluate(view, 5000.0, None, now_ts=cls.series[i].ts)
            if s:
                cls.signals.append(s)
        cls.strategy = st

    def test_produces_at_least_one_signal(self):
        self.assertTrue(self.signals, "합성 데이터에서 시그널이 하나도 안 나옴")

    def test_levels_are_ordered(self):
        for s in self.signals:
            if s.side == "buy":
                self.assertLess(s.sl, s.entry)
                self.assertLess(s.entry, s.tp)
            else:
                self.assertGreater(s.sl, s.entry)
                self.assertGreater(s.entry, s.tp)

    def test_min_rr_enforced(self):
        for s in self.signals:
            self.assertGreaterEqual(s.rr + 1e-9, self.cfg.min_rr)

    def test_risk_never_exceeds_configured_percent(self):
        for s in self.signals:
            self.assertLessEqual(s.risk_amount, 5000.0 * self.cfg.risk_pct / 100.0 + 1e-6)

    def test_reasons_are_recorded(self):
        for s in self.signals:
            self.assertTrue(s.reasons)
            self.assertTrue(any("CHOCH" in r for r in s.reasons))

    def test_rejection_summary_is_populated(self):
        self.assertTrue(self.strategy.rejection_summary())


class TestNoLookahead(unittest.TestCase):
    def test_truncated_history_gives_same_signal(self):
        series = synthetic(6000)
        cfg = preset("scalp")

        i, full_sig = first_signal(series, cfg)
        self.assertIsNotNone(full_sig, "비교할 시그널을 찾지 못함")

        st_trunc = CrowStrategy(cfg)
        trunc = series[: i + 1]
        cut_sig = st_trunc.evaluate(st_trunc.view(trunc), 5000.0, None, now_ts=series[i].ts)
        self.assertIsNotNone(cut_sig, "잘린 시계열에서는 시그널이 사라짐 → 룩어헤드 의심")
        self.assertAlmostEqual(full_sig.entry, cut_sig.entry, places=6)
        self.assertAlmostEqual(full_sig.sl, cut_sig.sl, places=6)
        self.assertAlmostEqual(full_sig.tp, cut_sig.tp, places=6)
        self.assertEqual(full_sig.side, cut_sig.side)


class TestPresets(unittest.TestCase):
    def test_all_presets_construct(self):
        for name in ("swing", "scalp", "highrisk"):
            st = CrowStrategy(preset(name))
            self.assertEqual(st.cfg.name, name)

    def test_unknown_preset_raises(self):
        with self.assertRaises(ValueError):
            preset("없는프리셋")


if __name__ == "__main__":
    unittest.main()
