"""실전 실행 루프.

한 번의 `step()` 이 하는 일 (순서 중요):

  1. 열린 포지션 관리   2R → 본절 이동, 3R → 분할 청산  (신규 진입보다 먼저)
  2. 대기 주문 점검     만료 / 전제 붕괴(종가가 SL 밖) 시 취소
  3. 리스크 게이트      브로커 체결 내역으로 당일 손익·연속 손절 재구성
  4. 신규 평가          새 봉이 마감됐을 때만, 포지션·대기주문이 없을 때만
  5. 주문 전송          브로커 제약(최소 이격/랏 단위/스프레드) 재검증 후 전송

브로커 상태를 매번 다시 읽기 때문에 프로세스를 재시작해도 이어서 동작한다.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Sequence

from crowcode.config import CrowConfig, preset
from crowcode.data import Series, tf_minutes
from crowcode.mt5.broker import (
    AccountInfo, Broker, DealInfo, OrderResult, PositionInfo, Side, SymbolInfo,
)
from crowcode.mt5.journal import Journal
from crowcode.risk import RiskState, position_size
from crowcode.sessions import NewsEvent
from crowcode.signals import Signal
from crowcode.strategy import CrowStrategy


@dataclass
class LiveConfig:
    symbol: str = "XAUUSD"
    preset_name: str = "scalp"
    base_timeframe: str = "M1"
    bars: int = 4000                 # 평가에 쓸 베이스 봉 수
    magic: int = 700911              # 이 봇의 주문만 식별하는 번호
    deviation: int = 20              # 시장가 슬리피지 허용 (포인트)
    dry_run: bool = True             # True 면 주문을 만들되 전송하지 않는다
    comment: str = "crowcode"
    state_path: str = "state/crowcode_state.json"
    max_spread_points: int = 60      # 스프레드가 넓으면 진입하지 않는다
    be_buffer_points: int = 10       # 본절 이동 시 수수료·스프레드 여유
    poll_seconds: int = 5
    summary_minutes: int = 60        # 기각 사유 요약을 남기는 주기
    verbose_rejects: bool = False    # True 면 매 봉 기각 사유를 전부 남긴다


@dataclass
class _Managed:
    """포지션의 최초 리스크를 기억한다 (본절 이동 후에는 SL 로 역산할 수 없다)."""
    ticket: int
    entry: float
    initial_sl: float
    tp: float
    volume: float
    moved_to_be: bool = False
    partial_done: bool = False

    @property
    def risk(self) -> float:
        return abs(self.entry - self.initial_sl)


class LiveRunner:
    def __init__(
        self,
        broker: Broker,
        live: LiveConfig,
        cfg: CrowConfig | None = None,
        journal: Journal | None = None,
        news: Sequence[NewsEvent] = (),
    ):
        self.broker = broker
        self.live = live
        self.base_cfg = cfg or preset(live.preset_name)
        self.journal = journal or Journal(echo=True)
        self.news = list(news)

        self.cfg = self.base_cfg          # 심볼 사양이 반영된 설정 (첫 step 에서 갱신)
        self.strategy = CrowStrategy(self.cfg, live.symbol, self.news)
        self._last_bar_ts: datetime | None = None
        self._pending_placed: dict[int, str] = {}   # ticket -> 배치 시각 ISO
        self.managed: dict[int, _Managed] = {}
        self._reject_counts: dict[str, int] = {}
        self._last_reject: str | None = None
        self._last_summary: datetime | None = None
        self._load_state()

    # ==================================================================
    # 메인 루프
    # ==================================================================
    def run(self, max_iterations: int | None = None) -> None:
        i = 0
        while max_iterations is None or i < max_iterations:
            try:
                self.step()
            except Exception as exc:                      # 루프는 죽지 않아야 한다
                self.journal.write("error", detail=repr(exc))
            i += 1
            if max_iterations is None or i < max_iterations:
                time.sleep(self.live.poll_seconds)

    def step(self) -> Signal | None:
        live = self.live
        now = self.broker.now()
        info = self.broker.symbol(live.symbol)
        acct = self.broker.account()
        self.cfg = config_for_symbol(self.base_cfg, info, acct)
        self.strategy.cfg = self.cfg

        positions = self.broker.positions(live.symbol, live.magic)
        pendings = self.broker.orders(live.symbol, live.magic)
        self._manage_positions(positions, info, pendings)
        self._review_pendings(pendings, now)

        if positions or pendings:
            return None                                    # 한 번에 한 셋업만

        rates = self.broker.rates(live.symbol, live.base_timeframe, live.bars)
        if len(rates) < 60:
            return None
        last_bar = rates[-1]
        if self._last_bar_ts is not None and last_bar.ts <= self._last_bar_ts:
            return None                                    # 새 봉이 마감됐을 때만 평가
        self._last_bar_ts = last_bar.ts

        self._maybe_summary(now)

        risk = self._risk_state(acct, now)
        ok, why = risk.can_trade(now, self.cfg)
        if not ok:
            self._note_reject("risk_gate", why, last_bar.ts)
            return None

        tick = self.broker.tick(live.symbol)
        spread_pts = round(tick.spread / info.point) if info.point else 0
        if spread_pts > live.max_spread_points:
            self._note_reject("spread", f"스프레드 {spread_pts}pt 과다", last_bar.ts)
            return None

        sig = self.strategy.evaluate(rates, acct.balance, risk)
        if sig is None:
            r = self.strategy.rejections[-1] if self.strategy.rejections else None
            if r is not None:
                self._note_reject(r.rule, r.detail, last_bar.ts)
            return None

        self._place(sig, info, acct, tick)
        return sig

    def _note_reject(self, rule: str, detail: str, bar_ts: datetime) -> None:
        """기각 사유는 집계해 두고, 사유가 바뀔 때만 한 줄 남긴다.

        1분봉이면 하루 1400줄이 쌓이므로 그대로 찍으면 로그가 쓸모없어진다.
        """
        self._reject_counts[rule] = self._reject_counts.get(rule, 0) + 1
        if self.live.verbose_rejects or rule != self._last_reject:
            self.journal.write("reject", rule=rule, detail=detail, bar=bar_ts)
        self._last_reject = rule

    def _maybe_summary(self, now: datetime) -> None:
        if self._last_summary is None:
            self._last_summary = now
            return
        if now - self._last_summary < timedelta(minutes=self.live.summary_minutes):
            return
        self._last_summary = now
        if self._reject_counts:
            self.journal.write("summary", rejections=dict(
                sorted(self._reject_counts.items(), key=lambda kv: -kv[1])))
            self._reject_counts.clear()

    # ==================================================================
    # 1) 포지션 관리
    # ==================================================================
    def _manage_positions(self, positions: Sequence[PositionInfo], info: SymbolInfo,
                          pendings: Sequence = ()) -> None:
        # 대기 주문의 티켓은 체결되면 포지션 티켓이 되므로 살아 있는 것으로 본다.
        # (이걸 빼면 체결 직전에 기록이 지워져 최초 리스크를 잃는다)
        alive = {p.ticket for p in positions} | {o.ticket for o in pendings}
        for ticket in list(self.managed):
            if ticket not in alive:
                del self.managed[ticket]
                self._save_state()

        tick = self.broker.tick(self.live.symbol)
        for p in positions:
            m = self.managed.get(p.ticket)
            if m is None:
                # 재시작 등으로 최초 리스크를 모르는 포지션 — 현재 SL 로 대체한다.
                m = _Managed(p.ticket, p.price_open, p.sl or p.price_open, p.tp, p.volume)
                self.managed[p.ticket] = m
                self.journal.write("adopt", ticket=p.ticket,
                                   detail="최초 리스크 미상 → 현재 SL 기준으로 관리")
                self._save_state()
            if m.risk <= 0:
                continue

            price = tick.bid if p.side == "buy" else tick.ask
            r = ((price - m.entry) if p.side == "buy" else (m.entry - price)) / m.risk

            if not m.moved_to_be and r >= self.cfg.breakeven_at_r:
                self._move_to_breakeven(p, m, info, price)
            if not m.partial_done and r >= self.cfg.partial_at_r and self.cfg.partial_fraction > 0:
                self._take_partial(p, m, info)

    def _move_to_breakeven(self, p: PositionInfo, m: _Managed, info: SymbolInfo, price: float) -> None:
        buf = self.live.be_buffer_points * info.point
        new_sl = m.entry + buf if p.side == "buy" else m.entry - buf
        # SL 은 이익 방향으로만 (채널 규칙: 절대 뒤로 밀지 않는다)
        if p.sl and ((p.side == "buy" and new_sl <= p.sl) or (p.side == "sell" and new_sl >= p.sl)):
            m.moved_to_be = True
            self._save_state()
            return
        if abs(price - new_sl) < info.min_stop_distance:
            return                                          # 최소 이격 미달 — 다음 틱에 재시도
        res = self._exec("modify_sltp", lambda: self.broker.modify_sltp(
            p.ticket, info.normalize_price(new_sl), p.tp), ticket=p.ticket, sl=new_sl)
        if res.ok:
            m.moved_to_be = True
            self._save_state()
            self.journal.write("breakeven", ticket=p.ticket, sl=round(new_sl, info.digits),
                               r=self.cfg.breakeven_at_r)

    def _take_partial(self, p: PositionInfo, m: _Managed, info: SymbolInfo) -> None:
        vol = info.normalize_volume(p.volume * self.cfg.partial_fraction)
        rest = round(p.volume - vol, 8)
        if vol <= 0 or rest < info.volume_min:
            m.partial_done = True                           # 나눌 수 없는 최소 랏 — 통째로 끌고 간다
            self._save_state()
            return
        res = self._exec("close_partial", lambda: self.broker.close_partial(
            p.ticket, vol, self.live.deviation), ticket=p.ticket, volume=vol)
        if res.ok:
            m.partial_done = True
            self._save_state()
            self.journal.write("partial", ticket=p.ticket, volume=vol, r=self.cfg.partial_at_r)

    # ==================================================================
    # 2) 대기 주문 점검
    # ==================================================================
    def _review_pendings(self, pendings, now: datetime) -> None:
        expiry = timedelta(minutes=self.cfg.limit_expiry_bars * tf_minutes(self.cfg.ltf))
        rates = None
        for o in pendings:
            placed = self._pending_placed.get(o.ticket)
            placed_at = datetime.fromisoformat(placed) if placed else o.placed_at
            if now - placed_at >= expiry:
                self._exec("cancel", lambda: self.broker.cancel_order(o.ticket),
                           ticket=o.ticket, reason="만료")
                self._pending_placed.pop(o.ticket, None)
                self._save_state()
                continue

            # 전제 붕괴: 마감 종가가 이미 SL 밖이면 그 셋업은 무효다.
            if rates is None:
                rates = self.broker.rates(self.live.symbol, self.live.base_timeframe, 5)
            if len(rates) and o.sl:
                c = rates[-1].close
                broken = (o.side == "buy" and c < o.sl) or (o.side == "sell" and c > o.sl)
                if broken:
                    self._exec("cancel", lambda: self.broker.cancel_order(o.ticket),
                               ticket=o.ticket, reason="구조 붕괴")
                    self._pending_placed.pop(o.ticket, None)
                    self._save_state()

    # ==================================================================
    # 3) 리스크 게이트
    # ==================================================================
    def _risk_state(self, acct: AccountInfo, now: datetime) -> RiskState:
        midnight = datetime(now.year, now.month, now.day, tzinfo=now.tzinfo)
        deals = self.broker.deals_since(self.live.symbol, self.live.magic, midnight)

        # 분할 청산이 있으면 한 포지션에서 체결이 여러 건 나온다 → 포지션 단위로 합산.
        per_position: dict[int, list[DealInfo]] = {}
        for d in deals:
            if d.entry == "out":
                per_position.setdefault(d.ticket, []).append(d)

        rows = [(max(x.closed_at for x in ds), sum(x.profit for x in ds))
                for ds in per_position.values()]
        rows.sort(key=lambda t: t[0])

        st = RiskState(balance=acct.balance)
        st.roll_day(now)
        for closed_at, pnl in rows:
            st.register_open(closed_at)
            st.register_close(pnl, self.cfg)
        st.balance = acct.balance          # 잔고는 브로커 값을 신뢰
        return st

    # ==================================================================
    # 4) 주문 전송
    # ==================================================================
    def _place(self, sig: Signal, info: SymbolInfo, acct: AccountInfo, tick) -> None:
        live = self.live
        entry = info.normalize_price(sig.entry)
        sl = info.normalize_price(sig.sl)
        tp = info.normalize_price(sig.tp)

        # 브로커 최소 이격 재검증 (전략은 브로커 제약을 모른다)
        ref = tick.ask if sig.side == "buy" else tick.bid
        gap = info.min_stop_distance
        if gap > 0 and (abs(entry - sl) < gap or abs(entry - tp) < gap):
            self.journal.write("reject", rule="stops_level",
                               detail=f"SL/TP 가 최소 이격 {gap} 미만")
            return

        lots, risk_amt = position_size(acct.balance, self.cfg.risk_pct, entry, sl, self.cfg)
        lots = info.normalize_volume(lots)
        if lots <= 0:
            self.journal.write("reject", rule="sizing", detail="랏이 최소 단위 미만")
            return

        payload = dict(symbol=live.symbol, side=sig.side, volume=lots, entry=entry,
                       sl=sl, tp=tp, rr=round(sig.rr, 2), risk=round(risk_amt, 2),
                       reasons=list(sig.reasons))

        if sig.order_type == "market":
            res = self._exec("order", lambda: self.broker.send_market(
                live.symbol, sig.side, lots, sl, tp, live.magic, live.comment, live.deviation),
                type="market", **payload)
            if res.ok and res.ticket:
                self.managed[res.ticket] = _Managed(
                    res.ticket, res.price or entry, sl, tp, lots)
                self._save_state()
            return

        # 지정가: 현재가를 이미 지나쳤으면 의미가 없다.
        if (sig.side == "buy" and entry >= ref) or (sig.side == "sell" and entry <= ref):
            self.journal.write("reject", rule="limit_side",
                               detail="지정가가 현재가 반대편 — 스킵")
            return

        expires = self.broker.now() + timedelta(
            minutes=self.cfg.limit_expiry_bars * tf_minutes(self.cfg.ltf))
        res = self._exec("order", lambda: self.broker.send_pending(
            live.symbol, sig.side, lots, entry, sl, tp, live.magic, live.comment, expires),
            type="limit", expires=expires, **payload)
        if res.ok and res.ticket:
            self._pending_placed[res.ticket] = self.broker.now().isoformat()
            self.managed[res.ticket] = _Managed(res.ticket, entry, sl, tp, lots)
            self._save_state()

    # ==================================================================
    # 공용
    # ==================================================================
    def _exec(self, kind: str, action, **fields) -> OrderResult:
        """dry-run 이면 전송하지 않고 기록만 한다."""
        if self.live.dry_run:
            self.journal.write(f"dry:{kind}", **fields)
            return OrderResult(False, message="dry-run", ticket=None)
        res = action()
        payload = dict(fields)
        payload.update(ok=res.ok, retcode=res.retcode, message=res.message)
        if res.ticket is not None:
            payload["result_ticket"] = res.ticket
        self.journal.write(kind if res.ok else f"{kind}:fail", **payload)
        return res

    # --- 상태 영속화 --------------------------------------------------
    def _load_state(self) -> None:
        path = self.live.state_path
        if not path or not os.path.exists(path):
            return
        try:
            with open(path, encoding="utf-8") as fh:
                raw = json.load(fh)
        except (OSError, ValueError):
            return
        self._pending_placed = {int(k): v for k, v in raw.get("pending_placed", {}).items()}
        for d in raw.get("managed", []):
            self.managed[int(d["ticket"])] = _Managed(**d)
        ts = raw.get("last_bar_ts")
        if ts:
            self._last_bar_ts = datetime.fromisoformat(ts)

    def _save_state(self) -> None:
        path = self.live.state_path
        if not path:
            return
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        payload = {
            "managed": [m.__dict__ for m in self.managed.values()],
            "pending_placed": {str(k): v for k, v in self._pending_placed.items()},
            "last_bar_ts": self._last_bar_ts.isoformat() if self._last_bar_ts else None,
        }
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, path)


def config_for_symbol(cfg: CrowConfig, info: SymbolInfo, acct: AccountInfo) -> CrowConfig:
    """전략 설정에 브로커의 실제 심볼 사양을 반영한다.

    하드코딩된 계약 크기 대신 tick_value/tick_size 로 계산한 '가격 1단위당 손익'을
    쓴다. 이렇게 해야 금·지수·FX 어디에 붙여도 랏 계산이 맞는다.
    """
    return cfg.with_(
        contract_size=info.money_per_price_unit(1.0),
        min_lot=info.volume_min,
        lot_step=info.volume_step,
        max_lot=info.volume_max,
        max_leverage=min(cfg.max_leverage, acct.leverage or cfg.max_leverage),
    )
