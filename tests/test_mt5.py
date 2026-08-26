import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from crowcode.config import preset
from crowcode.data import Candle, Series, synthetic
from crowcode.mt5.broker import AccountInfo, SymbolInfo
from crowcode.mt5.journal import Journal
from crowcode.mt5.paper import XAUUSD, PaperBroker
from crowcode.mt5.runner import LiveConfig, LiveRunner, _Managed, config_for_symbol
from crowcode.signals import Signal

MAGIC = 700911
T0 = datetime(2024, 1, 8, 9, 0, tzinfo=timezone.utc)


def flat_series(prices, start=T0):
    """종가 리스트 → 캔들 (고저는 종가에서 ±0.05)."""
    return Series(
        [Candle(start + timedelta(minutes=i), p, p + 0.05, p - 0.05, p) for i, p in enumerate(prices)],
        "XAUUSD", "M1",
    )


def quiet_runner(broker, **kw):
    live = LiveConfig(state_path="", dry_run=False, **kw)
    return LiveRunner(broker, live, preset("scalp"), Journal(echo=False))


class TestSymbolInfo(unittest.TestCase):
    def test_volume_normalization(self):
        self.assertEqual(XAUUSD.normalize_volume(0.1749), 0.17)
        self.assertEqual(XAUUSD.normalize_volume(0.004), 0.0)
        self.assertEqual(XAUUSD.normalize_volume(999), XAUUSD.volume_max)

    def test_money_per_price_unit_prefers_tick_value(self):
        info = XAUUSD  # tick_value 1.0 / tick_size 0.01 → 1랏, 1달러 이동 = 100달러
        self.assertAlmostEqual(info.money_per_price_unit(1.0), 100.0)

    def test_money_per_price_unit_falls_back_to_contract_size(self):
        info = SymbolInfo("X", 5, 0.00001, 100_000, 0.01, 100, 0.01, 0, 0, 10, 0.0, 0.0)
        self.assertAlmostEqual(info.money_per_price_unit(0.5), 50_000)

    def test_config_for_symbol_overrides_specs(self):
        cfg = config_for_symbol(preset("scalp"), XAUUSD,
                                AccountInfo(1, 5000, 5000, 5000, "USD", 30))
        self.assertAlmostEqual(cfg.contract_size, 100.0)
        self.assertEqual(cfg.min_lot, XAUUSD.volume_min)
        self.assertEqual(cfg.max_leverage, 30)   # 계좌 레버리지가 더 낮으면 그쪽을 따른다


class TestPaperBroker(unittest.TestCase):
    def setUp(self):
        self.b = PaperBroker(flat_series([1950.0] * 50), balance=5000.0)

    def test_market_order_opens_position(self):
        r = self.b.send_market("XAUUSD", "buy", 0.10, 1945.0, 1965.0, MAGIC, "t", 20)
        self.assertTrue(r.ok)
        self.assertEqual(len(self.b.positions("XAUUSD", MAGIC)), 1)

    def test_stops_level_rejects_tight_sl(self):
        r = self.b.send_market("XAUUSD", "buy", 0.10, 1949.9, 1965.0, MAGIC, "t", 20)
        self.assertFalse(r.ok)
        self.assertIn("최소 이격", r.message)

    def test_other_magic_is_invisible(self):
        self.b.send_market("XAUUSD", "buy", 0.10, 1945.0, 1965.0, 999, "t", 20)
        self.assertEqual(self.b.positions("XAUUSD", MAGIC), [])

    def test_pending_fills_when_price_reaches(self):
        b = PaperBroker(flat_series([1950, 1949, 1948, 1947, 1946]), balance=5000.0)
        r = b.send_pending("XAUUSD", "buy", 0.10, 1947.5, 1942.0, 1960.0, MAGIC, "t", None)
        self.assertTrue(r.ok, r.message)
        while b.advance():
            pass
        self.assertTrue(b.positions("XAUUSD", MAGIC) or b.deals_since("XAUUSD", MAGIC, T0))

    def test_pending_expires(self):
        b = PaperBroker(flat_series([1950] * 20), balance=5000.0)
        b.send_pending("XAUUSD", "buy", 0.10, 1940.0, 1935.0, 1960.0, MAGIC, "t",
                       T0 + timedelta(minutes=5))
        for _ in range(10):
            b.advance()
        self.assertEqual(b.orders("XAUUSD", MAGIC), [])

    def test_sl_hit_records_losing_deal(self):
        b = PaperBroker(flat_series([1950, 1949, 1946, 1944]), balance=5000.0)
        b.send_market("XAUUSD", "buy", 0.10, 1945.0, 1975.0, MAGIC, "t", 20)
        while b.advance():
            pass
        deals = b.deals_since("XAUUSD", MAGIC, T0)
        self.assertTrue(deals)
        self.assertLess(sum(d.profit for d in deals), 0)

    def test_sl_takes_priority_within_one_bar(self):
        s = Series([Candle(T0, 1950, 1950.1, 1950, 1950),
                    Candle(T0 + timedelta(minutes=1), 1950, 1980, 1940, 1970)], "XAUUSD", "M1")
        b = PaperBroker(s, balance=5000.0)
        b.send_market("XAUUSD", "buy", 0.10, 1945.0, 1975.0, MAGIC, "t", 20)
        b.advance()
        self.assertLess(sum(d.profit for d in b.deals_since("XAUUSD", MAGIC, T0)), 0)

    def test_partial_close_reduces_volume(self):
        self.b.send_market("XAUUSD", "buy", 0.10, 1945.0, 1965.0, MAGIC, "t", 20)
        t = self.b.positions("XAUUSD", MAGIC)[0].ticket
        self.assertTrue(self.b.close_partial(t, 0.05, 20).ok)
        self.assertAlmostEqual(self.b.positions("XAUUSD", MAGIC)[0].volume, 0.05)

    def test_cancel_order(self):
        r = self.b.send_pending("XAUUSD", "buy", 0.10, 1940.0, 1935.0, 1960.0, MAGIC, "t", None)
        self.assertTrue(self.b.cancel_order(r.ticket).ok)
        self.assertEqual(self.b.orders("XAUUSD", MAGIC), [])

    def test_rates_only_returns_closed_bars(self):
        b = PaperBroker(flat_series([1950 + i for i in range(60)]), start_index=30)
        self.assertEqual(len(b.rates("XAUUSD", "M1", 100)), 31)


class TestRunnerOrdering(unittest.TestCase):
    def _signal(self, side="buy", entry=1948.0, sl=1945.0, tp=1957.0, otype="limit"):
        return Signal(ts=T0, symbol="XAUUSD", side=side, entry=entry, sl=sl, tp=tp,
                      lots=0.05, risk_amount=25.0, rr=3.0, order_type=otype,
                      reasons=("테스트",))

    def test_market_order_is_sent_and_tracked(self):
        b = PaperBroker(flat_series([1950.0] * 30), balance=5000.0)
        r = quiet_runner(b)
        r.cfg = config_for_symbol(r.base_cfg, b.info, b.account())
        r._place(self._signal(otype="market"), b.info, b.account(), b.tick("XAUUSD"))
        self.assertEqual(len(b.positions("XAUUSD", MAGIC)), 1)
        self.assertEqual(len(r.managed), 1)

    def test_limit_on_wrong_side_is_skipped(self):
        b = PaperBroker(flat_series([1950.0] * 30), balance=5000.0)
        r = quiet_runner(b)
        r.cfg = config_for_symbol(r.base_cfg, b.info, b.account())
        # 매수 지정가가 현재가보다 위 → 스킵되어야 한다
        r._place(self._signal(entry=1955.0, sl=1950.0, tp=1970.0), b.info,
                 b.account(), b.tick("XAUUSD"))
        self.assertEqual(b.orders("XAUUSD", MAGIC), [])

    def test_stops_level_blocks_order(self):
        b = PaperBroker(flat_series([1950.0] * 30), balance=5000.0)
        r = quiet_runner(b)
        r.cfg = config_for_symbol(r.base_cfg, b.info, b.account())
        r._place(self._signal(entry=1949.0, sl=1948.9, tp=1949.4), b.info,
                 b.account(), b.tick("XAUUSD"))
        self.assertEqual(b.orders("XAUUSD", MAGIC), [])

    def test_dry_run_sends_nothing(self):
        b = PaperBroker(flat_series([1950.0] * 30), balance=5000.0)
        live = LiveConfig(state_path="", dry_run=True)
        r = LiveRunner(b, live, preset("scalp"), Journal(echo=False))
        r.cfg = config_for_symbol(r.base_cfg, b.info, b.account())
        r._place(self._signal(otype="market"), b.info, b.account(), b.tick("XAUUSD"))
        self.assertEqual(b.positions("XAUUSD", MAGIC), [])
        self.assertEqual(r.managed, {})

    def test_volume_is_sized_from_account_not_signal(self):
        b = PaperBroker(flat_series([1950.0] * 30), balance=20_000.0)
        r = quiet_runner(b)
        r.cfg = config_for_symbol(r.base_cfg, b.info, b.account())
        r._place(self._signal(otype="market"), b.info, b.account(), b.tick("XAUUSD"))
        pos = b.positions("XAUUSD", MAGIC)[0]
        # 리스크 0.5% = 100달러, SL 폭 약 2.2달러 → 0.4랏대 (시그널의 0.05 가 아니다)
        self.assertGreater(pos.volume, 0.05)


class TestRunnerManagement(unittest.TestCase):
    def _open(self, prices, entry_sl=1945.0, tp=1975.0):
        b = PaperBroker(flat_series(prices), balance=5000.0)
        r = quiet_runner(b)
        r.cfg = config_for_symbol(r.base_cfg, b.info, b.account())
        res = b.send_market("XAUUSD", "buy", 0.10, entry_sl, tp, MAGIC, "t", 20)
        pos = b.positions("XAUUSD", MAGIC)[0]
        r.managed[pos.ticket] = _Managed(pos.ticket, pos.price_open, entry_sl, tp, pos.volume)
        return b, r, pos

    def test_breakeven_moves_at_2r(self):
        # 진입 1950.2, SL 1945.0 → 1R ≈ 5.2 → 2R ≈ 1960.6
        b, r, pos = self._open([1950.0] * 5 + [1962.0] * 5)
        for _ in range(6):
            b.advance()
        r._manage_positions(b.positions("XAUUSD", MAGIC), b.info)
        live = b.positions("XAUUSD", MAGIC)[0]
        self.assertGreaterEqual(live.sl, pos.price_open)
        self.assertTrue(r.managed[pos.ticket].moved_to_be)

    def test_breakeven_not_moved_below_2r(self):
        b, r, pos = self._open([1950.0] * 5 + [1953.0] * 5)
        for _ in range(6):
            b.advance()
        r._manage_positions(b.positions("XAUUSD", MAGIC), b.info)
        self.assertFalse(r.managed[pos.ticket].moved_to_be)

    def test_sl_is_never_moved_backwards(self):
        b, r, pos = self._open([1950.0] * 5 + [1962.0] * 5)
        for _ in range(6):
            b.advance()
        b.modify_sltp(pos.ticket, 1958.0, pos.tp)      # 이미 더 앞으로 옮겨 둔 상태
        r._manage_positions(b.positions("XAUUSD", MAGIC), b.info)
        self.assertAlmostEqual(b.positions("XAUUSD", MAGIC)[0].sl, 1958.0)

    def test_partial_close_at_3r(self):
        # 3R ≈ 1950.2 + 15.6 ≈ 1965.8
        b, r, pos = self._open([1950.0] * 5 + [1970.0] * 5)
        for _ in range(6):
            b.advance()
        r._manage_positions(b.positions("XAUUSD", MAGIC), b.info)
        self.assertTrue(r.managed[pos.ticket].partial_done)
        self.assertAlmostEqual(b.positions("XAUUSD", MAGIC)[0].volume, 0.05)

    def test_adopts_unknown_position(self):
        b = PaperBroker(flat_series([1950.0] * 10), balance=5000.0)
        r = quiet_runner(b)
        r.cfg = config_for_symbol(r.base_cfg, b.info, b.account())
        b.send_market("XAUUSD", "buy", 0.10, 1945.0, 1975.0, MAGIC, "t", 20)
        r._manage_positions(b.positions("XAUUSD", MAGIC), b.info)
        self.assertEqual(len(r.managed), 1)

    def test_forgets_closed_positions(self):
        b, r, pos = self._open([1950.0] * 5 + [1940.0] * 5)
        for _ in range(6):
            b.advance()
        r._manage_positions(b.positions("XAUUSD", MAGIC), b.info)
        self.assertEqual(r.managed, {})


class TestRunnerPendings(unittest.TestCase):
    def test_expired_pending_is_cancelled(self):
        b = PaperBroker(flat_series([1950.0] * 400), balance=5000.0)
        r = quiet_runner(b)
        r.cfg = config_for_symbol(r.base_cfg, b.info, b.account())
        b.send_pending("XAUUSD", "buy", 0.10, 1940.0, 1935.0, 1970.0, MAGIC, "t", None)
        later = b.now() + timedelta(days=1)
        r._review_pendings(b.orders("XAUUSD", MAGIC), later)
        self.assertEqual(b.orders("XAUUSD", MAGIC), [])

    def test_structure_break_cancels_pending(self):
        # 종가가 SL(1935) 아래로 마감 → 셋업 전제 붕괴
        b = PaperBroker(flat_series([1950.0] * 5 + [1930.0] * 5), balance=5000.0)
        r = quiet_runner(b)
        r.cfg = config_for_symbol(r.base_cfg, b.info, b.account())
        b.send_pending("XAUUSD", "buy", 0.10, 1940.0, 1935.0, 1970.0, MAGIC, "t", None)
        for _ in range(6):
            b.advance()
        r._review_pendings(b.orders("XAUUSD", MAGIC), b.now())
        self.assertEqual(b.orders("XAUUSD", MAGIC), [])

    def test_healthy_pending_survives(self):
        b = PaperBroker(flat_series([1950.0] * 10), balance=5000.0)
        r = quiet_runner(b)
        r.cfg = config_for_symbol(r.base_cfg, b.info, b.account())
        b.send_pending("XAUUSD", "buy", 0.10, 1940.0, 1935.0, 1970.0, MAGIC, "t", None)
        b.advance()
        r._review_pendings(b.orders("XAUUSD", MAGIC), b.now())
        self.assertEqual(len(b.orders("XAUUSD", MAGIC)), 1)


class TestPendingToPositionHandover(unittest.TestCase):
    def test_initial_risk_survives_the_fill(self):
        """대기 주문이 체결될 때 최초 리스크 기록이 사라지면 안 된다.

        기록을 잃으면 본절 이동 기준(1R)이 체결 후의 SL 로 재계산되어
        2R 시점이 어긋난다.
        """
        b = PaperBroker(flat_series([1950.0] * 6 + [1946.0] * 6), balance=5000.0)
        r = quiet_runner(b)
        r.cfg = config_for_symbol(r.base_cfg, b.info, b.account())
        sig = Signal(ts=T0, symbol="XAUUSD", side="buy", entry=1947.0, sl=1943.0,
                     tp=1959.0, lots=0.05, risk_amount=25.0, rr=3.0, order_type="limit")
        r._place(sig, b.info, b.account(), b.tick("XAUUSD"))
        ticket = b.orders("XAUUSD", MAGIC)[0].ticket
        before = r.managed[ticket].initial_sl

        while b.advance():
            r._manage_positions(b.positions("XAUUSD", MAGIC), b.info,
                                b.orders("XAUUSD", MAGIC))
            if b.positions("XAUUSD", MAGIC):
                break

        self.assertIn(ticket, r.managed)
        self.assertAlmostEqual(r.managed[ticket].initial_sl, before)


class TestRunnerRiskGate(unittest.TestCase):
    def test_two_losses_today_halt_trading(self):
        b = PaperBroker(flat_series([1950.0] * 30), balance=5000.0)
        r = quiet_runner(b)
        r.cfg = config_for_symbol(r.base_cfg, b.info, b.account())
        for _ in range(2):
            res = b.send_market("XAUUSD", "buy", 0.10, 1945.0, 1975.0, MAGIC, "t", 20)
            b._settle(res.ticket, 1945.0, b.now())          # 손절 체결 시뮬레이션
        st = r._risk_state(b.account(), b.now())
        ok, why = st.can_trade(b.now(), r.cfg)
        self.assertFalse(ok)
        self.assertIn("연속 손절", why)

    def test_partial_closes_count_as_one_trade(self):
        b = PaperBroker(flat_series([1950.0] * 30), balance=5000.0)
        r = quiet_runner(b)
        r.cfg = config_for_symbol(r.base_cfg, b.info, b.account())
        res = b.send_market("XAUUSD", "buy", 0.10, 1945.0, 1975.0, MAGIC, "t", 20)
        b._settle(res.ticket, 1955.0, b.now(), 0.05)        # 분할
        b._settle(res.ticket, 1960.0, b.now())              # 잔량
        st = r._risk_state(b.account(), b.now())
        self.assertEqual(st.trades_today, 1)

    def test_winning_day_allows_trading(self):
        b = PaperBroker(flat_series([1950.0] * 30), balance=5000.0)
        r = quiet_runner(b)
        r.cfg = config_for_symbol(r.base_cfg, b.info, b.account())
        res = b.send_market("XAUUSD", "buy", 0.10, 1945.0, 1975.0, MAGIC, "t", 20)
        b._settle(res.ticket, 1960.0, b.now())
        st = r._risk_state(b.account(), b.now())
        self.assertTrue(st.can_trade(b.now(), r.cfg)[0])


class TestState(unittest.TestCase):
    def test_state_survives_restart(self):
        path = os.path.join(tempfile.mkdtemp(), "state.json")
        b = PaperBroker(flat_series([1950.0] * 30), balance=5000.0)
        live = LiveConfig(state_path=path, dry_run=False)
        r1 = LiveRunner(b, live, preset("scalp"), Journal(echo=False))
        r1.managed[42] = _Managed(42, 1950.0, 1945.0, 1975.0, 0.10, moved_to_be=True)
        r1._save_state()

        r2 = LiveRunner(b, live, preset("scalp"), Journal(echo=False))
        self.assertIn(42, r2.managed)
        self.assertTrue(r2.managed[42].moved_to_be)
        self.assertAlmostEqual(r2.managed[42].risk, 5.0)

    def test_corrupt_state_is_ignored(self):
        path = os.path.join(tempfile.mkdtemp(), "bad.json")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{ not json")
        b = PaperBroker(flat_series([1950.0] * 30), balance=5000.0)
        r = LiveRunner(b, LiveConfig(state_path=path), preset("scalp"), Journal(echo=False))
        self.assertEqual(r.managed, {})


class TestJournal(unittest.TestCase):
    def test_writes_jsonl(self):
        path = os.path.join(tempfile.mkdtemp(), "j.jsonl")
        j = Journal(path, echo=False)
        j.write("order", symbol="XAUUSD", ts_field=T0, reasons=["a", "b"])
        with open(path, encoding="utf-8") as fh:
            rec = json.loads(fh.readline())
        self.assertEqual(rec["kind"], "order")
        self.assertEqual(rec["ts_field"], T0.isoformat())
        self.assertEqual(rec["reasons"], ["a", "b"])


class TestEndToEnd(unittest.TestCase):
    def test_paper_loop_runs_without_error(self):
        b = PaperBroker(synthetic(3000), balance=5000.0, start_index=800)
        r = quiet_runner(b)
        steps = 0
        while b.advance() and steps < 1200:
            r.step()
            steps += 1
        self.assertGreater(steps, 0)
        self.assertTrue(r._reject_counts, "필터가 한 번도 동작하지 않음")

    def test_new_bar_gate_prevents_duplicate_evaluation(self):
        b = PaperBroker(synthetic(1500), balance=5000.0, start_index=800)
        r = quiet_runner(b)
        b.advance()
        r.step()
        first = r._last_bar_ts
        r.step()                      # 같은 봉에서 재평가하면 안 된다
        self.assertEqual(r._last_bar_ts, first)


if __name__ == "__main__":
    unittest.main()
