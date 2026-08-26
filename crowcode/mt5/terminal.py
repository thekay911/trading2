"""실제 MetaTrader 5 단말 연결 (Windows 단말 + `MetaTrader5` 파이썬 패키지 필요).

    pip install MetaTrader5

주의할 점 두 가지 — 실전에서 사고가 나는 지점이다.

1. **서버 시간 ≠ UTC**
   MT5 가 주는 봉 시각은 브로커 서버 시간(대개 UTC+2/+3)이다. 세션 필터가
   UTC 기준이므로 변환하지 않으면 런던·뉴욕 세션이 2~3시간 밀린다.
   `server_utc_offset` 로 보정하며, 지정하지 않으면 틱 시각과 실제 UTC 를
   비교해 자동 추정한다.

2. **copy_rates 의 0번은 아직 안 끝난 봉**
   미완성 봉으로 판단하면 룩어헤드와 신호 깜빡임이 생긴다.
   여기서는 항상 마지막(형성 중) 봉을 잘라내고 **마감된 봉만** 반환한다.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from crowcode.data import Candle, Series
from crowcode.mt5.broker import (
    AccountInfo, DealInfo, OrderInfo, OrderResult, PositionInfo, Side, SymbolInfo, Tick,
)


def _mt5():
    try:
        import MetaTrader5 as mt5  # type: ignore
    except ImportError as exc:     # pragma: no cover - 환경 의존
        raise RuntimeError(
            "MetaTrader5 패키지가 없습니다. Windows 에서 `pip install MetaTrader5` 후 "
            "MT5 단말을 실행한 상태로 다시 시도하세요."
        ) from exc
    return mt5


_TF_NAMES = {
    "M1": "TIMEFRAME_M1", "M5": "TIMEFRAME_M5", "M15": "TIMEFRAME_M15",
    "M30": "TIMEFRAME_M30", "H1": "TIMEFRAME_H1", "H4": "TIMEFRAME_H4",
    "D1": "TIMEFRAME_D1",
}


class Mt5Broker:
    """`Broker` 프로토콜의 MT5 구현."""

    def __init__(
        self,
        login: int | None = None,
        password: str | None = None,
        server: str | None = None,
        terminal_path: str | None = None,
        server_utc_offset: float | None = None,
        portable_timeout: int = 60_000,
    ):
        self.mt5 = _mt5()
        kwargs: dict[str, Any] = {"timeout": portable_timeout}
        if terminal_path:
            kwargs["path"] = terminal_path
        if login:
            kwargs.update(login=int(login), password=password or "", server=server or "")
        if not self.mt5.initialize(**kwargs):
            raise RuntimeError(f"MT5 초기화 실패: {self.mt5.last_error()}")

        self.offset = timedelta(hours=server_utc_offset) if server_utc_offset is not None \
            else self.detect_server_offset()

    # ------------------------------------------------------------------
    def shutdown(self) -> None:
        self.mt5.shutdown()

    def __enter__(self) -> "Mt5Broker":
        return self

    def __exit__(self, *exc) -> None:
        self.shutdown()

    # --- 시간 --------------------------------------------------------
    def detect_server_offset(self) -> timedelta:
        """서버 시간과 UTC 의 차이를 15분 단위로 추정한다."""
        t = self.mt5.symbol_info_tick(self._any_symbol())
        if t is None:
            return timedelta(0)
        server = datetime.fromtimestamp(t.time, tz=timezone.utc)   # naive 서버시각을 UTC 로 읽음
        real = datetime.now(timezone.utc)
        quarters = round((server - real).total_seconds() / 900.0)
        return timedelta(minutes=15 * quarters)

    def _any_symbol(self) -> str:
        syms = self.mt5.symbols_get()
        return syms[0].name if syms else "EURUSD"

    def _to_utc(self, server_epoch: float) -> datetime:
        return datetime.fromtimestamp(server_epoch, tz=timezone.utc) - self.offset

    def _to_server(self, ts: datetime) -> int:
        return int((ts + self.offset).timestamp())

    def now(self) -> datetime:
        return datetime.now(timezone.utc)

    # --- 조회 --------------------------------------------------------
    def account(self) -> AccountInfo:
        a = self.mt5.account_info()
        if a is None:
            raise RuntimeError(f"계좌 정보 조회 실패: {self.mt5.last_error()}")
        return AccountInfo(a.login, a.balance, a.equity, a.margin_free, a.currency, a.leverage)

    def symbol(self, name: str) -> SymbolInfo:
        s = self.mt5.symbol_info(name)
        if s is None:
            raise RuntimeError(f"심볼 없음: {name}")
        if not s.visible and not self.mt5.symbol_select(name, True):
            raise RuntimeError(f"심볼을 마켓워치에 추가하지 못함: {name}")
        s = self.mt5.symbol_info(name)
        return SymbolInfo(
            name=s.name, digits=s.digits, point=s.point,
            contract_size=s.trade_contract_size,
            volume_min=s.volume_min, volume_max=s.volume_max, volume_step=s.volume_step,
            stops_level_points=int(getattr(s, "trade_stops_level", 0)),
            freeze_level_points=int(getattr(s, "trade_freeze_level", 0)),
            spread_points=int(getattr(s, "spread", 0)),
            tick_value=float(getattr(s, "trade_tick_value", 0.0)),
            tick_size=float(getattr(s, "trade_tick_size", 0.0)),
        )

    def rates(self, symbol: str, timeframe: str, count: int) -> Series:
        tf = getattr(self.mt5, _TF_NAMES[timeframe.upper()])
        # 0번은 형성 중인 봉 → 하나 더 받아서 잘라낸다.
        raw = self.mt5.copy_rates_from_pos(symbol, tf, 0, count + 1)
        if raw is None or len(raw) == 0:
            return Series([], symbol, timeframe.upper())
        rows = list(raw)[:-1]
        candles = [
            Candle(self._to_utc(r["time"]), float(r["open"]), float(r["high"]),
                   float(r["low"]), float(r["close"]), float(r["tick_volume"]))
            for r in rows
        ]
        return Series(candles, symbol, timeframe.upper())

    def tick(self, symbol: str) -> Tick:
        t = self.mt5.symbol_info_tick(symbol)
        if t is None:
            raise RuntimeError(f"틱 조회 실패: {symbol}")
        return Tick(self._to_utc(t.time), float(t.bid), float(t.ask))

    def positions(self, symbol: str, magic: int) -> list[PositionInfo]:
        out = self.mt5.positions_get(symbol=symbol) or []
        return [
            PositionInfo(
                ticket=p.ticket, symbol=p.symbol,
                side="buy" if p.type == self.mt5.POSITION_TYPE_BUY else "sell",
                volume=p.volume, price_open=p.price_open, sl=p.sl, tp=p.tp,
                profit=p.profit, magic=p.magic, opened_at=self._to_utc(p.time),
                comment=p.comment,
            )
            for p in out if p.magic == magic
        ]

    def orders(self, symbol: str, magic: int) -> list[OrderInfo]:
        out = self.mt5.orders_get(symbol=symbol) or []
        res = []
        for o in out:
            if o.magic != magic:
                continue
            buy = o.type in (self.mt5.ORDER_TYPE_BUY_LIMIT, self.mt5.ORDER_TYPE_BUY_STOP)
            kind = "limit" if o.type in (self.mt5.ORDER_TYPE_BUY_LIMIT,
                                         self.mt5.ORDER_TYPE_SELL_LIMIT) else "stop"
            res.append(OrderInfo(
                ticket=o.ticket, symbol=o.symbol, side="buy" if buy else "sell",
                order_type=kind, volume=o.volume_current, price_open=o.price_open,
                sl=o.sl, tp=o.tp, magic=o.magic, placed_at=self._to_utc(o.time_setup),
                expires_at=self._to_utc(o.time_expiration) if o.time_expiration else None,
            ))
        return res

    def deals_since(self, symbol: str, magic: int, since: datetime) -> list[DealInfo]:
        frm = datetime.fromtimestamp(self._to_server(since), tz=timezone.utc).replace(tzinfo=None)
        to = datetime.utcfromtimestamp(self._to_server(self.now()) + 3600)
        deals = self.mt5.history_deals_get(frm, to) or []
        out = []
        for d in deals:
            if d.symbol != symbol or d.magic != magic:
                continue
            out.append(DealInfo(
                ticket=d.position_id, symbol=d.symbol,
                profit=float(d.profit) + float(d.commission) + float(d.swap),
                closed_at=self._to_utc(d.time), magic=d.magic,
                entry="out" if d.entry == self.mt5.DEAL_ENTRY_OUT else "in",
            ))
        return out

    # --- 주문 --------------------------------------------------------
    def _filling(self, symbol: str) -> int:
        """브로커가 허용하는 체결 방식을 고른다 (틀리면 10030 으로 거부된다)."""
        s = self.mt5.symbol_info(symbol)
        mode = int(getattr(s, "filling_mode", 0))
        if mode & 1:      # SYMBOL_FILLING_FOK
            return self.mt5.ORDER_FILLING_FOK
        if mode & 2:      # SYMBOL_FILLING_IOC
            return self.mt5.ORDER_FILLING_IOC
        return self.mt5.ORDER_FILLING_RETURN

    def _supports_expiration(self, symbol: str) -> bool:
        s = self.mt5.symbol_info(symbol)
        return bool(int(getattr(s, "expiration_mode", 0)) & 4)   # SYMBOL_EXPIRATION_SPECIFIED

    def _result(self, r) -> OrderResult:
        if r is None:
            return OrderResult(False, message=f"order_send 실패: {self.mt5.last_error()}")
        ok = r.retcode in (self.mt5.TRADE_RETCODE_DONE, self.mt5.TRADE_RETCODE_PLACED)
        return OrderResult(ok, ticket=(r.order or r.deal or None), retcode=r.retcode,
                           message=r.comment or "", price=getattr(r, "price", None))

    def send_market(self, symbol, side, volume, sl, tp, magic, comment, deviation) -> OrderResult:
        t = self.mt5.symbol_info_tick(symbol)
        req = {
            "action": self.mt5.TRADE_ACTION_DEAL,
            "symbol": symbol, "volume": float(volume),
            "type": self.mt5.ORDER_TYPE_BUY if side == "buy" else self.mt5.ORDER_TYPE_SELL,
            "price": t.ask if side == "buy" else t.bid,
            "sl": float(sl), "tp": float(tp),
            "deviation": int(deviation), "magic": int(magic), "comment": comment[:31],
            "type_time": self.mt5.ORDER_TIME_GTC,
            "type_filling": self._filling(symbol),
        }
        return self._result(self.mt5.order_send(req))

    def send_pending(self, symbol, side, volume, price, sl, tp, magic, comment,
                     expires_at) -> OrderResult:
        req = {
            "action": self.mt5.TRADE_ACTION_PENDING,
            "symbol": symbol, "volume": float(volume),
            "type": self.mt5.ORDER_TYPE_BUY_LIMIT if side == "buy"
            else self.mt5.ORDER_TYPE_SELL_LIMIT,
            "price": float(price), "sl": float(sl), "tp": float(tp),
            "magic": int(magic), "comment": comment[:31],
            "type_filling": self._filling(symbol),
        }
        if expires_at is not None and self._supports_expiration(symbol):
            req["type_time"] = self.mt5.ORDER_TIME_SPECIFIED
            req["expiration"] = self._to_server(expires_at)
        else:
            # 만료를 못 걸면 러너가 시각을 보고 직접 취소한다.
            req["type_time"] = self.mt5.ORDER_TIME_GTC
        return self._result(self.mt5.order_send(req))

    def modify_sltp(self, ticket: int, sl: float, tp: float) -> OrderResult:
        pos = self.mt5.positions_get(ticket=ticket)
        if not pos:
            return OrderResult(False, message="포지션 없음")
        p = pos[0]
        req = {"action": self.mt5.TRADE_ACTION_SLTP, "symbol": p.symbol,
               "position": int(ticket), "sl": float(sl), "tp": float(tp)}
        return self._result(self.mt5.order_send(req))

    def close_partial(self, ticket: int, volume: float, deviation: int) -> OrderResult:
        return self._close(ticket, volume, deviation)

    def close_position(self, ticket: int, deviation: int) -> OrderResult:
        return self._close(ticket, None, deviation)

    def _close(self, ticket: int, volume: float | None, deviation: int) -> OrderResult:
        pos = self.mt5.positions_get(ticket=ticket)
        if not pos:
            return OrderResult(False, message="포지션 없음")
        p = pos[0]
        t = self.mt5.symbol_info_tick(p.symbol)
        is_buy = p.type == self.mt5.POSITION_TYPE_BUY
        req = {
            "action": self.mt5.TRADE_ACTION_DEAL, "symbol": p.symbol,
            "volume": float(volume if volume is not None else p.volume),
            "type": self.mt5.ORDER_TYPE_SELL if is_buy else self.mt5.ORDER_TYPE_BUY,
            "position": int(ticket), "price": t.bid if is_buy else t.ask,
            "deviation": int(deviation), "magic": int(p.magic), "comment": "crowcode close",
            "type_time": self.mt5.ORDER_TIME_GTC, "type_filling": self._filling(p.symbol),
        }
        return self._result(self.mt5.order_send(req))

    def cancel_order(self, ticket: int) -> OrderResult:
        req = {"action": self.mt5.TRADE_ACTION_REMOVE, "order": int(ticket)}
        return self._result(self.mt5.order_send(req))
