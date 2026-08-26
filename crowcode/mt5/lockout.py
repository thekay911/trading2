"""서킷브레이커 잠금.

당일 손실이 한도에 닿으면 매매를 멈추는 것까지는 리스크 게이트가 한다.
문제는 그게 **자정에 저절로 풀린다**는 것이다. 진 날을 그냥 넘기고
다음 날 같은 설정으로 재개하면 같은 실패를 반복한다.

이 모듈은 그 자동 해제를 막는다.
  손실 한도 도달 → 잠금 기록 → 복기(`crowcode review`) → 사람이 해제(`crowcode release`)
잠금은 파일에 남으므로 프로세스를 재시작해도, 날짜가 바뀌어도 유지된다.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone


@dataclass
class Lockout:
    """왜, 언제 잠겼는지. 해제하려면 사람이 사유를 적어야 한다."""
    locked_at: str                 # ISO8601
    trading_day: str               # YYYY-MM-DD
    reason: str
    balance_at_lock: float
    loss_amount: float
    loss_pct: float
    trades: int = 0
    released_at: str | None = None
    released_note: str | None = None

    @property
    def active(self) -> bool:
        return self.released_at is None

    def summary(self) -> str:
        lines = [
            "=" * 60,
            " 매매 잠금 (서킷브레이커)",
            "=" * 60,
            f" 발생일      : {self.trading_day}  ({self.locked_at})",
            f" 사유        : {self.reason}",
            f" 당일 손실   : {self.loss_amount:,.2f}  ({self.loss_pct:.2f}%)",
            f" 잠금 시 잔고: {self.balance_at_lock:,.2f}",
            f" 당일 거래   : {self.trades}건",
            "-" * 60,
        ]
        if self.active:
            lines += [
                " 상태: 잠김 — 복기 전까지 신규 진입이 차단된다.",
                "",
                " 1) 복기   python3 -m crowcode review --journal state/journal.jsonl",
                " 2) 수정   설정을 고쳤다면 무엇을 왜 바꿨는지 메모로 남길 것",
                " 3) 해제   python3 -m crowcode release --note \"...\"",
            ]
        else:
            lines += [f" 상태: 해제됨 ({self.released_at})",
                      f" 메모: {self.released_note or '-'}"]
        lines.append("=" * 60)
        return "\n".join(lines)


class LockoutStore:
    """잠금 상태를 파일 하나로 관리한다 (이력도 함께 남긴다)."""

    def __init__(self, path: str = "state/lockout.json"):
        self.path = path

    # ------------------------------------------------------------------
    def _read(self) -> dict:
        if not self.path or not os.path.exists(self.path):
            return {"current": None, "history": []}
        try:
            with open(self.path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {"current": None, "history": []}

    def _write(self, data: dict) -> None:
        if not self.path:
            return
        os.makedirs(os.path.dirname(os.path.abspath(self.path)) or ".", exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    # ------------------------------------------------------------------
    def current(self) -> Lockout | None:
        raw = self._read().get("current")
        return Lockout(**raw) if raw else None

    def is_locked(self) -> bool:
        cur = self.current()
        return cur is not None and cur.active

    def history(self) -> list[Lockout]:
        return [Lockout(**r) for r in self._read().get("history", [])]

    def lock(self, reason: str, balance: float, loss_amount: float,
             loss_pct: float, trades: int, now: datetime | None = None) -> Lockout:
        """이미 잠겨 있으면 기존 잠금을 그대로 둔다 (사유가 덮이지 않게)."""
        cur = self.current()
        if cur is not None and cur.active:
            return cur
        ts = now or datetime.now(timezone.utc)
        lock = Lockout(
            locked_at=ts.isoformat(timespec="seconds"),
            trading_day=ts.date().isoformat(),
            reason=reason, balance_at_lock=round(balance, 2),
            loss_amount=round(loss_amount, 2), loss_pct=round(loss_pct, 2),
            trades=trades,
        )
        data = self._read()
        data["current"] = asdict(lock)
        self._write(data)
        return lock

    def release(self, note: str, now: datetime | None = None) -> Lockout | None:
        """복기를 마쳤을 때만 사람이 호출한다. 메모는 필수다."""
        cur = self.current()
        if cur is None or not cur.active:
            return None
        ts = now or datetime.now(timezone.utc)
        cur.released_at = ts.isoformat(timespec="seconds")
        cur.released_note = note
        data = self._read()
        data["current"] = None
        data.setdefault("history", []).append(asdict(cur))
        self._write(data)
        return cur
