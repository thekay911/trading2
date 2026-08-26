import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from crowcode.config import preset
from crowcode.data import Candle, Series
from crowcode.mt5.journal import Journal
from crowcode.mt5.lockout import Lockout, LockoutStore
from crowcode.mt5.paper import PaperBroker
from crowcode.mt5.runner import LiveConfig, LiveRunner

MAGIC = 700911
T0 = datetime(2024, 1, 8, 9, 0, tzinfo=timezone.utc)


def tmp_path(name="lockout.json"):
    return os.path.join(tempfile.mkdtemp(), name)


def flat_series(prices, start=T0):
    return Series(
        [Candle(start + timedelta(minutes=i), p, p + 0.05, p - 0.05, p)
         for i, p in enumerate(prices)],
        "XAUUSD", "M1",
    )


class TestLockoutStore(unittest.TestCase):
    def setUp(self):
        self.store = LockoutStore(tmp_path())

    def test_starts_unlocked(self):
        self.assertFalse(self.store.is_locked())
        self.assertIsNone(self.store.current())

    def test_lock_records_the_reason(self):
        lock = self.store.lock("서킷 도달", 4500.0, -500.0, 10.0, 3, T0)
        self.assertTrue(self.store.is_locked())
        self.assertEqual(lock.trading_day, "2024-01-08")
        self.assertAlmostEqual(lock.loss_pct, 10.0)
        self.assertEqual(lock.trades, 3)

    def test_second_lock_does_not_overwrite_the_first(self):
        first = self.store.lock("첫 사유", 4500.0, -500.0, 10.0, 3, T0)
        again = self.store.lock("다른 사유", 4000.0, -900.0, 18.0, 5,
                                T0 + timedelta(hours=2))
        self.assertEqual(again.reason, first.reason)
        self.assertEqual(again.locked_at, first.locked_at)

    def test_lock_survives_a_new_store_instance(self):
        self.store.lock("서킷", 4500.0, -500.0, 10.0, 2, T0)
        again = LockoutStore(self.store.path)
        self.assertTrue(again.is_locked())

    def test_does_not_expire_with_the_calendar(self):
        """이게 핵심이다 — 자정에 저절로 풀리면 안 된다."""
        self.store.lock("서킷", 4500.0, -500.0, 10.0, 2, T0)
        self.assertTrue(self.store.is_locked())
        # 며칠이 지나도 여전히 잠겨 있어야 한다
        self.assertTrue(LockoutStore(self.store.path).is_locked())

    def test_release_moves_it_to_history(self):
        self.store.lock("서킷", 4500.0, -500.0, 10.0, 2, T0)
        released = self.store.release("손절 버퍼 0.3→0.5", T0 + timedelta(days=1))
        self.assertIsNotNone(released)
        self.assertFalse(self.store.is_locked())
        hist = self.store.history()
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0].released_note, "손절 버퍼 0.3→0.5")

    def test_release_without_lock_is_noop(self):
        self.assertIsNone(self.store.release("아무거나"))

    def test_history_accumulates(self):
        for i in range(3):
            self.store.lock(f"{i}회차", 4500.0, -500.0, 10.0, 2, T0 + timedelta(days=i))
            self.store.release(f"수정 {i}", T0 + timedelta(days=i, hours=1))
        self.assertEqual(len(self.store.history()), 3)

    def test_empty_path_disables_persistence(self):
        store = LockoutStore("")
        store.lock("서킷", 4500.0, -500.0, 10.0, 2, T0)
        self.assertFalse(store.is_locked())

    def test_corrupt_file_is_treated_as_unlocked(self):
        path = tmp_path()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{ broken")
        self.assertFalse(LockoutStore(path).is_locked())

    def test_summary_renders_both_states(self):
        self.store.lock("서킷", 4500.0, -500.0, 10.0, 2, T0)
        self.assertIn("잠김", self.store.current().summary())
        self.store.release("고침", T0 + timedelta(days=1))
        self.assertIn("해제됨", self.store.history()[0].summary())


class TestCircuitBreaker(unittest.TestCase):
    """당일 손실이 서킷에 닿으면 잠기고, 잠긴 동안 신규 진입이 막혀야 한다."""

    def _runner(self, prices, balance=5000.0, **cfg_kw):
        broker = PaperBroker(flat_series(prices), balance=balance)
        cfg = preset("intraday").with_(**cfg_kw) if cfg_kw else preset("intraday")
        live = LiveConfig(state_path="", lockout_path=tmp_path(), dry_run=False)
        return broker, LiveRunner(broker, live, cfg, Journal(echo=False))

    def _lose(self, broker, runner, pct, balance=5000.0):
        """지정한 비율만큼 손실을 낸 것처럼 체결 내역을 만든다."""
        target = balance * pct / 100.0
        res = broker.send_market("XAUUSD", "buy", 0.10, 1945.0, 1975.0, MAGIC, "t", 20)
        loss_price = 1950.0 - target / (0.10 * broker.info.contract_size)
        broker._settle(res.ticket, loss_price, broker.now())

    def test_locks_when_daily_loss_reaches_the_circuit(self):
        broker, runner = self._runner([1950.0] * 40)
        runner.cfg = runner.base_cfg
        self._lose(broker, runner, 11.0)
        risk = runner._risk_state(broker.account(), broker.now())
        self.assertTrue(runner._check_circuit_breaker(risk, broker.account(), broker.now()))
        self.assertTrue(runner.lockout.is_locked())

    def test_does_not_lock_below_the_threshold(self):
        broker, runner = self._runner([1950.0] * 40)
        runner.cfg = runner.base_cfg
        self._lose(broker, runner, 4.0)
        risk = runner._risk_state(broker.account(), broker.now())
        self.assertFalse(runner._check_circuit_breaker(risk, broker.account(), broker.now()))
        self.assertFalse(runner.lockout.is_locked())

    def test_auto_release_when_review_not_required(self):
        broker, runner = self._runner([1950.0] * 40, halt_requires_review=False)
        runner.cfg = runner.base_cfg
        self._lose(broker, runner, 11.0)
        risk = runner._risk_state(broker.account(), broker.now())
        runner._check_circuit_breaker(risk, broker.account(), broker.now())
        self.assertFalse(runner.lockout.is_locked())
        self.assertEqual(len(runner.lockout.history()), 1)

    def test_disabled_when_threshold_is_zero(self):
        broker, runner = self._runner([1950.0] * 40, hard_stop_loss_pct=0.0)
        runner.cfg = runner.base_cfg
        self._lose(broker, runner, 30.0)
        risk = runner._risk_state(broker.account(), broker.now())
        self.assertFalse(runner._check_circuit_breaker(risk, broker.account(), broker.now()))

    def test_locked_runner_refuses_new_entries(self):
        broker, runner = self._runner([1950.0] * 200)
        runner.lockout.lock("테스트", 5000.0, -500.0, 10.0, 2, broker.now())
        for _ in range(30):
            broker.advance()
            self.assertIsNone(runner.step())
        self.assertIn("locked", runner._reject_counts)

    def test_locked_runner_still_manages_open_positions(self):
        """잠금은 신규 진입만 막는다. 관리까지 멈추면 손절도 안 옮겨진다."""
        broker, runner = self._runner([1950.0] * 6 + [1962.0] * 10)
        runner.cfg = runner.base_cfg
        res = broker.send_market("XAUUSD", "buy", 0.10, 1945.0, 1990.0, MAGIC, "t", 20)
        pos = broker.positions("XAUUSD", MAGIC)[0]
        from crowcode.mt5.runner import _Managed
        runner.managed[pos.ticket] = _Managed(pos.ticket, pos.price_open, 1945.0, 1990.0,
                                              pos.volume)
        runner.lockout.lock("테스트", 5000.0, -500.0, 10.0, 2, broker.now())
        for _ in range(8):
            broker.advance()
            runner.step()
        live = broker.positions("XAUUSD", MAGIC)
        self.assertTrue(live, "포지션이 사라졌다")
        self.assertGreaterEqual(live[0].sl, pos.price_open, "잠금 중에 본절 이동이 안 됨")

    def test_release_lets_trading_resume(self):
        broker, runner = self._runner([1950.0] * 60)
        runner.lockout.lock("테스트", 5000.0, -500.0, 10.0, 2, broker.now())
        broker.advance()
        runner.step()
        self.assertIn("locked", runner._reject_counts)
        runner.lockout.release("복기 완료")
        runner._reject_counts.clear()
        broker.advance()
        runner.step()
        self.assertNotIn("locked", runner._reject_counts)


if __name__ == "__main__":
    unittest.main()
