"""브로커 인터페이스와 공용 자료구조.

MT5 단말에 직접 의존하는 코드는 `terminal.Mt5Broker` 한 곳에만 두고,
나머지 로직은 전부 이 프로토콜에만 의존한다.
그래야 단말 없이도(리눅스·CI 포함) 전략 실행 경로를 테스트할 수 있다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal, Protocol, Sequence, runtime_checkable

from crowcode.data import Series

Side = Literal["buy", "sell"]


@dataclass(frozen=True)
class AccountInfo:
    login: int
    balance: float
    equity: float
    margin_free: float
    currency: str
    leverage: int


@dataclass(frozen=True)
class SymbolInfo:
    name: str
    digits: int
    point: float
    contract_size: float
    volume_min: float
    volume_max: float
    volume_step: float
    stops_level_points: int      # SL/TP 최소 이격 (포인트)
    freeze_level_points: int
    spread_points: int
    tick_value: float            # 1랏 / 1틱당 손익 (계좌 통화)
    tick_size: float

    @property
    def min_stop_distance(self) -> float:
        return self.stops_level_points * self.point

    def normalize_price(self, price: float) -> float:
        return round(price, self.digits)

    def normalize_volume(self, volume: float) -> float:
        if self.volume_step <= 0:
            return max(self.volume_min, min(volume, self.volume_max))
        steps = int((volume + 1e-9) / self.volume_step)
        v = round(steps * self.volume_step, 8)
        if v < self.volume_min:
            return 0.0
        return min(v, self.volume_max)

    def money_per_price_unit(self, volume: float) -> float:
        """가격 1단위 움직일 때의 손익 (계좌 통화).

        tick_value/tick_size 가 있으면 그것을 쓰고 (교차 통화까지 반영),
        없으면 계약 크기로 대체한다.
        """
        if self.tick_size > 0 and self.tick_value > 0:
            return volume * self.tick_value / self.tick_size
        return volume * self.contract_size


@dataclass(frozen=True)
class Tick:
    ts: datetime
    bid: float
    ask: float

    @property
    def spread(self) -> float:
        return self.ask - self.bid


@dataclass(frozen=True)
class PositionInfo:
    ticket: int
    symbol: str
    side: Side
    volume: float
    price_open: float
    sl: float
    tp: float
    profit: float
    magic: int
    opened_at: datetime
    comment: str = ""


@dataclass(frozen=True)
class OrderInfo:
    ticket: int
    symbol: str
    side: Side
    order_type: Literal["limit", "stop", "market"]
    volume: float
    price_open: float
    sl: float
    tp: float
    magic: int
    placed_at: datetime
    expires_at: datetime | None = None


@dataclass(frozen=True)
class DealInfo:
    ticket: int
    symbol: str
    profit: float
    closed_at: datetime
    magic: int
    entry: Literal["in", "out"] = "out"


@dataclass(frozen=True)
class OrderResult:
    ok: bool
    ticket: int | None = None
    retcode: int | None = None
    message: str = ""
    price: float | None = None

    def __bool__(self) -> bool:
        return self.ok


@runtime_checkable
class Broker(Protocol):
    """실행 계층이 필요로 하는 최소 기능."""

    def account(self) -> AccountInfo: ...

    def symbol(self, name: str) -> SymbolInfo: ...

    def list_symbols(self) -> list[str]: ...

    def rates(self, symbol: str, timeframe: str, count: int) -> Series: ...

    def tick(self, symbol: str) -> Tick: ...

    def positions(self, symbol: str, magic: int) -> list[PositionInfo]: ...

    def orders(self, symbol: str, magic: int) -> list[OrderInfo]: ...

    def send_market(self, symbol: str, side: Side, volume: float, sl: float, tp: float,
                    magic: int, comment: str, deviation: int) -> OrderResult: ...

    def send_pending(self, symbol: str, side: Side, volume: float, price: float,
                     sl: float, tp: float, magic: int, comment: str,
                     expires_at: datetime | None) -> OrderResult: ...

    def modify_sltp(self, ticket: int, sl: float, tp: float) -> OrderResult: ...

    def close_partial(self, ticket: int, volume: float, deviation: int) -> OrderResult: ...

    def close_position(self, ticket: int, deviation: int) -> OrderResult: ...

    def cancel_order(self, ticket: int) -> OrderResult: ...

    def deals_since(self, symbol: str, magic: int, since: datetime) -> list[DealInfo]: ...

    def now(self) -> datetime: ...
