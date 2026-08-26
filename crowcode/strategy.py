"""CrowStrategy — 채널의 매매 방식을 하나의 탑다운 규칙 엔진으로 결합.

파이프라인
----------
  0) 게이트      : 세션 / 뉴스 / 금요일 마감 / 일일 리스크 한도
  1) HTF 방향    : 시장구조 + Wyckoff 국면  → "Buy only" 또는 "Sell only"
  2) MTF POI     : 방향에 맞는 오더블록 · FVG (미소진 구역)
  3) LTF 트리거  : 반대편 유동성 스윕 → CHOCH 로 구조 전환 확인
  4) 주문        : POI 에 지정가, 스윕 극점 바깥에 SL, 유동성/고정 R 에 TP
  5) 리스크      : 계좌 % 기반 사이징 + 레버리지 상한, 최소 RR 미달이면 폐기

한 단계라도 실패하면 시그널은 나오지 않고 사유가 `rejections` 에 남는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Sequence

from crowcode import liquidity as liq
from crowcode import waves, wyckoff
from crowcode.config import CrowConfig, DEFAULT
from crowcode.data import Candle, MTFView, Series, atr, resample, tf_minutes
from crowcode.risk import RiskState, position_size, validate_rr
from crowcode.sessions import NewsEvent, friday_close_block, in_session, news_blackout
from crowcode.signals import Rejection, Side, Signal
from crowcode.structure import analyze_structure, swing_points


@dataclass
class Context:
    """한 번의 평가에서 쓰인 모든 중간 판단 (설명 가능성 확보용)."""
    ts: datetime
    htf_bias: str | None = None
    htf_wyckoff: wyckoff.WyckoffView | None = None
    mtf_bias: str | None = None
    wave: waves.WaveCount | None = None
    sweep: liq.Sweep | None = None
    choch_index: int | None = None
    poi: liq.POI | None = None
    reasons: list[str] = field(default_factory=list)


class CrowStrategy:
    def __init__(
        self,
        cfg: CrowConfig = DEFAULT,
        symbol: str = "XAUUSD",
        news: Sequence[NewsEvent] = (),
    ):
        self.cfg = cfg
        self.symbol = symbol
        self.news = list(news)
        self.rejections: list[Rejection] = []
        self.last_context: Context | None = None
        # 구조 계산에 쓰는 최근 봉 수 (오래된 구조는 의미가 없고, 계산량도 아낀다)
        self.htf_bars = 200
        self.mtf_bars = 300
        self.ltf_bars = 400

    # ------------------------------------------------------------------
    def view(self, base: Series) -> MTFView:
        """베이스 시계열로 다중 타임프레임 뷰를 만든다 (백테스트에서 1회 생성)."""
        return MTFView(base, (self.cfg.htf, self.cfg.mtf, self.cfg.ltf))

    def evaluate(
        self,
        market: Series | MTFView,
        balance: float,
        risk: RiskState | None = None,
        now_ts: datetime | None = None,
    ) -> Signal | None:
        cfg = self.cfg
        view = market if isinstance(market, MTFView) else self.view(market)
        base = view.base
        if len(base) < 60:
            return self._reject(now_ts or datetime.now(), "data", "데이터 부족")

        now = view.last_base(now_ts) if now_ts is not None else base[-1]
        if now is None:
            return self._reject(now_ts or datetime.now(), "data", "기준 시각 이전 데이터 없음")
        ctx = Context(ts=now.ts)
        self.last_context = ctx

        # --- 0) 게이트 -------------------------------------------------
        sess = in_session(now.ts, cfg.sessions)
        if sess is None:
            return self._reject(now.ts, "session", "거래 세션 밖 (유럽·미국 세션만)")
        ctx.reasons.append(f"세션: {sess.name}")

        if not cfg.trade_on_friday_close and friday_close_block(now.ts):
            return self._reject(now.ts, "friday", "금요일 마감 직전 신규 진입 금지")

        ev = news_blackout(now.ts, self.news, cfg.news_blackout_before_min, cfg.news_blackout_after_min)
        if ev is not None:
            return self._reject(now.ts, "news", f"고위험 지표 전후 ({ev.name})")

        if risk is not None:
            ok, why = risk.can_trade(now.ts, cfg)
            if not ok:
                return self._reject(now.ts, "risk_gate", why)

        # --- 타임프레임 구성 ------------------------------------------
        htf_c = view.slice(cfg.htf, now.ts, self.htf_bars)
        mtf_c = view.slice(cfg.mtf, now.ts, self.mtf_bars)
        ltf_c = view.slice(cfg.ltf, now.ts, self.ltf_bars)
        if len(htf_c) < 20 or len(mtf_c) < 30 or len(ltf_c) < 40:
            return self._reject(now.ts, "data", "상위/하위 타임프레임 캔들 부족")

        # --- 1) HTF 방향 ----------------------------------------------
        st_h = analyze_structure(htf_c, cfg.swing_left, cfg.swing_right, cfg.structure_break_on_close)
        wy = wyckoff.analyze(htf_c, cfg.wyckoff_lookback, cfg.wyckoff_min_touches,
                             cfg.range_max_width_atr, cfg.spring_max_penetration_atr)
        ctx.htf_bias, ctx.htf_wyckoff = st_h.bias, wy

        side = self._decide_side(st_h.bias, wy.bias)
        if side is None:
            return self._reject(now.ts, "htf_bias", "HTF 구조와 Wyckoff 국면이 불일치 또는 미정")
        ctx.reasons.append(f"HTF({cfg.htf}) 구조={st_h.bias or '중립'}, Wyckoff={wy.schematic}/Phase {wy.phase}")

        # --- 2) MTF 확인 + POI ----------------------------------------
        st_m = analyze_structure(mtf_c, cfg.swing_left, cfg.swing_right, cfg.structure_break_on_close)
        ctx.mtf_bias = st_m.bias
        if st_m.bias is not None and st_m.bias != ("bullish" if side == "buy" else "bearish"):
            # 상위 방향과 반대면, 채널식으로는 '되돌림 파동' → 조정 완료를 요구
            wc = waves.count(swing_points(mtf_c, cfg.swing_left, cfg.swing_right))
            ctx.wave = wc
            if not waves.correction_complete(wc):
                return self._reject(now.ts, "mtf_conflict", "MTF 가 아직 반대 방향 조정 진행 중")
            ctx.reasons.append("MTF ABC 조정 완료 → 본 방향 복귀")

        pois = liq.collect_pois(mtf_c, st_m.events, side, cfg.poi_types)
        if not pois:
            return self._reject(now.ts, "poi", "방향에 맞는 미소진 POI(오더블록/FVG) 없음")

        # --- 3) LTF 트리거 --------------------------------------------
        sw = swing_points(ltf_c, cfg.swing_left, cfg.swing_right)
        st_l = analyze_structure(ltf_c, cfg.swing_left, cfg.swing_right, cfg.structure_break_on_close)
        i_now = len(ltf_c) - 1

        sweep = None
        if cfg.require_liquidity_sweep:
            sweeps = liq.find_sweeps(ltf_c, sw, cfg.sweep_lookback,
                                     start=max(1, i_now - cfg.sweep_lookback))
            sweep = liq.last_sweep(sweeps, side, i_now, cfg.sweep_lookback)
            if sweep is None:
                return self._reject(now.ts, "sweep", "반대편 유동성 스윕 미확인")
            ctx.sweep = sweep
            ctx.reasons.append(f"유동성 스윕: {sweep.level:.3f} ({sweep.direction})")

        if cfg.require_choch:
            want = "bullish" if side == "buy" else "bearish"
            ch = st_l.last_choch()
            if ch is None or ch.direction != want:
                return self._reject(now.ts, "choch", "LTF CHOCH 미발생 또는 방향 불일치")
            if sweep is not None and ch.index < sweep.index:
                return self._reject(now.ts, "choch", "CHOCH 가 스윕보다 먼저 발생 (순서 불일치)")
            if i_now - ch.index > cfg.sweep_lookback // 2:
                return self._reject(now.ts, "choch", "CHOCH 가 너무 오래됨")
            ctx.choch_index = ch.index
            ctx.reasons.append(f"LTF({cfg.ltf}) CHOCH {ch.level:.3f} 돌파 → 구조 전환")

        # --- 4) 주문 구성 ---------------------------------------------
        a_ltf = atr(ltf_c[-100:])
        tol = a_ltf * cfg.poi_touch_atr
        poi = self._pick_poi(pois, now.close, side, tol)
        if poi is None:
            return self._reject(now.ts, "entry", "되돌림으로 도달 가능한 POI 없음")
        ctx.poi = poi

        # 가격이 이미 POI 안(또는 허용 오차 내)이면 즉시 진입, 아니면 지정가 대기.
        at_poi = (poi.bottom - tol) <= now.close <= (poi.top + tol)
        if at_poi and cfg.market_if_at_poi:
            order_type = "market"
            entry = now.close
        else:
            order_type = cfg.entry_style
            entry = poi.entry_price(cfg.entry_at)
            if a_ltf > 0 and abs(entry - now.close) > cfg.max_entry_distance_atr * a_ltf:
                return self._reject(now.ts, "distance",
                                    "POI 가 현재가에서 너무 멀다 (되돌림 대기)")
        ctx.reasons.append(
            f"진입 POI: {poi.kind} {poi.bottom:.3f}~{poi.top:.3f} ({'즉시' if order_type == 'market' else '지정가'})")

        sl = self._stop(side, entry, poi, sweep, a_ltf, cfg)
        tp = self._target(side, entry, sl, mtf_c, sw, cfg, a_ltf)

        ok, rr = validate_rr(entry, sl, tp, side, cfg.min_rr)
        if not ok:
            return self._reject(now.ts, "rr", f"RR {rr:.2f} < 최소 {cfg.min_rr}")

        lots, risk_amt = position_size(balance, cfg.risk_pct, entry, sl, cfg)
        if lots <= 0:
            return self._reject(now.ts, "sizing", "계산된 랏이 최소 단위 미만 (잔고/SL 폭 확인)")

        score = self._score(ctx, wy, rr)
        return Signal(
            ts=now.ts, symbol=self.symbol, side=side, entry=entry, sl=sl, tp=tp,
            lots=lots, risk_amount=risk_amt, rr=rr,
            order_type=order_type, expiry_bars=cfg.limit_expiry_bars,
            timeframe=f"{cfg.htf}>{cfg.mtf}>{cfg.ltf}",
            reasons=tuple(ctx.reasons), score=score,
        )

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------
    @staticmethod
    def _decide_side(structure_bias: str | None, wyckoff_bias: str | None) -> Side | None:
        s = {"bullish": "buy", "bearish": "sell"}.get(structure_bias or "")
        if wyckoff_bias and s and wyckoff_bias != s:
            return None            # 구조와 국면이 싸우면 관망
        return (wyckoff_bias or s) or None  # type: ignore[return-value]

    @staticmethod
    def _pick_poi(pois: Sequence[liq.POI], price: float, side: Side, tol: float = 0.0) -> liq.POI | None:
        """현재가 아래(매수)/위(매도)에 있거나 지금 닿아 있는 구역 중 가장 가까운 것."""
        if side == "buy":
            cand = [p for p in pois if p.bottom <= price + tol]
            return max(cand, key=lambda p: p.top) if cand else None
        cand = [p for p in pois if p.top >= price - tol]
        return min(cand, key=lambda p: p.bottom) if cand else None

    @staticmethod
    def _stop(side: Side, entry: float, poi: liq.POI, sweep: liq.Sweep | None,
              a: float, cfg: CrowConfig) -> float:
        buf = max(a * cfg.sl_buffer_atr, 1e-6)
        if side == "buy":
            base = poi.bottom
            if sweep is not None and sweep.direction == "below":
                base = min(base, sweep.extreme)
            return base - buf
        base = poi.top
        if sweep is not None and sweep.direction == "above":
            base = max(base, sweep.extreme)
        return base + buf

    @staticmethod
    def _target(side: Side, entry: float, sl: float, mtf_c: Sequence[Candle],
                ltf_swings, cfg: CrowConfig, a: float) -> float:
        """1순위: 반대편 유동성(동일 고·저점). 2순위: 고정 target_rr."""
        risk = abs(entry - sl)
        fixed = entry + risk * cfg.target_rr if side == "buy" else entry - risk * cfg.target_rr

        m_sw = swing_points(mtf_c, cfg.swing_left, cfg.swing_right)
        tol = a * cfg.equal_level_tol_atr if a > 0 else 0.0
        pools = liq.liquidity_pools(m_sw, tol if tol > 0 else 0.1)
        if side == "buy":
            above = [p.price for p in pools if p.kind == "equal_highs" and p.price > entry]
            if above:
                cand = min(above)
                if (cand - entry) / risk >= cfg.min_rr:
                    return cand
        else:
            below = [p.price for p in pools if p.kind == "equal_lows" and p.price < entry]
            if below:
                cand = max(below)
                if (entry - cand) / risk >= cfg.min_rr:
                    return cand
        return fixed

    @staticmethod
    def _score(ctx: Context, wy: wyckoff.WyckoffView, rr: float) -> float:
        s = 0.0
        if ctx.htf_bias:
            s += 1.0
        if wy.bias:
            s += 1.0
        if wy.spring is not None or wy.upthrust is not None:
            s += 0.5
        if ctx.sweep is not None:
            s += 1.0
        if ctx.choch_index is not None:
            s += 1.0
        if ctx.poi and ctx.poi.kind == "order_block":
            s += 0.5
        s += min(rr / 3.0, 1.5)
        return s

    def _reject(self, ts: datetime, rule: str, detail: str) -> None:
        self.rejections.append(Rejection(ts, rule, detail))
        return None

    def rejection_summary(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.rejections:
            out[r.rule] = out.get(r.rule, 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))
