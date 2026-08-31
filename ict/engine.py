"""사전 계산 엔진.

원래 구조는 매 봉마다 `analyze(최근 600봉)` 을 다시 돌렸다. 봉이 2만 개면
2만 번, 60만 개면 60만 번이다 — 2018~현재를 훑는 건 불가능하다.

구조·스윙·FVG·일별 레벨은 전부 **인과적**이라 전체 시계열에서 한 번만
계산해도 결과가 같다. 오히려 더 정확하다 — 창을 자르면 그 앞의 구조 상태를
잃기 때문이다.

  기존:  O(n × 창크기)
  지금:  O(n)  — 한 번 계산하고 인덱스로 조회
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Sequence

from crowcode.data import Candle, atr
from ict import liquidity as liq
from ict import pdarrays as pda
from ict.gold import GoldProfile, STANDARD, Volatility
from ict.structure import BEAR, BULL, Dir, StructureEvent, StructureState, Swing, analyze, swings
from ict.timeops import ny_date


@dataclass
class Market:
    """전체 시계열을 한 번 훑어 만든 조회용 구조체."""
    candles: list[Candle]
    gold: GoldProfile = STANDARD
    structure: StructureState = None            # type: ignore[assignment]
    swings: list[Swing] = field(default_factory=list)
    fvgs: list[pda.PDArray] = field(default_factory=list)
    daily: dict[date, tuple[float, float, int]] = field(default_factory=dict)
    day_order: list[date] = field(default_factory=list)
    _atr: list[float] = field(default_factory=list)
    _mss_index: list[int] = field(default_factory=list)
    _fvg_index: list[int] = field(default_factory=list)
    _day_bars: dict[date, tuple[int, int]] = field(default_factory=dict)   # (첫, 마지막) 인덱스
    _day_of_bar: list[date] = field(default_factory=list)
    _pool_cache: dict[date, list[liq.Pool]] = field(default_factory=dict)
    _fvg_touch: list[int] = field(default_factory=list)
    _asia_cache: dict[date, tuple[float, float] | None] = field(default_factory=dict)
    _fvg_broken: list[int] = field(default_factory=list)      # FVG 가 종가로 뚫린 봉
    _swing_confirmed: list[int] = field(default_factory=list)   # 확정 시각 오름차순
    _inv_at: dict[int, list[int]] = field(default_factory=dict)  # 뚫린 봉 -> FVG 색인
    _ext_low: list[Swing | None] = field(default_factory=list)   # 봉별 최저 스윙 저점
    _ext_high: list[Swing | None] = field(default_factory=list)  # 봉별 최고 스윙 고점

    @classmethod
    def build(cls, candles: Sequence[Candle], gold: GoldProfile = STANDARD,
              min_displacement_atr: float | None = None,
              swing_left: int = 1, swing_right: int = 1) -> "Market":
        bars = list(candles)
        m = cls(bars, gold)

        # ATR 을 봉마다 미리 (롤링) — 임계값이 전부 여기 걸린다
        m._atr = _rolling_atr(bars, 20)

        disp = min_displacement_atr if min_displacement_atr is not None \
            else gold.displacement_atr
        m.structure = analyze(bars, swing_left, swing_right,
                              require_displacement=True, min_displacement_atr=disp)
        m.swings = swings(bars, swing_left, swing_right)

        # FVG 는 봉마다 최소 크기가 달라지므로 그 시점 기준으로 판정한다
        m.fvgs = []
        for i in range(2, len(bars)):
            a, c = bars[i - 2], bars[i]
            v = Volatility(c.close, m._atr[i], 0.0)
            floor = max(m._atr[i] * gold.min_fvg_atr,
                        c.close * gold.min_fvg_bp / 10_000.0,
                        gold.spread * gold.fvg_spread_multiple)
            if c.low - a.high > floor:
                m.fvgs.append(pda.PDArray("FVG", BULL, c.low, a.high, i))
            elif a.low - c.high > floor:
                m.fvgs.append(pda.PDArray("FVG", BEAR, a.low, c.high, i))

        # 각 FVG 의 CE(50%) 가 처음 닿는 봉을 미리 찾아 둔다.
        # 이걸 안 하면 조회할 때마다 그 FVG 이후를 전부 훑게 된다.
        m._fvg_touch = _first_touch(bars, m.fvgs)
        m._fvg_broken = _first_break(bars, m.fvgs)
        # 봉마다 전체 스윙/갭을 훑으면 O(n^2) 다. 494,000봉에서 스캔이
        # 끝나지 않았다. 확정 시각과 '뚫린 봉' 으로 색인해 둔다.
        m.swings.sort(key=lambda x: x.confirmed_at)
        m._swing_confirmed = [x.confirmed_at for x in m.swings]
        for j, b in enumerate(m._fvg_broken):
            if b < 10 ** 9:
                m._inv_at.setdefault(b, []).append(j)
        m._ext_low = _window_extreme(m.swings, len(bars), 120, low=True)
        m._ext_high = _window_extreme(m.swings, len(bars), 120, low=False)

        m.daily = liq.daily_levels(bars)
        m.day_order = sorted(m.daily)

        # 봉 → 날짜, 날짜 → 봉 구간. 이게 있어야 하루 단위로 캐시할 수 있다.
        m._day_of_bar = [ny_date(c.ts) for c in bars]
        for i, d in enumerate(m._day_of_bar):
            if d not in m._day_bars:
                m._day_bars[d] = (i, i)
            else:
                m._day_bars[d] = (m._day_bars[d][0], i)
        m._asia_cache = _asian_ranges(bars, m._day_of_bar)
        m._mss_index = [e.index for e in m.structure.events]
        m._fvg_index = [g.index for g in m.fvgs]
        return m

    # ------------------------------------------------------------------
    def __len__(self) -> int:
        return len(self.candles)

    def atr_at(self, i: int) -> float:
        return self._atr[i] if 0 <= i < len(self._atr) else 0.0

    def volatility(self, i: int) -> Volatility:
        c = self.candles[i]
        a = self.atr_at(i)
        return Volatility(c.close, a, (a / c.close * 10_000.0) if c.close else 0.0)

    def last_mss(self, now: int, within: int, direction: Dir | None = None
                 ) -> StructureEvent | None:
        """`now` 이전의 마지막 유효 MSS (변위 동반)."""
        j = bisect.bisect_right(self._mss_index, now) - 1
        while j >= 0:
            e = self.structure.events[j]
            if now - e.index > within:
                return None
            if e.kind == "MSS" and e.displacement is not None:
                if direction is None or e.direction == direction:
                    return e
                return None
            j -= 1
        return None

    def fresh_fvgs(self, now: int, direction: Dir, since: int,
                   limit: int = 6) -> list[pda.PDArray]:
        """`since`~`now` 사이에 생겼고 아직 CE 를 안 건드린 FVG."""
        lo = bisect.bisect_left(self._fvg_index, since)
        hi = bisect.bisect_right(self._fvg_index, now)
        out = []
        for j in range(hi - 1, lo - 1, -1):
            g = self.fvgs[j]
            if g.direction != direction:
                continue
            if self._touched_at(j) <= now:          # 이미 CE 를 건드렸다
                continue
            out.append(g)
            if len(out) >= limit:
                break
        return out

    def extreme_swing(self, now: int, low: bool) -> Swing | None:
        """최근 120봉 안에 만들어졌고 `now` 까지 확정된 스윙의 극점.

        봉마다 스윙 전체를 훑으면 O(n^2) 다. 프로파일러로 보니 그 한 줄이
        failed_break 실행시간의 90% 였고, 494,000봉에서는 끝나지 않았다.
        `build` 가 단조 덱으로 미리 계산해 둔다.
        """
        arr = self._ext_low if low else self._ext_high
        return arr[now] if 0 <= now < len(arr) else None

    def inverted(self, now: int, direction: Dir, within: int = 12,
                 since: int = 0) -> list[pda.PDArray]:
        """`now` 기준 최근에 뚫려서 `direction` 편이 된 FVG 들.

        원래 방향이 반대였던 갭이 종가로 뚫리면 역할이 뒤집힌다.
        """
        out: list[pda.PDArray] = []
        lo = max(since, now - within)
        for b in range(now, lo - 1, -1):
            for j in self._inv_at.get(b, ()):
                g = self.fvgs[j]
                if g.direction == direction:
                    continue              # 이미 이쪽 편이면 반전이 아니다
                out.append(pda.PDArray("IFVG", -g.direction, g.top, g.bottom,
                                       b, g.index))
            if out:
                break                     # 가장 최근에 뚫린 것만 쓴다
        return out

    def _touched_at(self, j: int) -> int:
        return self._fvg_touch[j] if j < len(self._fvg_touch) else 10 ** 9

    def pools(self, now: int) -> list[liq.Pool]:
        """그날의 기준 유동성. 날짜 단위로 캐시하고 `taken_at` 만 그날 안에서 찾는다."""
        d = self._day_of_bar[now]
        base = self._pool_cache.get(d)
        if base is None:
            base = self._day_pools(d)
            self._pool_cache[d] = base
        # taken_at 은 이미 하루 단위로 계산돼 있다. 지금 시점 기준으로만 가린다.
        return [p if (p.taken_at is not None and p.taken_at <= now)
                else liq.Pool(p.kind, p.price, p.label, p.index, p.strength, None)
                for p in base]

    def _day_pools(self, d: date) -> list[liq.Pool]:
        k = bisect.bisect_left(self.day_order, d)
        out: list[liq.Pool] = []
        if k >= 1:
            prev = self.day_order[k - 1]
            hi, lo, idx = self.daily[prev]
            out.append(liq.Pool("BSL", hi, "PDH", idx))
            out.append(liq.Pool("SSL", lo, "PDL", idx))
        # 전주 고·저 — 지금 주와 다른 마지막 ISO 주
        wk = d.isocalendar()[:2]
        prev_wk = None
        for dd in reversed(self.day_order[:k]):
            if dd.isocalendar()[:2] != wk:
                prev_wk = dd.isocalendar()[:2]
                break
        if prev_wk is not None:
            hs = [self.daily[x][0] for x in self.day_order[:k]
                  if x.isocalendar()[:2] == prev_wk]
            ls = [self.daily[x][1] for x in self.day_order[:k]
                  if x.isocalendar()[:2] == prev_wk]
            if hs:
                idx = self._day_bars[d][0]
                out.append(liq.Pool("BSL", max(hs), "PWH", idx))
                out.append(liq.Pool("SSL", min(ls), "PWL", idx))
        ar = self._asia_cache.get(d)
        if ar:
            hi, lo = ar
            idx = self._day_bars[d][0]
            out.append(liq.Pool("BSL", hi, "ASIA_HIGH", idx))
            out.append(liq.Pool("SSL", lo, "ASIA_LOW", idx))
        # 그날 안에서 각 풀이 처음 뚫리는 봉을 한 번만 찾는다
        first, last = self._day_bars[d]
        return [self._resolve(p, first, last) for p in out]

    def _resolve(self, p: liq.Pool, first: int, last: int) -> liq.Pool:
        for k in range(max(p.index + 1, first), last + 1):
            c = self.candles[k]
            if (p.kind == "BSL" and c.high > p.price) or (p.kind == "SSL" and c.low < p.price):
                return liq.Pool(p.kind, p.price, p.label, p.index, p.strength, k)
        return p


def _window_extreme(sw: Sequence[Swing], n: int, lookback: int,
                    low: bool) -> list[Swing | None]:
    """봉마다 '최근 lookback 봉 안에 만들어졌고 이미 확정된' 스윙의 극점.

    단조 덱으로 한 번에 만든다. 창에서 빠지는 것만 앞에서 버리고,
    새로 들어오는 것보다 못한 것은 뒤에서 버린다.
    """
    out: list[Swing | None] = [None] * n
    want = [x for x in sw if (not x.is_high) == low]
    want.sort(key=lambda x: x.confirmed_at)
    dq: list[Swing] = []
    k = 0
    for i in range(n):
        while k < len(want) and want[k].confirmed_at <= i:
            x = want[k]
            k += 1
            while dq and ((dq[-1].price >= x.price) if low else (dq[-1].price <= x.price)):
                dq.pop()
            dq.append(x)
        while dq and dq[0].index < i - lookback:
            dq.pop(0)
        out[i] = dq[0] if dq else None
    return out


def _first_break(bars, gaps) -> list[int]:
    """각 FVG 가 **종가로** 반대편을 뚫은 첫 봉. 없으면 매우 큰 값.

    뚫린 FVG 는 반대 역할이 된다(iFVG). 이걸 모델 안에서 매 봉 다시
    계산하면 O(n^2) 가 된다 — 실제로 494,000봉에서 12분을 넘겼다.
    갭은 인덱스 오름차순이고 아직 안 뚫린 것만 추적하면 한 번에 끝난다.
    """
    BIG = 10 ** 9
    out = [BIG] * len(gaps)
    live: list[int] = []
    gi = 0
    for i in range(len(bars)):
        while gi < len(gaps) and gaps[gi].index <= i:
            live.append(gi)
            gi += 1
        if not live:
            continue
        c = bars[i]
        keep = []
        for j in live:
            g = gaps[j]
            if g.index >= i:
                keep.append(j)
                continue
            broke = (c.close < g.bottom) if g.direction == BULL else (c.close > g.top)
            if broke:
                out[j] = i
            else:
                keep.append(j)
        live = keep
    return out


def _first_touch(bars, gaps) -> list[int]:
    """각 FVG 의 CE 가 처음 닿는 봉 인덱스. 없으면 매우 큰 값.

    갭은 인덱스 오름차순이고 아직 안 닿은 것만 추적하면 되므로,
    전체를 한 번만 훑으면 된다.
    """
    n = len(bars)
    out = [10 ** 9] * len(gaps)
    pending: list[int] = []
    gi = 0
    for i in range(n):
        while gi < len(gaps) and gaps[gi].index <= i:
            pending.append(gi)
            gi += 1
        if not pending:
            continue
        c = bars[i]
        still = []
        for j in pending:
            g = gaps[j]
            if g.index >= i:
                still.append(j)
                continue
            hit = (c.low <= g.mid) if g.direction == BULL else (c.high >= g.mid)
            if hit:
                out[j] = i
            else:
                still.append(j)
        pending = still
    return out


def _asian_ranges(bars, day_of_bar) -> dict:
    """아시아 레인지를 전체에서 한 번만 계산한다 (봉당 O(1))."""
    from ict.timeops import ASIAN_RANGE, to_ny
    from datetime import timedelta
    acc: dict = {}
    for i, c in enumerate(bars):
        if not ASIAN_RANGE.contains(c.ts):
            continue
        n = to_ny(c.ts)
        owner = n.date() if n.hour < 12 else (n.date() + timedelta(days=1))
        if owner not in acc:
            acc[owner] = [c.high, c.low]
        else:
            acc[owner][0] = max(acc[owner][0], c.high)
            acc[owner][1] = min(acc[owner][1], c.low)
    return {d: (v[0], v[1]) for d, v in acc.items()}


def _mark(candles: Sequence[Candle], pools: Sequence[liq.Pool], now: int) -> list[liq.Pool]:
    out = []
    for p in pools:
        taken = None
        for k in range(p.index + 1, now + 1):
            c = candles[k]
            if (p.kind == "BSL" and c.high > p.price) or (p.kind == "SSL" and c.low < p.price):
                taken = k
                break
        out.append(liq.Pool(p.kind, p.price, p.label, p.index, p.strength, taken))
    return out


def _rolling_atr(candles: Sequence[Candle], period: int = 20) -> list[float]:
    """Wilder ATR 을 봉마다. O(n)."""
    n = len(candles)
    out = [0.0] * n
    if n < 2:
        return out
    prev = candles[0].close
    trs = []
    val = 0.0
    for i in range(1, n):
        c = candles[i]
        tr = max(c.high - c.low, abs(c.high - prev), abs(c.low - prev))
        prev = c.close
        if i <= period:
            trs.append(tr)
            val = sum(trs) / len(trs)
        else:
            val = (val * (period - 1) + tr) / period
        out[i] = val
    out[0] = out[1] if n > 1 else 0.0
    return out
