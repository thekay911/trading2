"""ICTGold.mq5 의 로직을 그대로 파이썬으로 옮긴 것.

왜 필요한가
-----------
여기엔 MetaEditor 가 없어서 EA 를 컴파일도 실행도 할 수 없다. 그래서 EA 는
파이썬 모델을 손으로 다시 옮겨 쓴 것이고, **둘이 같은 거래를 하는지 아무도
확인한 적이 없었다.** 실계좌에서 확인됐다 — 계좌가 90% 녹았다.

이 파일은 그 검증을 데이터로 한다. MQL5 코드를 한 줄씩 파이썬으로 옮기고
(근사가 아니라 같은 인덱싱, 같은 조건, 같은 순서), 같은 21년 데이터에
돌려서 `ict.strategy` 모델과 결과가 갈리는 지점을 찾는다.

옮길 때의 규칙
  · MQL5 배열 인덱싱을 그대로 (0 = 가장 오래된 봉, ArraySetAsSeries(false))
  · 판정 순서도 그대로 — 모델 평가 순서, 첫 매치에서 return
  · 필터를 '있어야 할 것' 이 아니라 '실제로 코드에 있는 것' 만 옮긴다
    (EA 의 TurtleSoup 에는 킬존 검사가 없다. 그 사실이 재현돼야 한다)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Sequence

from crowcode.data import Candle
from ict.gold import STANDARD, GoldProfile, bp_to_price
from ict.timeops import ny_clock, ny_date


# ----------------------------------------------------------------------
# EA 의 입력값 (ICTGold.mq5 의 input 과 1:1)
# ----------------------------------------------------------------------
@dataclass
class EaInputs:
    use_unicorn: bool = True
    use_judas: bool = False
    use_turtle: bool = True
    use_ote: bool = False
    use_tjr: bool = False

    uni_rr: float = 4.0
    uni_hold_min: int = 480
    uni_risk: float = 2.0
    ts_rr: float = 4.0
    ts_hold_min: int = 480
    ts_risk: float = 2.0

    atr_period: int = 20
    displacement_atr: float = 1.5
    displacement_bp: float = 8.0
    min_fvg_atr: float = 0.20
    min_fvg_bp: float = 1.5
    fvg_spread_mult: float = 2.0
    stop_buffer_atr: float = 0.25
    stop_buffer_bp: float = 1.5
    max_entry_dist_atr: float = 3.0

    max_spread_to_stop: float = 0.15
    max_spread_price: float = 0.60
    spread_floor_bp: float = 0.55
    limit_expiry_bars: int = 24
    swing_left: int = 1
    swing_right: int = 1
    lookback_bars: int = 600

    block_rollover: bool = True
    rollover_start: float = 17.0
    rollover_end: float = 20.0
    block_friday_late: bool = True
    friday_cutoff_ny: float = 15.0

    require_killzone: bool = True
    require_context: bool = True
    cooldown_bars: int = 12
    context_lookback: int = 40
    min_rr: float = 2.0

    max_trades_per_day: int = 3
    hard_cap_per_day: int = 10
    max_consec_losses: int = 3
    max_daily_loss_pct: float = 6.0
    hard_stop_pct: float = 10.0


@dataclass
class EaSetup:
    model: str
    is_buy: bool
    is_limit: bool
    entry: float
    stop: float
    target: float
    risk_pct: float
    hold_min: int
    index: int
    ts: datetime
    why: str = ""


# ----------------------------------------------------------------------
# MQL5 헬퍼를 그대로
# ----------------------------------------------------------------------
class Bars:
    """MQL5 의 H[]/L[]/O[]/C[]/T[] 와 같은 인덱싱."""

    def __init__(self, candles: Sequence[Candle]):
        self.c = list(candles)
        self.H = [x.high for x in self.c]
        self.L = [x.low for x in self.c]
        self.O = [x.open for x in self.c]
        self.C = [x.close for x in self.c]
        self.T = [x.ts for x in self.c]
        self._atr: list[float] = []

    def __len__(self) -> int:
        return len(self.c)


def rolling_atr(b: Bars, period: int) -> list[float]:
    """EA 의 AtrAt() 을 전 구간에 대해 한 번에. 값은 동일하다."""
    out = [0.0] * len(b)
    if len(b) <= period:
        return out
    trs = [0.0] * len(b)
    for k in range(1, len(b)):
        a = b.H[k] - b.L[k]
        x = abs(b.H[k] - b.C[k - 1])
        y = abs(b.L[k] - b.C[k - 1])
        trs[k] = max(a, x, y)
    run = sum(trs[1:period + 1])
    for i in range(period, len(b)):
        if i > period:
            run += trs[i] - trs[i - period]
        out[i] = run / period
    return out


def scaled(atr: float, mult: float, bp: float, price: float) -> float:
    return max(atr * mult, bp_to_price(bp, price))


def is_swing_high(b: Bars, i: int, left: int, right: int) -> bool:
    if i - left < 0 or i + right >= len(b):
        return False
    for k in range(i - left, i + right + 1):
        if k != i and b.H[k] > b.H[i]:
            return False
    return True


def is_swing_low(b: Bars, i: int, left: int, right: int) -> bool:
    if i - left < 0 or i + right >= len(b):
        return False
    for k in range(i - left, i + right + 1):
        if k != i and b.L[k] < b.L[i]:
            return False
    return True


def is_displacement(b: Bars, atr: list[float], inp: EaInputs,
                    start: int, end: int, direction: int) -> tuple[bool, float, float]:
    """EA 의 IsDisplacement() 그대로."""
    if end <= start or start < 1:
        return False, 0.0, 0.0
    a = atr[end]
    if a <= 0:
        return False, 0.0, 0.0
    need = scaled(a, inp.displacement_atr, inp.displacement_bp, b.C[end])
    hi, lo = b.H[start], b.L[start]
    for k in range(start, end + 1):
        hi = max(hi, b.H[k])
        lo = min(lo, b.L[k])
    if hi - lo < need:
        return False, lo, hi
    for k in range(start + 1, end):
        if direction > 0 and b.L[k + 1] > b.H[k - 1]:
            return True, lo, hi
        if direction < 0 and b.H[k + 1] < b.L[k - 1]:
            return True, lo, hi
    return False, lo, hi


def find_mss(b: Bars, atr: list[float], inp: EaInputs, now: int, within: int):
    """EA 의 FindMss() 그대로 — 이중 루프와 break 위치까지 동일."""
    lo_limit = inp.swing_left + 2
    for i in range(now, max(now - within, lo_limit), -1):
        for j in range(i - 1, max(i - 60, inp.swing_left), -1):
            if is_swing_high(b, j, inp.swing_left, inp.swing_right) \
                    and b.C[i] > b.H[j] and b.C[i - 1] <= b.H[j]:
                ok, lo, hi = is_displacement(b, atr, inp, j, i, +1)
                if ok:
                    return +1, j, i, lo, hi
                break
            if is_swing_low(b, j, inp.swing_left, inp.swing_right) \
                    and b.C[i] < b.L[j] and b.C[i - 1] >= b.L[j]:
                ok, lo, hi = is_displacement(b, atr, inp, j, i, -1)
                if ok:
                    return -1, j, i, lo, hi
                break
    return 0, 0, 0, 0.0, 0.0


def origin_block(b: Bars, ls: int, le: int, direction: int):
    """EA 의 OriginBlock() — 충격 구간의 마지막 반대색 캔들."""
    for k in range(le, max(ls, 0), -1):
        opposing = (b.C[k] < b.O[k]) if direction > 0 else (b.C[k] > b.O[k])
        if opposing:
            return b.H[k], b.L[k], k
    return None


def leg_fvg(b: Bars, ls: int, le: int, direction: int, min_gap: float):
    """EA 의 FindLegFvg()."""
    for k in range(le - 1, max(ls, 1), -1):
        if direction > 0 and b.L[k + 1] > b.H[k - 1] and (b.L[k + 1] - b.H[k - 1]) >= min_gap:
            return b.L[k + 1], b.H[k - 1]
        if direction < 0 and b.H[k + 1] < b.L[k - 1] and (b.L[k - 1] - b.H[k + 1]) >= min_gap:
            return b.L[k - 1], b.H[k + 1]
    return None


def day_levels(b: Bars, now: int, which: date):
    """EA 의 DayLevels() — 지금 봉에서 뒤로 걸어가며 그 날의 고·저."""
    hi = lo = None
    for k in range(now, -1, -1):
        if ny_date(b.T[k]) != which:
            if hi is not None:
                break
            continue
        if hi is None:
            hi, lo = b.H[k], b.L[k]
        else:
            hi = max(hi, b.H[k])
            lo = min(lo, b.L[k])
    return (hi, lo) if hi is not None else None


def asian_range(b: Bars, now: int):
    """EA 의 AsianRange() — 전날 뉴욕 20시 ~ 당일 02시."""
    today = ny_date(b.T[now])
    prev = today - timedelta(days=1)
    hi = lo = None
    for k in range(now, -1, -1):
        d = ny_date(b.T[k])
        if d < prev:
            break
        h = ny_clock(b.T[k])
        if not ((d == today and h < 2.0) or (d == prev and h >= 20.0)):
            continue
        if hi is None:
            hi, lo = b.H[k], b.L[k]
        else:
            hi = max(hi, b.H[k])
            lo = min(lo, b.L[k])
    if hi is None or hi <= lo:
        return None
    return hi, lo


def in_killzone_ea(ts: datetime) -> bool:
    """EA 의 InKillzone() 그대로."""
    h = ny_clock(ts)
    return (2.0 <= h < 5.0) or (7.0 <= h < 10.0) or (10.0 <= h < 11.0)


def market_context(b: Bars, atr: list[float], inp: EaInputs, now: int):
    """EA 의 MarketContext() 그대로. 0=통합 1=확장 2=전환 3=되돌림."""
    lo = max(0, now - inp.context_lookback)
    hi = max(b.H[lo:now + 1])
    low = min(b.L[lo:now + 1])
    a = atr[now]
    size = hi - low
    if a <= 0 or size <= 0 or size < a * 3.0:
        return 0, hi, low
    r0 = max(0, now - 2)
    if max(b.H[r0:now + 1]) >= hi - 1e-9 or min(b.L[r0:now + 1]) <= low + 1e-9:
        return 1, hi, low
    eq = (hi + low) / 2.0
    t0 = max(0, now - 10)
    took_high = any(b.H[k] >= hi - 1e-9 for k in range(t0, now + 1))
    took_low = any(b.L[k] <= low + 1e-9 for k in range(t0, now + 1))
    if took_high and b.C[now] < eq:
        return 2, hi, low
    if took_low and b.C[now] > eq:
        return 2, hi, low
    return 3, hi, low


def in_rollover(inp: EaInputs, ts: datetime) -> bool:
    if not inp.block_rollover:
        return False
    h = ny_clock(ts)
    return inp.rollover_start <= h < inp.rollover_end


# ----------------------------------------------------------------------
# 모델 — EA 코드 그대로 (없는 필터는 없는 채로)
# ----------------------------------------------------------------------
def _finish(b: Bars, atr: list[float], inp: EaInputs, gold: GoldProfile,
            now: int, s: EaSetup, rr: float, risk_pct: float,
            hold_min: int) -> EaSetup | None:
    """EA 의 Finish()."""
    risk = abs(s.entry - s.stop)
    if risk <= 0:
        return None
    sp = max(gold.spread_at(b.C[now]), bp_to_price(inp.spread_floor_bp, b.C[now]))
    if sp > inp.max_spread_price:
        return None
    if inp.max_spread_to_stop > 0 and sp > risk * inp.max_spread_to_stop:
        return None
    a = atr[now]
    if a <= 0:
        return None
    if abs(s.entry - b.C[now]) > a * inp.max_entry_dist_atr:
        return None
    if rr < inp.min_rr:
        return None
    s.target = s.entry + risk * rr if s.is_buy else s.entry - risk * rr
    s.risk_pct = risk_pct
    s.hold_min = hold_min
    return s


def first_break(b: Bars, r: int, lvl: float, above: bool, scan: int = 60) -> bool:
    """EA 의 FirstBreak() — 습격은 그 레벨이 처음 뚫린 그 봉이다."""
    for k in range(max(1, r - scan), r):
        if above and b.H[k] > lvl:
            return False
        if not above and b.L[k] < lvl:
            return False
    return True


def ea_turtle_soup(b: Bars, atr: list[float], inp: EaInputs, gold: GoldProfile,
                   now: int) -> EaSetup | None:
    """EA 의 TurtleSoup(). **킬존 검사가 없다** — 코드에 없으므로 여기도 없다."""
    if not inp.use_turtle:
        return None
    today = ny_date(b.T[now])
    pd_ = day_levels(b, now, today - timedelta(days=1))
    asia = asian_range(b, now)
    if pd_ is None and asia is None:
        return None
    a = atr[now]
    if a <= 0:
        return None
    buf = scaled(a, inp.stop_buffer_atr, inp.stop_buffer_bp, b.C[now])

    levels: list[tuple[float, int]] = []
    if pd_:
        levels += [(pd_[0], +1), (pd_[1], -1)]
    if asia:
        levels += [(asia[0], +1), (asia[1], -1)]

    for back in range(0, 11):
        r = now - back
        if r <= 1:
            break
        for lvl, kind in levels:
            if kind > 0:
                if b.H[r] > lvl and b.C[r] < lvl and b.C[now] < lvl \
                        and first_break(b, r, lvl, True):
                    s = EaSetup("TurtleSoup", False, True, lvl, b.H[r] + buf,
                                0.0, 0.0, 0, now, b.T[now],
                                f"false break above {lvl:.2f}")
                    if s.stop <= s.entry:
                        continue
                    out = _finish(b, atr, inp, gold, now, s, inp.ts_rr,
                                  inp.ts_risk, inp.ts_hold_min)
                    if out:
                        return out
            else:
                if b.L[r] < lvl and b.C[r] > lvl and b.C[now] > lvl \
                        and first_break(b, r, lvl, False):
                    s = EaSetup("TurtleSoup", True, True, lvl, b.L[r] - buf,
                                0.0, 0.0, 0, now, b.T[now],
                                f"false break below {lvl:.2f}")
                    if s.stop >= s.entry:
                        continue
                    out = _finish(b, atr, inp, gold, now, s, inp.ts_rr,
                                  inp.ts_risk, inp.ts_hold_min)
                    if out:
                        return out
    return None


def ea_unicorn(b: Bars, atr: list[float], inp: EaInputs, gold: GoldProfile,
               now: int) -> EaSetup | None:
    """EA 의 Unicorn(). 이것도 킬존 검사가 없다."""
    if not inp.use_unicorn:
        return None
    d, ls, le, lo, hi = find_mss(b, atr, inp, now, 60)
    if d == 0:
        return None
    a = atr[now]
    if a <= 0:
        return None
    min_gap = scaled(a, inp.min_fvg_atr, inp.min_fvg_bp, b.C[now])
    sp_gap = bp_to_price(inp.spread_floor_bp, b.C[now]) * inp.fvg_spread_mult
    min_gap = max(min_gap, sp_gap)

    f = leg_fvg(b, ls, le, d, min_gap)
    if f is None:
        return None
    ftop, fbot = f
    ob = origin_block(b, ls, le, d)
    if ob is None:
        return None
    btop, bbot, _ = ob
    broken = (b.C[le] > btop) if d > 0 else (b.C[le] < bbot)
    if not broken:
        return None
    top = min(btop, ftop)
    bot = max(bbot, fbot)
    if top <= bot:
        return None
    entry = (top + bot) / 2.0
    if d > 0 and entry > b.C[now]:
        return None
    if d < 0 and entry < b.C[now]:
        return None
    buf = scaled(a, inp.stop_buffer_atr, inp.stop_buffer_bp, b.C[now])
    s = EaSetup("Unicorn", d > 0, True, entry,
                bot - buf if d > 0 else top + buf, 0.0, 0.0, 0, now, b.T[now],
                "breaker overlaps FVG")
    if s.is_buy and s.stop >= s.entry:
        return None
    if not s.is_buy and s.stop <= s.entry:
        return None
    return _finish(b, atr, inp, gold, now, s, inp.uni_rr,
                   inp.uni_risk, inp.uni_hold_min)


# ----------------------------------------------------------------------
# OnTick 루프
# ----------------------------------------------------------------------
def collect(candles: Sequence[Candle], inp: EaInputs = EaInputs(),
            gold: GoldProfile = STANDARD, start: int = 300) -> list[EaSetup]:
    """EA 의 OnTick() 이 실제로 내는 주문 목록.

    한 봉에 한 번 평가하고, 평가 순서대로 첫 매치에서 멈춘다 (EA 와 동일).
    포지션이 있는 동안은 새 주문을 안 낸다 — 그 상태 추적은 호출부가 한다.
    """
    b = Bars(candles)
    atr = rolling_atr(b, inp.atr_period)
    out: list[EaSetup] = []
    last_fire: dict[str, int] = {}
    day = None
    day_trades = 0
    for now in range(start, len(b) - 1):
        ts = b.T[now]
        d = ny_date(ts)
        if d != day:
            day, day_trades = d, 0
        if in_rollover(inp, ts):
            continue
        if inp.block_friday_late and d.weekday() == 4 \
                and ny_clock(ts) >= inp.friday_cutoff_ny:
            continue
        if day_trades >= min(inp.max_trades_per_day, inp.hard_cap_per_day):
            continue
        if inp.require_killzone and not in_killzone_ea(ts):
            continue
        ctx, _hi, _lo = market_context(b, atr, inp, now)
        if inp.require_context and ctx not in (2, 3):
            continue

        s = None
        for name, fn in (("Unicorn", ea_unicorn), ("TurtleSoup", ea_turtle_soup)):
            if now - last_fire.get(name, -10 ** 9) < inp.cooldown_bars:
                continue
            s = fn(b, atr, inp, gold, now)
            if s is not None:
                last_fire[name] = now
                break
        if s is not None:
            out.append(s)
            day_trades += 1
    return out
