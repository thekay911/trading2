"""단말 없이 도는 시뮬레이션 브로커.

주 용도는 두 가지다.
  1) `--dry-run` — 실주문 없이 실전 루프를 그대로 돌려 보기
  2) 테스트 — MT5 가 없는 환경(리눅스/CI)에서 실행 경로 전체를 검증

체결 규칙은 백테스터와 동일하다: 한 봉 안에서 SL·TP 가 모두 닿으면 SL 우선.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from crowcode.data import Series, tf_minutes
from crowcode.mt5.broker import (
    AccountInfo, DealInfo, OrderInfo, OrderResult, PositionInfo, Side, SymbolInfo, Tick,
)

XAUUSD = SymbolInfo(
    name="XAUUSD", digits=2, point=0.01, contract_size=100.0,
    volume_min=0.01, volume_max=50.0, volume_step=0.01,
    stops_level_points=50, freeze_level_points=0, spread_points=20,
    tick_value=1.0, tick_size=0.01,
)


@dataclass
class _Pending:
    info: OrderInfo


class PaperBroker:
    """가격 시계열을 한 봉씩 전진시키며 주문을 체결시키는 가짜 브로커."""

    def __init__(
        self,
        series: Series,
        info: SymbolInfo = XAUUSD,
        balance: float = 5000.0,
        start_index: int = 0,
        spread: float | None = None,
    ):
        self.series = series
        self.info = info
        self.balance = balance
        self.start_balance = balance
        self.cursor = start_index
        self.spread = info.spread_points * info.point if spread is None else spread

        self._ids = itertools.count(1000)
        self._pending: dict[int, OrderInfo] = {}
        self._positions: dict[int, PositionInfo] = {}
        self._deals: list[DealInfo] = []
        self.rejections: list[str] = []

    # ------------------------------------------------------------------
    # 시간 전진
    # ------------------------------------------------------------------
    @property
    def bar(self):
        return self.series[self.cursor]

    def advance(self) -> bool:
        """다음 봉으로 이동하며 만료·체결·청산을 처리한다."""
        if self.cursor + 1 >= len(self.series):
            return False
        self.cursor += 1
        c = self.bar
        self._expire(c.ts)
        self._fill_pending(c)
        self._resolve_positions(c)
        return True

    def _expire(self, ts: datetime) -> None:
        for ticket, o in list(self._pending.items()):
            if o.expires_at is not None and ts >= o.expires_at:
                del self._pending[ticket]

    def _fill_pending(self, c) -> None:
        for ticket, o in list(self._pending.items()):
            hit = (o.side == "buy" and c.low <= o.price_open) or \
                  (o.side == "sell" and c.high >= o.price_open)
            if not hit:
                continue
            price = o.price_open + (self.spread if o.side == "buy" else -self.spread)
            self._positions[ticket] = PositionInfo(
                ticket=ticket, symbol=o.symbol, side=o.side, volume=o.volume,
                price_open=self.info.normalize_price(price), sl=o.sl, tp=o.tp,
                profit=0.0, magic=o.magic, opened_at=c.ts, comment="paper",
            )
            del self._pending[ticket]

    def _resolve_positions(self, c) -> None:
        for ticket, p in list(self._positions.items()):
            hit_sl = c.low <= p.sl if p.side == "buy" else c.high >= p.sl
            hit_tp = c.high >= p.tp if p.side == "buy" else c.low <= p.tp
            if p.sl and hit_sl:
                self._settle(ticket, p.sl, c.ts)
            elif p.tp and hit_tp:
                self._settle(ticket, p.tp, c.ts)
            else:
                d = c.close - p.price_open if p.side == "buy" else p.price_open - c.close
                self._positions[ticket] = PositionInfo(
                    **{**p.__dict__, "profit": d * self.info.money_per_price_unit(p.volume)}
                )

    def _settle(self, ticket: int, price: float, ts: datetime, volume: float | None = None) -> float:
        p = self._positions[ticket]
        vol = p.volume if volume is None else min(volume, p.volume)
        d = price - p.price_open if p.side == "buy" else p.price_open - price
        profit = d * self.info.money_per_price_unit(vol)
        self.balance += profit
        self._deals.append(DealInfo(ticket, p.symbol, profit, ts, p.magic, "out"))
        if vol >= p.volume - 1e-9:
            del self._positions[ticket]
        else:
            self._positions[ticket] = PositionInfo(**{**p.__dict__, "volume": round(p.volume - vol, 8)})
        return profit

    # ------------------------------------------------------------------
    # Broker 프로토콜
    # ------------------------------------------------------------------
    def now(self) -> datetime:
        return self.bar.ts

    def account(self) -> AccountInfo:
        equity = self.balance + sum(p.profit for p in self._positions.values())
        return AccountInfo(1, round(self.balance, 2), round(equity, 2), equity, "USD", 100)

    def symbol(self, name: str) -> SymbolInfo:
        return self.info

    def rates(self, symbol: str, timeframe: str, count: int) -> Series:
        from crowcode.data import resample

        base = self.series[: self.cursor + 1]
        base_min = tf_minutes(self.series.timeframe) if self.series.timeframe else 1
        out = base if tf_minutes(timeframe) == base_min else resample(base, timeframe)
        return out.window(count)

    def tick(self, symbol: str) -> Tick:
        c = self.bar
        return Tick(c.ts, self.info.normalize_price(c.close),
                    self.info.normalize_price(c.close + self.spread))

    def positions(self, symbol: str, magic: int) -> list[PositionInfo]:
        return [p for p in self._positions.values() if p.symbol == symbol and p.magic == magic]

    def orders(self, symbol: str, magic: int) -> list[OrderInfo]:
        return [o for o in self._pending.values() if o.symbol == symbol and o.magic == magic]

    def send_market(self, symbol, side, volume, sl, tp, magic, comment, deviation) -> OrderResult:
        vol = self.info.normalize_volume(volume)
        if vol <= 0:
            return OrderResult(False, message="랏이 최소 단위 미만")
        t = self.tick(symbol)
        price = t.ask if side == "buy" else t.bid
        bad = self._check_stops(side, price, sl, tp)
        if bad:
            return OrderResult(False, message=bad)
        ticket = next(self._ids)
        self._positions[ticket] = PositionInfo(
            ticket, symbol, side, vol, self.info.normalize_price(price),
            self.info.normalize_price(sl), self.info.normalize_price(tp),
            0.0, magic, self.now(), comment,
        )
        self._deals.append(DealInfo(ticket, symbol, 0.0, self.now(), magic, "in"))
        return OrderResult(True, ticket=ticket, price=price)

    def send_pending(self, symbol, side, volume, price, sl, tp, magic, comment, expires_at) -> OrderResult:
        vol = self.info.normalize_volume(volume)
        if vol <= 0:
            return OrderResult(False, message="랏이 최소 단위 미만")
        t = self.tick(symbol)
        ref = t.ask if side == "buy" else t.bid
        if side == "buy" and price >= ref:
            return OrderResult(False, message="매수 지정가가 현재가 이상")
        if side == "sell" and price <= ref:
            return OrderResult(False, message="매도 지정가가 현재가 이하")
        bad = self._check_stops(side, price, sl, tp)
        if bad:
            return OrderResult(False, message=bad)
        ticket = next(self._ids)
        self._pending[ticket] = OrderInfo(
            ticket, symbol, side, "limit", vol, self.info.normalize_price(price),
            self.info.normalize_price(sl), self.info.normalize_price(tp),
            magic, self.now(), expires_at,
        )
        return OrderResult(True, ticket=ticket, price=price)

    def modify_sltp(self, ticket: int, sl: float, tp: float) -> OrderResult:
        p = self._positions.get(ticket)
        if p is None:
            return OrderResult(False, message="포지션 없음")
        bad = self._check_stops(p.side, self.tick(p.symbol).bid, sl, tp)
        if bad:
            return OrderResult(False, message=bad)
        self._positions[ticket] = PositionInfo(
            **{**p.__dict__, "sl": self.info.normalize_price(sl), "tp": self.info.normalize_price(tp)}
        )
        return OrderResult(True, ticket=ticket)

    def close_partial(self, ticket: int, volume: float, deviation: int) -> OrderResult:
        p = self._positions.get(ticket)
        if p is None:
            return OrderResult(False, message="포지션 없음")
        vol = self.info.normalize_volume(volume)
        if vol <= 0 or vol >= p.volume:
            return OrderResult(False, message="분할 청산 수량이 부적절")
        t = self.tick(p.symbol)
        self._settle(ticket, t.bid if p.side == "buy" else t.ask, self.now(), vol)
        return OrderResult(True, ticket=ticket)

    def close_position(self, ticket: int, deviation: int) -> OrderResult:
        p = self._positions.get(ticket)
        if p is None:
            return OrderResult(False, message="포지션 없음")
        t = self.tick(p.symbol)
        self._settle(ticket, t.bid if p.side == "buy" else t.ask, self.now())
        return OrderResult(True, ticket=ticket)

    def cancel_order(self, ticket: int) -> OrderResult:
        if ticket in self._pending:
            del self._pending[ticket]
            return OrderResult(True, ticket=ticket)
        return OrderResult(False, message="대기 주문 없음")

    def deals_since(self, symbol: str, magic: int, since: datetime) -> list[DealInfo]:
        return [d for d in self._deals
                if d.symbol == symbol and d.magic == magic
                and d.entry == "out" and d.closed_at >= since]

    # ------------------------------------------------------------------
    def _check_stops(self, side: Side, price: float, sl: float, tp: float) -> str:
        """브로커의 최소 이격(stops level) 검사 — 실제 MT5 에서 가장 흔한 주문 거부 사유."""
        d = self.info.min_stop_distance
        if sl and abs(price - sl) < d:
            return f"SL 이 최소 이격({d}) 안에 있음"
        if tp and abs(price - tp) < d:
            return f"TP 가 최소 이격({d}) 안에 있음"
        if side == "buy" and sl and sl >= price:
            return "매수 SL 이 진입가 이상"
        if side == "sell" and sl and sl <= price:
            return "매도 SL 이 진입가 이하"
        return ""
