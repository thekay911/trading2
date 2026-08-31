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
from ict import context as ctxmod
from ict import quality as qual
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
    if risk <= 0 or not gold.spread_ok(risk, m.candles[now].close):
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
# 7. TJR — 스윕 -> 변위 -> 기원(오더블록) 되돌림
# ----------------------------------------------------------------------
def tjr(m: Market, now: int, cfg: Config, sweep_within: int = 40) -> Setup | None:
    """TJR 이 공개적으로 설명하는 순서를 그대로 옮긴 것.

      1. 명확한 유동성 (동일 고저 / 안 건드린 스윙 / 전일 고저)
      2. 스윕 — 꼬리가 레벨을 뚫고 **종가는 다시 안으로**
         (봉이 열려 있는 동안 스윕이라 부르지 않는다. 종가로만 확정)
      3. 구조 전환 — 직전 반대편 스윙을 깬다
      4. 진입 — 그 충격의 **기원**, 즉 마지막 반대색 캔들(오더블록)로 되돌아올 때
      5. 손절 — 유동성을 가져간 그 꼬리 바깥
      6. 목표 — 반대편 유동성 풀

    ICT2022 와 다른 점은 진입 자리다. ICT2022 는 FVG 의 중간(CE)에 걸고,
    TJR 은 충격이 시작된 오더블록에 건다. 그래서 진입가도 손절폭도 달라진다.
    """
    kz = _gate(m, now, cfg)
    if kz is None:
        return None

    for side in ("buy", "sell"):
        raid = _raid(m, now, side, sweep_within)      # type: ignore[arg-type]
        if raid is None or not raid.closed_back:
            continue                                   # 종가가 안 돌아왔으면 스윕이 아니다

        direction = BULL if side == "buy" else BEAR
        mss = m.last_mss(now, cfg.mss_lookback, direction)
        if mss is None or mss.index < raid.index:
            continue                                   # 전환은 스윕 뒤에 와야 한다

        d = mss.displacement
        if d is None:
            continue

        # 충격의 기원: 변위 구간 안에서 마지막 반대색 캔들
        ob = pda.order_block(m.candles, mss.index, direction)
        if ob is None or ob.index < raid.index:
            continue

        entry = ob.top if side == "buy" else ob.bottom
        bar = m.candles[now]
        if (side == "buy" and entry > bar.close) or (side == "sell" and entry < bar.close):
            continue                                   # 이미 지나간 자리

        v = m.volatility(now)
        buf = m.gold.stop_buffer(v)
        stop = raid.extreme - buf if side == "buy" else raid.extreme + buf
        if (side == "buy" and stop >= entry) or (side == "sell" and stop <= entry):
            continue

        notes = [f"{raid.pool.label} {raid.pool.price:.2f} 스윕 후 종가 복귀",
                 f"변위 {d.atr_multiple:.1f}xATR 로 구조 전환",
                 f"기원 오더블록 {ob.bottom:.2f}~{ob.top:.2f} 되돌림 대기"]
        s = _build(m, now, "TJR", side, entry, stop, ob, mss, raid,  # type: ignore[arg-type]
                   kz, notes, cfg)
        if s:
            return s
    return None



# ----------------------------------------------------------------------
# 8. CISD — Change In State Of Delivery
# ----------------------------------------------------------------------
def _run_of(m: Market, now: int, down: bool, max_len: int = 12) -> tuple[int, int] | None:
    """`now` 직전까지 한쪽 색 캔들이 지배한 구간 (start, end).

    영상의 표현: "음봉들이 양봉을 삼키며 통제하고 있다".
    여기서는 그 구간 안에서 지배색 캔들이 과반이고, 구간 전체가 한 방향으로
    움직였을 때만 인정한다.
    """
    end = now - 1
    if end < 3:
        return None
    for length in range(3, max_len + 1):
        start = end - length + 1
        if start < 1:
            break
        seg = m.candles[start:end + 1]
        want = sum(1 for c in seg if (c.close < c.open) == down)
        if want < len(seg) * 0.6:
            continue
        moved = (seg[0].open - seg[-1].close) if down else (seg[-1].close - seg[0].open)
        if moved <= 0:
            continue
        return start, end
    return None


def cisd(m: Market, now: int, cfg: Config) -> Setup | None:
    """배달 상태의 전환.

    하락을 지배하던 구간 전체를 한 캔들이 되돌려 감싸면(그 구간의 시가를
    종가로 넘어서면) 통제권이 넘어간 것으로 본다. 손절은 그 구간의 최저점,
    목표는 반대편 유동성.

    영상이 말하는 순서를 그대로 옮긴 것이고, 진입 자리가 FVG 도 오더블록도
    아니라는 점에서 기존 모델과 다르다 — 되돌림을 기다리지 않고 전환하는
    그 봉의 종가에 들어간다.
    """
    kz = _gate(m, now, cfg)
    if kz is None:
        return None
    bar = m.candles[now]

    for side in ("buy", "sell"):
        down = (side == "buy")            # 매수는 하락 구간이 감싸질 때
        run = _run_of(m, now, down)
        if run is None:
            continue
        start, end = run
        seg = m.candles[start:end + 1]
        # 그 구간의 시작점을 종가로 넘어섰는가
        if down and not (bar.close > seg[0].open):
            continue
        if not down and not (bar.close < seg[0].open):
            continue
        # 감싸는 봉은 구간의 평균 몸통보다 커야 한다
        bodies = [abs(c.close - c.open) for c in seg]
        if abs(bar.close - bar.open) < (sum(bodies) / len(bodies)):
            continue

        v = m.volatility(now)
        buf = m.gold.stop_buffer(v)
        extreme = min(c.low for c in seg) if down else max(c.high for c in seg)
        entry = bar.close
        stop = extreme - buf if down else extreme + buf
        if (side == "buy" and stop >= entry) or (side == "sell" and stop <= entry):
            continue

        arr = pda.PDArray("CISD", BULL if side == "buy" else BEAR,
                          max(entry, extreme), min(entry, extreme), now)
        raid = _raid(m, now, side, 20)        # type: ignore[arg-type]
        notes = [f"{end - start + 1}봉 {'하락' if down else '상승'} 구간을 종가로 되돌림",
                 f"구간 극점 {extreme:.2f}"]
        s = _build(m, now, "CISD", side, entry, stop, arr,   # type: ignore[arg-type]
                   m.last_mss(now, 200), raid, kz, notes, cfg)
        if s:
            return s
    return None


# ----------------------------------------------------------------------
# 9. iFVG — 뚫린 FVG 를 반대로 쓴다
# ----------------------------------------------------------------------
def ifvg(m: Market, now: int, cfg: Config, within: int = 60) -> Setup | None:
    """FVG 가 종가로 뚫리면 반대 역할이 된다.

    영상: "하락 FVG 들이 레벨로 내려가는데, 그중 하나를 종가로 뚫었다.
    약세라면 일어나지 않을 일이다 -> 상승 확률이 높다."

    `pdarrays.inversion_fvgs` 는 전부터 있었지만 이걸 쓰는 모델이 없었다.
    """
    kz = _gate(m, now, cfg)
    if kz is None:
        return None
    bar = m.candles[now]
    since = max(0, now - within)

    for side in ("buy", "sell"):
        want = BULL if side == "buy" else BEAR
        inv = m.inverted(now, want, within=12, since=since)
        if not inv:
            continue
        arr = inv[0]                      # 가장 최근에 뚫린 것

        entry = arr.mid
        if (side == "buy" and entry > bar.close) or (side == "sell" and entry < bar.close):
            continue

        v = m.volatility(now)
        buf = m.gold.stop_buffer(v)
        stop = arr.bottom - buf if side == "buy" else arr.top + buf
        if (side == "buy" and stop >= entry) or (side == "sell" and stop <= entry):
            continue

        notes = [f"{'하락' if want == BULL else '상승'} FVG "
                 f"{arr.bottom:.2f}~{arr.top:.2f} 가 종가로 뚫려 반전",
                 f"뚫린 지 {now - arr.index}봉"]
        s = _build(m, now, "iFVG", side, entry, stop, arr,
                   m.last_mss(now, 200), _raid(m, now, side, 20),  # type: ignore[arg-type]
                   kz, notes, cfg)
        if s:
            return s
    return None


# ----------------------------------------------------------------------
# 10. 실패한 돌파 — 변위 없이 구조를 건드리면 반대로 간다
# ----------------------------------------------------------------------
def failed_break(m: Market, now: int, cfg: Config, within: int = 20) -> Setup | None:
    """구조를 건드렸는데 변위가 없으면 그 방향은 실패다.

    이 저장소는 그동안 '변위 없는 MSS 는 MSS 가 아니다' 로 **버렸다**.
    영상은 같은 사건을 **반대 방향 진입 근거**로 쓴다 —
    "에너지 있게 뚫지 못하면 되돌린다. 그리고 제 일을 못 한 그 고점을
    목표로 삼아라."

    같은 관찰을 버리느냐 쓰느냐의 차이라 재볼 값어치가 있다.
    """
    kz = _gate(m, now, cfg)
    if kz is None:
        return None
    sw = [x for x in m.swings if x.confirmed_at <= now and x.index >= now - 120]
    if not sw:
        return None
    bar = m.candles[now]
    v = m.volatility(now)
    need = m.gold.displacement(v)
    buf = m.gold.stop_buffer(v)

    for side in ("buy", "sell"):
        # 매수: 저점을 건드렸지만 아래로 변위하지 못했다
        pts = [x for x in sw if (not x.is_high) == (side == "buy")]
        if not pts:
            continue
        ref = min(pts, key=lambda x: x.price) if side == "buy"             else max(pts, key=lambda x: x.price)
        broke_at = None
        for k in range(max(ref.confirmed_at + 1, now - within), now + 1):
            c = m.candles[k]
            if (side == "buy" and c.low < ref.price) or                (side == "sell" and c.high > ref.price):
                broke_at = k
                break
        if broke_at is None:
            continue

        seg = m.candles[broke_at:now + 1]
        travel = (ref.price - min(c.low for c in seg)) if side == "buy"             else (max(c.high for c in seg) - ref.price)
        if travel >= need:
            continue                       # 제대로 변위했다 -> 실패가 아니다
        if (side == "buy" and bar.close <= ref.price) or            (side == "sell" and bar.close >= ref.price):
            continue                       # 아직 안으로 돌아오지 않았다

        extreme = min(c.low for c in seg) if side == "buy" else max(c.high for c in seg)
        entry = bar.close
        stop = extreme - buf if side == "buy" else extreme + buf
        if (side == "buy" and stop >= entry) or (side == "sell" and stop <= entry):
            continue

        arr = pda.PDArray("FAILBREAK", BULL if side == "buy" else BEAR,
                          max(entry, extreme), min(entry, extreme), broke_at)
        notes = [f"스윙 {ref.price:.2f} 을 건드렸으나 변위 실패 "
                 f"({travel:.2f} < {need:.2f})",
                 "제 일을 못 한 반대편 극점이 목표"]
        s = _build(m, now, "FailedBreak", side, entry, stop, arr,
                   m.last_mss(now, 200), None, kz, notes, cfg)
        if s:
            return s
    return None


# ----------------------------------------------------------------------
MODELS: dict[str, Callable[..., Setup | None]] = {
    "ICT2022": ict2022,
    "SilverBullet": silver_bullet,
    "TurtleSoup": turtle_soup,
    "JudasSwing": judas_swing,
    "OTE": ote,
    "Unicorn": unicorn,
    "TJR": tjr,
    "CISD": cisd,
    "iFVG": ifvg,
    "FailedBreak": failed_break,
}


def scan(m: Market, cfg: Config = Config(), models: Sequence[str] | None = None,
         start: int = 300, cooldown: int = 12,
         select: bool = True, per_day: int = qual.PER_DAY) -> list[Setup]:
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
    if not select:
        return out

    # ICT Ep.2: 참조점을 보기 전에 지금이 어떤 상태인지부터 정한다.
    # 확장 중에 들어가면 이미 간 걸 쫓는 것이다. 그 다음 하루 상한.
    scored = []
    for s in out:
        c, rng = ctxmod.context(m.candles, s.index, m.atr_at(s.index))
        j = qual.judge(s, c, rng)
        if j is not None:
            scored.append(j)
    return qual.best_per_day(scored, per_day)
