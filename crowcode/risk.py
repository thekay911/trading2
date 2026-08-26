"""자금관리 — 채널이 가장 강조하는 부분.

  "quản lý vốn là thứ quan trọng bậc nhất" (자금관리가 제일 중요하다)
  "1틱당 계좌 1%", "2R 가면 본절", "SL 은 절대 밀지 않는다",
  "2번 손절 나면 그날은 끝", "레버리지 1:50 또는 1:20 으로 낮춰라",
  "여유 자금이면 계좌를 스윙/스캘핑/고위험 3개로 나눠라"
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

from crowcode.config import CrowConfig
from crowcode.signals import Side


# ----------------------------------------------------------------------
# 포지션 사이징
# ----------------------------------------------------------------------
def round_lots(lots: float, step: float, min_lot: float, max_lot: float) -> float:
    if step <= 0:
        return max(min_lot, min(lots, max_lot))
    stepped = math.floor(lots / step + 1e-9) * step
    stepped = round(stepped, 8)
    if stepped < min_lot:
        return 0.0
    return min(stepped, max_lot)


def max_lots_by_leverage(balance: float, leverage: int, contract_size: float, price: float) -> float:
    """레버리지 상한이 만들어 주는 자동 랏 상한.

    채널의 논리: 레버리지를 낮춰 두면 플랫폼이 과대 베팅을 막아 준다.
    """
    if price <= 0 or contract_size <= 0:
        return 0.0
    return (balance * leverage) / (contract_size * price)


def position_size(
    balance: float,
    risk_pct: float,
    entry: float,
    sl: float,
    cfg: CrowConfig,
) -> tuple[float, float]:
    """(랏, 실제 리스크 금액) 반환. SL 거리가 0이면 거래 불가."""
    dist = abs(entry - sl)
    if dist <= 0 or balance <= 0:
        return 0.0, 0.0
    risk_amount = balance * (risk_pct / 100.0)
    raw = risk_amount / (dist * cfg.contract_size)
    cap = max_lots_by_leverage(balance, cfg.max_leverage, cfg.contract_size, entry)
    lots = round_lots(min(raw, cap), cfg.lot_step, cfg.min_lot, cfg.max_lot)
    return lots, lots * dist * cfg.contract_size


# ----------------------------------------------------------------------
# 계좌 분리
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class AccountSplit:
    swing: float
    scalp: float
    high_risk: float

    def as_dict(self) -> dict[str, float]:
        return {"swing": self.swing, "scalp": self.scalp, "high_risk": self.high_risk}


def split_capital(total: float, cfg: CrowConfig) -> AccountSplit:
    a, b, c = cfg.account_split
    s = a + b + c
    return AccountSplit(round(total * a / s, 2), round(total * b / s, 2), round(total * c / s, 2))


# ----------------------------------------------------------------------
# 거래 관리 (본절 / 분할 / 일일 한도)
# ----------------------------------------------------------------------
@dataclass
class ManagedPosition:
    side: Side
    entry: float
    sl: float
    tp: float
    lots: float
    initial_risk: float          # 가격 단위 1R
    opened_at: datetime
    moved_to_be: bool = False
    partial_done: bool = False
    remaining: float = 1.0       # 남은 비중

    def r_multiple(self, price: float) -> float:
        if self.initial_risk <= 0:
            return 0.0
        d = price - self.entry if self.side == "buy" else self.entry - price
        return d / self.initial_risk

    def update(self, price: float, cfg: CrowConfig) -> list[str]:
        """가격이 갱신될 때 호출. 수행된 관리 동작 목록을 돌려준다."""
        acts: list[str] = []
        r = self.r_multiple(price)

        if not self.moved_to_be and r >= cfg.breakeven_at_r:
            new_sl = self.entry
            if self._sl_is_forward(new_sl, cfg):
                self.sl = new_sl
                self.moved_to_be = True
                acts.append(f"{cfg.breakeven_at_r:g}R 도달 → SL 본절 이동")

        if not self.partial_done and r >= cfg.partial_at_r and cfg.partial_fraction > 0:
            self.remaining = max(0.0, self.remaining - cfg.partial_fraction)
            self.partial_done = True
            acts.append(f"{cfg.partial_at_r:g}R 도달 → {cfg.partial_fraction:.0%} 분할 청산")
        return acts

    def _sl_is_forward(self, new_sl: float, cfg: CrowConfig) -> bool:
        """SL 은 이익 방향으로만 움직인다 (물타기·SL 밀기 금지)."""
        if not cfg.move_sl_only_forward:
            return True
        return new_sl > self.sl if self.side == "buy" else new_sl < self.sl


@dataclass
class RiskState:
    """일일 리스크 게이트."""
    balance: float
    day: date | None = None
    trades_today: int = 0
    losses_today: int = 0
    consecutive_losses: int = 0
    pnl_today: float = 0.0
    halted_reason: str | None = None

    def roll_day(self, ts: datetime) -> None:
        d = ts.date()
        if self.day != d:
            self.day = d
            self.trades_today = 0
            self.losses_today = 0
            self.pnl_today = 0.0
            self.halted_reason = None

    def can_trade(self, ts: datetime, cfg: CrowConfig) -> tuple[bool, str]:
        self.roll_day(ts)
        if self.halted_reason:
            return False, self.halted_reason
        if self.trades_today >= cfg.max_trades_per_day:
            return False, f"일일 거래 한도 {cfg.max_trades_per_day}건 소진"
        if self.consecutive_losses >= cfg.max_consecutive_losses:
            return False, f"연속 손절 {self.consecutive_losses}회 → 당일 중단"
        if self.balance > 0 and -self.pnl_today >= self.balance * cfg.max_daily_loss_pct / 100.0:
            return False, f"일일 손실 한도 {cfg.max_daily_loss_pct}% 도달"
        return True, ""

    def register_open(self, ts: datetime) -> None:
        self.roll_day(ts)
        self.trades_today += 1

    def register_close(self, pnl: float, cfg: CrowConfig) -> None:
        self.balance += pnl
        self.pnl_today += pnl
        if pnl < 0:
            self.losses_today += 1
            self.consecutive_losses += 1
            if self.consecutive_losses >= cfg.max_consecutive_losses:
                self.halted_reason = f"연속 손절 {self.consecutive_losses}회 → 당일 중단"
        else:
            self.consecutive_losses = 0


def validate_rr(entry: float, sl: float, tp: float, side: Side, min_rr: float) -> tuple[bool, float]:
    risk = abs(entry - sl)
    reward = abs(tp - entry)
    if risk <= 0:
        return False, 0.0
    if side == "buy" and not (sl < entry < tp):
        return False, 0.0
    if side == "sell" and not (tp < entry < sl):
        return False, 0.0
    rr = reward / risk
    return rr >= min_rr, rr
