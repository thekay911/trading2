"""시그널 자료구조."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

Side = Literal["buy", "sell"]


@dataclass(frozen=True)
class Signal:
    ts: datetime
    symbol: str
    side: Side
    entry: float
    sl: float
    tp: float
    lots: float
    risk_amount: float
    rr: float
    order_type: Literal["limit", "market"] = "limit"
    expiry_bars: int = 24
    timeframe: str = ""
    reasons: tuple[str, ...] = ()
    score: float = 0.0

    @property
    def risk_per_unit(self) -> float:
        return abs(self.entry - self.sl)

    def r_price(self, r: float) -> float:
        """R 배수에 해당하는 가격."""
        d = self.risk_per_unit * r
        return self.entry + d if self.side == "buy" else self.entry - d

    def to_dict(self) -> dict:
        return {
            "ts": self.ts.isoformat(),
            "symbol": self.symbol,
            "side": self.side,
            "order_type": self.order_type,
            "entry": round(self.entry, 5),
            "sl": round(self.sl, 5),
            "tp": round(self.tp, 5),
            "rr": round(self.rr, 2),
            "lots": self.lots,
            "risk": round(self.risk_amount, 2),
            "timeframe": self.timeframe,
            "score": round(self.score, 2),
            "reasons": list(self.reasons),
        }

    def pretty(self) -> str:
        arrow = "▲ BUY " if self.side == "buy" else "▼ SELL"
        lines = [
            f"{arrow} {self.symbol} [{self.timeframe}] {self.order_type.upper()}",
            f"  진입 {self.entry:.3f} / SL {self.sl:.3f} / TP {self.tp:.3f}  (RR 1:{self.rr:.1f})",
            f"  랏 {self.lots}  리스크 {self.risk_amount:.2f}  점수 {self.score:.2f}",
            f"  본절 이동가 {self.r_price(2.0):.3f} (2R)",
        ]
        lines += [f"  · {r}" for r in self.reasons]
        return "\n".join(lines)


@dataclass
class Rejection:
    """시그널이 걸러진 이유 — 규칙이 실제로 작동했는지 감사(audit)용."""
    ts: datetime
    rule: str
    detail: str = ""
