import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from crowcode import review as rv
from crowcode.config import preset
from crowcode.data import Candle, Series

T0 = datetime(2024, 1, 8, 9, 0, tzinfo=timezone.utc)
CFG = preset("intraday")


def series(paths, start=T0):
    """(low, high) 쌍 목록으로 캔들을 만든다."""
    out = []
    for i, (lo, hi) in enumerate(paths):
        out.append(Candle(start + timedelta(minutes=i), (lo + hi) / 2, hi, lo, (lo + hi) / 2))
    return Series(out, "XAUUSD", "M1")


def journal_file(records):
    path = os.path.join(tempfile.mkdtemp(), "j.jsonl")
    with open(path, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return path


def order_rec(ticket=1, side="buy", entry=1950.0, sl=1945.0, tp=1965.0, reasons=()):
    return {"ts": T0.isoformat(), "kind": "order", "result_ticket": ticket, "side": side,
            "entry": entry, "sl": sl, "tp": tp, "volume": 0.1, "type": "limit",
            "reasons": list(reasons)}


def closed_rec(ticket=1, pnl=-50.0, r=-1.0, entry=1950.0, sl=1945.0, tp=1965.0,
               opened=T0, closed=None):
    return {"ts": T0.isoformat(), "kind": "closed", "ticket": ticket, "pnl": pnl, "r": r,
            "entry": entry, "initial_sl": sl, "tp": tp, "volume": 0.1,
            "moved_to_be": False, "partial_done": False,
            "opened_at": opened.isoformat(),
            "closed_at": (closed or opened + timedelta(minutes=30)).isoformat()}


class TestJournalParsing(unittest.TestCase):
    def test_pairs_order_with_close(self):
        path = journal_file([order_rec(reasons=("세션: London",)), closed_rec()])
        trades = rv.build_trades(rv.load_journal(path))
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0].side, "buy")
        self.assertAlmostEqual(trades[0].risk, 5.0)
        self.assertEqual(trades[0].reasons, ("세션: London",))

    def test_unclosed_orders_are_skipped(self):
        path = journal_file([order_rec(), order_rec(ticket=2)])
        self.assertEqual(rv.build_trades(rv.load_journal(path)), [])

    def test_close_without_order_still_usable(self):
        path = journal_file([closed_rec()])
        trades = rv.build_trades(rv.load_journal(path))
        self.assertEqual(len(trades), 1)

    def test_missing_file_is_empty(self):
        self.assertEqual(rv.load_journal("/nope/none.jsonl"), [])

    def test_corrupt_lines_are_skipped(self):
        path = journal_file([order_rec(), closed_rec()])
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("{ not json\n")
        self.assertEqual(len(rv.build_trades(rv.load_journal(path))), 1)

    def test_trades_are_time_ordered(self):
        recs = [order_rec(1), closed_rec(1, opened=T0 + timedelta(hours=2)),
                order_rec(2), closed_rec(2, opened=T0)]
        trades = rv.build_trades(recs)
        self.assertEqual([t.ticket for t in trades], [2, 1])

    def test_poi_kind_extracted_from_reasons(self):
        recs = [order_rec(reasons=("진입 POI: order_block 1940.0~1945.0 (지정가)",)),
                closed_rec()]
        self.assertEqual(rv.build_trades(recs)[0].poi_kind, "order_block")


class TestEnrichment(unittest.TestCase):
    def test_mfe_and_mae(self):
        # 진입 1950, 손절 1945 (1R = 5). 최고 1960(+2R), 최저 1947.5(-0.5R)
        s = series([(1949, 1951), (1947.5, 1955), (1948, 1960), (1949, 1952)])
        trades = rv.build_trades([order_rec(), closed_rec(
            closed=T0 + timedelta(minutes=3))])
        rv.enrich(trades, s)
        t = trades[0]
        self.assertAlmostEqual(t.mfe_r, 2.0, places=2)
        self.assertAlmostEqual(t.mae_r, 0.5, places=2)
        self.assertEqual(t.bars_held, 3)

    def test_sell_side_is_mirrored(self):
        s = series([(1949, 1951), (1940, 1951), (1938, 1948)])
        trades = rv.build_trades([
            order_rec(side="sell", entry=1950.0, sl=1955.0, tp=1935.0),
            closed_rec(sl=1955.0, tp=1935.0, closed=T0 + timedelta(minutes=2))])
        rv.enrich(trades, s)
        self.assertAlmostEqual(trades[0].mfe_r, 2.4, places=2)   # (1950-1938)/5

    def test_detects_stop_run(self):
        """손절로 끝난 뒤 목표까지 갔다면 손절 자리가 문제다."""
        s = series([(1949, 1951), (1944, 1950), (1950, 1966), (1960, 1968)])
        trades = rv.build_trades([order_rec(), closed_rec(
            pnl=-50.0, closed=T0 + timedelta(minutes=1))])
        rv.enrich(trades, s, min_lookahead=5)
        self.assertTrue(trades[0].tp_after_stop)

    def test_no_stop_run_when_price_keeps_going(self):
        s = series([(1949, 1951), (1944, 1950), (1935, 1943), (1930, 1938)])
        trades = rv.build_trades([order_rec(), closed_rec(
            pnl=-50.0, closed=T0 + timedelta(minutes=1))])
        rv.enrich(trades, s, min_lookahead=5)
        self.assertFalse(trades[0].tp_after_stop)

    def test_without_series_nothing_is_computed(self):
        trades = rv.build_trades([order_rec(), closed_rec()])
        rv.enrich(trades, None)
        self.assertIsNone(trades[0].mfe_r)


class TestClassification(unittest.TestCase):
    def _t(self, **kw):
        base = dict(ticket=1, side="buy", entry=1950.0, sl=1945.0, tp=1965.0,
                    volume=0.1, opened_at=T0, closed_at=T0 + timedelta(minutes=10),
                    pnl=-50.0, r=-1.0)
        base.update(kw)
        return rv.ReviewTrade(**base)

    def test_win(self):
        self.assertEqual(rv.classify(self._t(pnl=150.0, r=3.0, mfe_r=3.0), CFG), "win")

    def test_stop_hunted(self):
        t = self._t(mfe_r=0.8, mae_r=1.0, tp_after_stop=True)
        self.assertEqual(rv.classify(t, CFG), "stop_hunted")

    def test_gave_back(self):
        t = self._t(mfe_r=2.6, mae_r=1.0, tp_after_stop=False)
        self.assertEqual(rv.classify(t, CFG), "gave_back")

    def test_near_miss(self):
        t = self._t(mfe_r=2.2, mae_r=1.0, tp_after_stop=False)
        self.assertEqual(rv.classify(t, CFG.with_(breakeven_at_r=9.0)), "near_miss")

    def test_wrong_way(self):
        t = self._t(mfe_r=0.2, mae_r=1.0, tp_after_stop=False)
        self.assertEqual(rv.classify(t, CFG), "wrong_way")

    def test_chop(self):
        t = self._t(mfe_r=1.0, mae_r=1.0, tp_after_stop=False, r=-1.0)
        self.assertEqual(rv.classify(t, CFG), "chop")

    def test_unknown_without_price_data(self):
        self.assertEqual(rv.classify(self._t(), CFG), "unknown")


class TestDiagnosis(unittest.TestCase):
    def _many(self, verdict_kw, n=6):
        out = []
        for i in range(n):
            out.append(rv.ReviewTrade(
                ticket=i, side="buy", entry=1950.0, sl=1945.0, tp=1965.0, volume=0.1,
                opened_at=T0 + timedelta(hours=i), closed_at=T0 + timedelta(hours=i, minutes=20),
                pnl=-50.0, r=-1.0, **verdict_kw))
        return out

    def test_stop_hunt_cluster_suggests_a_wider_buffer(self):
        d = rv.diagnose(self._many(dict(mfe_r=0.8, mae_r=1.0, tp_after_stop=True)), CFG)
        self.assertTrue(d.findings)
        self.assertIn("sl_buffer_atr", d.findings[0].knob)

    def test_wrong_way_cluster_points_at_the_htf(self):
        d = rv.diagnose(self._many(dict(mfe_r=0.1, mae_r=1.0, tp_after_stop=False)), CFG)
        self.assertIn("htf", d.findings[0].knob)

    def test_breakeven_exits_are_diagnosed_too(self):
        """본절은 손실이 아니지만 2R 갔다가 0으로 끝난 건 고칠 거리다."""
        trades = self._many(dict(mfe_r=2.8, mae_r=0.2, tp_after_stop=False))
        for t in trades:
            t.pnl, t.r = 0.0, 0.0
        d = rv.diagnose(trades, CFG)
        self.assertEqual(len(d.flats), 6)
        self.assertTrue(any("breakeven_at_r" in f.knob for f in d.findings))

    def test_no_findings_when_everything_wins(self):
        wins = self._many(dict(mfe_r=3.0, mae_r=0.3))
        for t in wins:
            t.pnl, t.r = 150.0, 3.0
        d = rv.diagnose(wins, CFG)
        self.assertEqual(d.findings, [])

    def test_session_skew_is_flagged(self):
        """런던은 다 지고 뉴욕은 다 이기면 런던을 빼라고 말해야 한다."""
        losing = self._many(dict(mfe_r=1.0, mae_r=1.0, tp_after_stop=False), n=4)
        for t in losing:
            t.opened_at = T0.replace(hour=8)               # 런던
        winning = self._many(dict(mfe_r=3.0, mae_r=0.3), n=4)
        for t in winning:
            t.opened_at = T0.replace(hour=14)              # 뉴욕
            t.pnl, t.r = 150.0, 3.0
        d = rv.diagnose(losing + winning, CFG)
        self.assertTrue(any("세션" in f.title and "London" in f.title for f in d.findings))

    def test_single_session_sample_is_not_called_a_skew(self):
        """거래가 원래 한 세션에만 있으면 편중이라고 말하면 안 된다."""
        trades = self._many(dict(mfe_r=1.0, mae_r=1.0, tp_after_stop=False), n=5)
        for t in trades:
            t.opened_at = T0.replace(hour=8)
        d = rv.diagnose(trades, CFG)
        self.assertFalse(any("세션" in f.title for f in d.findings))

    def test_average_mfe_below_target_suggests_lower_target(self):
        trades = self._many(dict(mfe_r=1.2, mae_r=0.8, tp_after_stop=False), n=8)
        d = rv.diagnose(trades, CFG)
        self.assertTrue(any("target_rr" in f.knob for f in d.findings))


class TestReports(unittest.TestCase):
    def _diag(self):
        trades = rv.build_trades([order_rec(reasons=("세션: London",)), closed_rec()])
        s = series([(1949, 1951), (1944, 1960), (1948, 1955)])
        rv.enrich(trades, s)
        return rv.diagnose(trades, CFG), s

    def test_text_report(self):
        d, _ = self._diag()
        text = rv.text_report(d)
        self.assertIn("복기", text)
        self.assertIn("판정 분포", text)
        self.assertIn("표본", text)

    def test_text_report_with_no_trades(self):
        self.assertIn("기록된 거래가 없다", rv.text_report(rv.Diagnosis([], CFG)))

    def test_html_report_is_self_contained(self):
        d, s = self._diag()
        html = rv.html_report(d, s)
        self.assertIn("<svg", html)
        self.assertIn("<style>", html)
        self.assertNotIn("http://", html)
        self.assertNotIn("https://", html)

    def test_html_handles_missing_price_data(self):
        trades = rv.build_trades([order_rec(), closed_rec()])
        html = rv.html_report(rv.diagnose(trades, CFG), None)
        self.assertIn("거래 목록", html)

    def test_html_escapes_reason_text(self):
        trades = rv.build_trades([order_rec(reasons=("<script>x</script>",)), closed_rec()])
        s = series([(1949, 1951), (1944, 1960)])
        rv.enrich(trades, s)
        html = rv.html_report(rv.diagnose(trades, CFG), s)
        self.assertNotIn("<script>", html)


class TestFromBacktest(unittest.TestCase):
    def test_converts_backtest_trades(self):
        from crowcode.backtest import Backtester
        from crowcode.data import synthetic

        s = synthetic(9000, minutes=5)
        res = Backtester(preset("intraday"), 5000.0, spread=0.25,
                         warmup=600, eval_every=3).run(s)
        trades = rv.from_backtest(res)
        self.assertEqual(len(trades), len(res.trades))
        if trades:
            rv.enrich(trades, s)
            d = rv.diagnose(trades, preset("intraday"))
            self.assertTrue(rv.text_report(d))


if __name__ == "__main__":
    unittest.main()


class TestOverrun(unittest.TestCase):
    """손절이 지켜지지 않은 거래는 전략 문제가 아니라 체결 문제다."""

    def _t(self, r, **kw):
        base = dict(ticket=1, side="buy", entry=1950.0, sl=1945.0, tp=1965.0,
                    volume=0.1, opened_at=T0, closed_at=T0 + timedelta(minutes=10),
                    pnl=r * 50.0, r=r)
        base.update(kw)
        return rv.ReviewTrade(**base)

    def test_loss_far_beyond_one_r_is_overrun(self):
        self.assertEqual(rv.classify(self._t(-2.4, mfe_r=0.1, mae_r=2.4), CFG), "overrun")

    def test_normal_stop_is_not_overrun(self):
        self.assertNotEqual(rv.classify(self._t(-1.0, mfe_r=0.1, mae_r=1.0), CFG), "overrun")

    def test_detected_without_price_data(self):
        self.assertEqual(rv.classify(self._t(-2.4), CFG), "overrun")

    def test_cluster_points_at_execution_not_strategy(self):
        trades = [self._t(-2.4, ticket=i, mfe_r=0.1, mae_r=2.4,
                          opened_at=T0 + timedelta(hours=i)) for i in range(5)]
        d = rv.diagnose(trades, CFG)
        self.assertTrue(any("체결 문제" in f.detail for f in d.findings))
