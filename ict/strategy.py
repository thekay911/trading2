"""ICT 모델 전부 — 사전계산 엔진(Market) 위에서.

여기가 실제로 쓰는 진입점이다. models.py / playbook.py 의 느린 경로를
대체한다. 모든 모델이 같은 `Setup` 을 돌려주므로 백테스터가 그대로 받는다.

  ICT2022        습격 → MSS+변위 → PD Array 복귀 → 프리미엄/디스카운트
  SilverBullet   뉴욕 10~11시 창 안의 FVG
  TurtleSoup     PDH/PDL 가짜 돌파 후 되돌림
  JudasSwing     아시아 레인지를 턴 뒤 반대 방향 (PO3 의 M 구간)
  OTE            변위 레그의 62~79% 되돌림
  Unicorn        브레이커 + FVG 겹침
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal, Sequence

from ict import liquidity as liq
from ict import pdarrays as pda
from ict.engine import Market
from ict.gold import GoldProfile
from ict.models import Config, Setup
from ict.plays import ACTIVE
from ict.ranges import leg_range
from ict.structure import BEAR, BULL, Dir
from ict.timeops import (
    LONDON_KZ, NY_AM_KZ, SILVER_BULLET_AM, SILVER_BULLET_PM,
    active_windows, in_killzone, ny_clock, ny_date, session_of,
)

Side = Literal["buy", "sell"]


# ----------------------------------------------------------------------
def _raid(m: Market, now: int, side: Side, within: int) -> liq.Raid | None:
    """진입 방향에 맞는 최근 유동성 습격.

    풀의 `taken_at` 을 그대로 쓴다 — 습격은 그 풀이 처음 뚫린 그 봉이다.
    """
    want = "SSL" if side == "buy" else "BSL"
    best: liq.Raid | None = None
    for p in m.pools(now):
        if p.kind != want or p.taken_at is None:
            continue
        if not (0 <= now - p.taken_at <= within):
            continue
        c = m.candles[p.taken_at]
        ext = c.low if side == "buy" else c.high
        r = liq.Raid(p.taken_at, c.ts, BULL if side == "buy" else BEAR, p, ext,
                     (c.close > p.price) if side == "buy" else (c.close < p.price))
        if best is None or r.index > best.index:
            best = r
    return best


def _targets(m: Market, now: int, side: Side, entry: float, risk: float,
             min_rr: float, max_rr: float) -> liq.Pool | None:
    """min_rr 을 넘는 가장 가까운 유동성. 단 max_rr 보다 멀면 쓰지 않는다."""
    for p in liq.draw_targets(m.pools(now), side, entry):
        rr = abs(p.price - entry) / risk
        if rr < min_rr:
            continue
        return p if rr <= max_rr else None
    return None


def _build(m: Market, now: int, model: str, side: Side, entry: float, stop: float,
           array: pda.PDArray, mss, raid, kz: str, notes: list[str],
           cfg: Config) -> Setup | None:
    gold = m.gold
    risk = abs(entry - stop)
    if risk <= 0 or not gold.spread_ok(risk):
        return None
    # 너무 먼 지정가는 채워질 때쯤 근거가 이미 죽어 있다. 모든 모델 공통.
    if abs(entry - m.candles[now].close) > m.volatility(now).atr * gold.max_entry_distance_atr:
        return None
    pool = _targets(m, now, side, entry, risk, cfg.min_rr, cfg.max_rr)
    target = pool.price if pool else (entry + risk * cfg.default_rr if side == "buy"
                                      else entry - risk * cfg.default_rr)
    s = Setup(ts=m.candles[now].ts, index=now, model=model, side=side,
              entry=entry, stop=stop, target=target, array=array, raid=raid,
              mss=mss, target_pool=pool, killzone=kz,
              mss_index=mss.index if mss else -1, notes=notes)
    return s if cfg.min_rr <= s.rr <= cfg.max_rr + 1e-9 else None


def _gate(m: Market, now: int, cfg: Config,
          windows: Sequence = (LONDON_KZ, NY_AM_KZ, SILVER_BULLET_AM)) -> str | None:
    """공통 시간 게이트. 통과하면 킬존 이름을 돌려준다."""
    bar = m.candles[now]
    if m.gold.in_rollover(bar.ts):
        return None
    if cfg.require_killzone and not any(w.contains(bar.ts) for w in windows):
        return None
    kz = in_killzone(bar.ts)
    return kz.name if kz else session_of(bar.ts)


# ----------------------------------------------------------------------
# 1. ICT 2022 모델
# ----------------------------------------------------------------------
def ict2022(m: Market, now: int, cfg: Config) -> Setup | None:
    kz = _gate(m, now, cfg)
    if kz is None:
        return None
    mss = m.last_mss(now, cfg.mss_lookback)
    if mss is None:
        return None
    side: Side = "buy" if mss.direction == BULL else "sell"

    raid = _raid(m, now, side, cfg.raid_lookback) if cfg.require_raid else None
    if cfg.require_raid and (raid is None or raid.index > mss.index):
        return None

    d = mss.displacement
    gaps = m.fresh_fvgs(now, mss.direction, d.start if d else mss.index - 10)
    if not gaps:
        return None
    array = gaps[0]
    entry = array.mid
    bar = m.candles[now]
    if (side == "buy" and entry > bar.close) or (side == "sell" and entry < bar.close):
        return None

    v = m.volatility(now)
    notes: list[str] = []
    if cfg.require_discount_premium and d:
        dr = leg_range(m.candles, d.start, d.end)
        if dr is None:
            return None
        if side == "buy" and not dr.is_discount(entry):
            return None
        if side == "sell" and not dr.is_premium(entry):
            return None
        notes.append(f"레인지 {dr.position(entry):.0%} "
                     f"({'디스카운트' if side == 'buy' else '프리미엄'})")
        if dr.in_ote(entry, side):
            notes.append("OTE 구간")

    buf = m.gold.stop_buffer(v)
    base = raid.extreme if raid else (array.bottom if side == "buy" else array.top)
    stop = (min(base, array.bottom) - buf) if side == "buy" else (max(base, array.top) + buf)
    if d:
        notes.append(f"변위 {d.atr_multiple:.1f}×ATR")
    return _build(m, now, "ICT2022", side, entry, stop, array, mss, raid, kz, notes, cfg)


# ----------------------------------------------------------------------
# 2. 실버 불릿
# ----------------------------------------------------------------------
def silver_bullet(m: Market, now: int, cfg: Config, pm: bool = False) -> Setup | None:
    window = SILVER_BULLET_PM if pm else SILVER_BULLET_AM
    bar = m.candles[now]
    if not window.contains(bar.ts) or m.gold.in_rollover(bar.ts):
        return None
    start = now
    while start > 0 and window.contains(m.candles[start - 1].ts):
        start -= 1
    if now - start < 3:
        return None

    mss = m.last_mss(now, 120)
    if mss is None:
        return None
    side: Side = "buy" if mss.direction == BULL else "sell"
    raid = _raid(m, now, side, 120)

    gaps = m.fresh_fvgs(now, mss.direction, start)      # 창 안에서 생긴 것만
    if not gaps:
        return None
    array = gaps[0]
    entry = array.mid
    if (side == "buy" and entry > bar.close) or (side == "sell" and entry < bar.close):
        return None

    v = m.volatility(now)
    buf = m.gold.stop_buffer(v)
    base = raid.extreme if raid else (array.bottom if side == "buy" else array.top)
    stop = (min(base, array.bottom) - buf) if side == "buy" else (max(base, array.top) + buf)
    notes = [f"실버불릿 {'PM' if pm else 'AM'} 창 안 FVG"]
    if raid:
        notes.append(f"{raid.pool.label} {raid.pool.price:.2f} 습격 후")
    return _build(m, now, "SilverBullet", side, entry, stop, array, mss, raid,
                  window.name, notes, cfg)


# ----------------------------------------------------------------------
# 3. 터틀 수프
# ----------------------------------------------------------------------
def turtle_soup(m: Market, now: int, cfg: Config, within: int = 10) -> Setup | None:
    kz = _gate(m, now, cfg)
    if kz is None:
        return None
    bar = m.candles[now]
    for side in ("buy", "sell"):
        raid = _raid(m, now, side, within)               # type: ignore[arg-type]
        if raid is None or not raid.closed_back:
            continue
        entry = raid.pool.price
        if (side == "buy" and bar.close < entry) or (side == "sell" and bar.close > entry):
            continue
        v = m.volatility(now)
        buf = m.gold.stop_buffer(v)
        stop = raid.extreme - buf if side == "buy" else raid.extreme + buf
        array = pda.rejection_block(m.candles, raid.index,
                                    BULL if side == "buy" else BEAR)
        if array is None:
            array = pda.PDArray("REJECTION", BULL if side == "buy" else BEAR,
                                max(entry, raid.extreme), min(entry, raid.extreme),
                                raid.index)
        mss = m.last_mss(now, 200)
        notes = [f"{raid.pool.label} {raid.pool.price:.2f} 가짜 돌파 후 종가 회복",
                 f"꼬리 극점 {raid.extreme:.2f}"]
        s = _build(m, now, "TurtleSoup", side, entry, stop, array, mss, raid,  # type: ignore[arg-type]
                   kz, notes, cfg)
        if s:
            return s
    return None


# ----------------------------------------------------------------------
# 4. 유다 스윙 (PO3)
# ----------------------------------------------------------------------
def judas_swing(m: Market, now: int, cfg: Config,
                open_hour: float = 2.0, window_h: float = 3.0) -> Setup | None:
    bar = m.candles[now]
    h = ny_clock(bar.ts)
    if not (open_hour <= h < open_hour + window_h) or m.gold.in_rollover(bar.ts):
        return None
    today = ny_date(bar.ts)
    ar = m._asia_cache.get(today)          # 사전 계산된 값 (전체 훑기 방지)
    if ar is None:
        return None
    a_hi, a_lo = ar
    if a_hi <= a_lo:
        return None

    since = [c for c in m.candles[max(0, now - 60):now + 1]
             if ny_date(c.ts) == today and ny_clock(c.ts) >= open_hour]
    if len(since) < 3:
        return None
    swept_high = max(c.high for c in since) > a_hi
    swept_low = min(c.low for c in since) < a_lo
    if swept_high == swept_low:
        return None

    side: Side = "sell" if swept_high else "buy"
    direction: Dir = BEAR if swept_high else BULL
    extreme = max(c.high for c in since) if swept_high else min(c.low for c in since)

    mss = m.last_mss(now, cfg.mss_lookback, direction)
    if mss is None:
        return None
    gaps = m.fresh_fvgs(now, direction,
                        mss.displacement.start if mss.displacement else mss.index - 10)
    if not gaps:
        return None
    array = gaps[0]
    entry = array.mid
    if (side == "buy" and entry > bar.close) or (side == "sell" and entry < bar.close):
        return None

    v = m.volatility(now)
    buf = m.gold.stop_buffer(v)
    stop = extreme + buf if side == "sell" else extreme - buf
    notes = [f"아시아 레인지 {a_lo:.2f}~{a_hi:.2f} 의 "
             f"{'고점' if swept_high else '저점'} 을 털고 반대로",
             f"조작 극점 {extreme:.2f}"]
    return _build(m, now, "JudasSwing", side, entry, stop, array, mss, None,
                  "LondonKZ" if open_hour < 6 else "NY_AM_KZ", notes, cfg)


# ----------------------------------------------------------------------
# 5. OTE
# ----------------------------------------------------------------------
def ote(m: Market, now: int, cfg: Config) -> Setup | None:
    kz = _gate(m, now, cfg)
    if kz is None:
        return None
    mss = m.last_mss(now, cfg.mss_lookback)
    if mss is None or mss.displacement is None:
        return None
    d = mss.displacement
    dr = leg_range(m.candles, d.start, d.end)
    if dr is None:
        return None
    side: Side = "buy" if mss.direction == BULL else "sell"
    bar = m.candles[now]
    if not dr.in_ote(bar.close, side):
        return None

    v = m.volatility(now)
    buf = m.gold.stop_buffer(v)
    entry = bar.close
    stop = (dr.low - buf) if side == "buy" else (dr.high + buf)
    array = pda.PDArray("FVG", mss.direction, max(dr.ote(side)), min(dr.ote(side)), now)
    pos = dr.position(entry)
    notes = [f"변위 레그 {dr.low:.2f}~{dr.high:.2f} 의 "
             f"{(1 - pos) if side == 'buy' else pos:.0%} 되돌림", "OTE 62~79%"]
    return _build(m, now, "OTE", side, entry, stop, array, mss, None, kz, notes, cfg)


# ----------------------------------------------------------------------
# 6. 유니콘
# ----------------------------------------------------------------------
def unicorn(m: Market, now: int, cfg: Config) -> Setup | None:
    kz = _gate(m, now, cfg)
    if kz is None:
        return None
    mss = m.last_mss(now, cfg.mss_lookback)
    if mss is None or mss.displacement is None:
        return None
    d = mss.displacement
    gaps = m.fresh_fvgs(now, mss.direction, d.start)
    if not gaps:
        return None
    ob = pda.order_block(m.candles, mss.index, -mss.direction)
    br = pda.breaker(m.candles, ob, now) if ob else None
    if br is None or br.direction != mss.direction:
        return None
    uni = pda.unicorn([br] + gaps)
    if uni is None:
        return None

    side: Side = "buy" if mss.direction == BULL else "sell"
    bar = m.candles[now]
    entry = uni.mid
    if (side == "buy" and entry > bar.close) or (side == "sell" and entry < bar.close):
        return None
    v = m.volatility(now)
    buf = m.gold.stop_buffer(v)
    stop = uni.bottom - buf if side == "buy" else uni.top + buf
    return _build(m, now, "Unicorn", side, entry, stop, uni, mss, None, kz,
                  ["브레이커와 FVG 겹침 (유니콘)"], cfg)


# ----------------------------------------------------------------------
MODELS: dict[str, Callable[..., Setup | None]] = {
    "ICT2022": ict2022,
    "SilverBullet": silver_bullet,
    "TurtleSoup": turtle_soup,
    "JudasSwing": judas_swing,
    "OTE": ote,
    "Unicorn": unicorn,
}


def scan(m: Market, cfg: Config = Config(), models: Sequence[str] | None = None,
         start: int = 300, cooldown: int = 12) -> list[Setup]:
    """전 구간 훑기. 모델별로 `cooldown` 봉 안의 중복은 버린다.

    models 를 안 주면 `ict.plays.ACTIVE` (실측에서 살아남은 모델) 만 돈다.
    전부 보려면 `models=list(MODELS)` 를 넘긴다.
    """
    names = list(models) if models else list(ACTIVE)
    out: list[Setup] = []
    last: dict[str, int] = {}
    for i in range(start, len(m)):
        for name in names:
            if i - last.get(name, -10 ** 9) < cooldown:
                continue
            s = MODELS[name](m, i, cfg)
            if s is not None:
                out.append(s)
                last[name] = i
    out.sort(key=lambda s: s.index)
    return out
