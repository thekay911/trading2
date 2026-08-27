"""유동성 — ICT 의 출발점.

ICT 에서 가격은 지지·저항으로 가는 게 아니라 **스톱이 쌓인 곳**으로 간다.

  BSL (Buy Side Liquidity)   고점 위. 숏의 손절과 롱의 돌파 매수가 있다.
  SSL (Sell Side Liquidity)  저점 아래. 롱의 손절과 숏의 돌파 매도가 있다.

가장 확실한 유동성은 사람들이 모두 보는 자리다.
  · 전일 고·저 (PDH / PDL)
  · 전주 고·저 (PWH / PWL)
  · 아시아 레인지 고·저
  · 세션 고·저
  · 동일 고점 / 동일 저점 (equal highs / lows)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal, Sequence

from crowcode.data import Candle, atr
from ict.structure import BEAR, BULL, Dir, Swing, swings
from ict.timeops import ASIAN_RANGE, ny_date, to_ny

Side = Literal["buy", "sell"]


@dataclass(frozen=True)
class Pool:
    """유동성 풀. `kind` 는 어느 쪽 스톱인지."""
    kind: Literal["BSL", "SSL"]
    price: float
    label: str                 # PDH, PDL, PWH, ASIA_HIGH, EQH ...
    index: int                 # 형성 시점
    strength: int = 1          # 겹친 개수 (동일 고점이면 늘어난다)
    taken_at: int | None = None

    @property
    def untapped(self) -> bool:
        return self.taken_at is None


@dataclass(frozen=True)
class Raid:
    """유동성 습격 — 풀을 뚫었다가 되돌아온 봉. ICT 의 'stop run'."""
    index: int
    ts: datetime
    direction: Dir             # BULL = SSL 습격(저점 사냥) → 매수 관점
    pool: Pool
    extreme: float             # 습격 봉의 극점 (손절 기준)
    closed_back: bool          # 종가가 풀 안쪽으로 돌아왔는가


# ----------------------------------------------------------------------
# 기준 레벨
# ----------------------------------------------------------------------
def daily_levels(candles: Sequence[Candle]) -> dict[date, tuple[float, float, int]]:
    """뉴욕 날짜별 (고, 저, 마지막 인덱스)."""
    out: dict[date, list] = {}
    for i, c in enumerate(candles):
        d = ny_date(c.ts)
        if d not in out:
            out[d] = [c.high, c.low, i]
        else:
            out[d][0] = max(out[d][0], c.high)
            out[d][1] = min(out[d][1], c.low)
            out[d][2] = i
    return {d: (v[0], v[1], v[2]) for d, v in out.items()}


def asian_range(candles: Sequence[Candle], day: date) -> tuple[float, float] | None:
    """해당 뉴욕 날짜의 아시아 레인지 고·저.

    아시아 창은 전날 20:00 에 시작해 자정을 넘으므로, 날짜 경계 처리에 주의한다.
    """
    hi = lo = None
    for c in candles:
        if not ASIAN_RANGE.contains(c.ts):
            continue
        n = to_ny(c.ts)
        # 20:00~23:59 는 '다음날' 의 아시아 레인지다
        owner = n.date() if n.hour < 12 else (n.date() + _one_day())
        if owner != day:
            continue
        hi = c.high if hi is None else max(hi, c.high)
        lo = c.low if lo is None else min(lo, c.low)
    return (hi, lo) if hi is not None else None


def _one_day():
    from datetime import timedelta
    return timedelta(days=1)


def reference_pools(candles: Sequence[Candle], upto: int) -> list[Pool]:
    """`upto` 시점에서 살아 있는 기준 유동성 (전일·전주 고저, 아시아 레인지)."""
    if upto <= 0:
        return []
    view = candles[:upto + 1]
    levels = daily_levels(view)
    days = sorted(levels)
    pools: list[Pool] = []
    if len(days) >= 2:
        prev = days[-2]
        hi, lo, idx = levels[prev]
        pools.append(Pool("BSL", hi, "PDH", idx))
        pools.append(Pool("SSL", lo, "PDL", idx))

    today = days[-1] if days else None
    if today is not None:
        ar = asian_range(view, today)
        if ar:
            hi, lo = ar
            pools.append(Pool("BSL", hi, "ASIA_HIGH", upto))
            pools.append(Pool("SSL", lo, "ASIA_LOW", upto))

    # 전주 고·저 — 뉴욕 날짜의 ISO 주 기준
    weeks: dict[tuple[int, int], list[float]] = {}
    for d in days:
        key = (d.isocalendar().year, d.isocalendar().week)
        hi, lo, _ = levels[d]
        if key not in weeks:
            weeks[key] = [hi, lo]
        else:
            weeks[key][0] = max(weeks[key][0], hi)
            weeks[key][1] = min(weeks[key][1], lo)
    wk = sorted(weeks)
    if len(wk) >= 2:
        hi, lo = weeks[wk[-2]]
        pools.append(Pool("BSL", hi, "PWH", upto))
        pools.append(Pool("SSL", lo, "PWL", upto))

    return _mark_taken(view, pools)


def _mark_taken(candles: Sequence[Candle], pools: Sequence[Pool]) -> list[Pool]:
    out = []
    for p in pools:
        taken = None
        for k in range(p.index + 1, len(candles)):
            c = candles[k]
            if (p.kind == "BSL" and c.high > p.price) or (p.kind == "SSL" and c.low < p.price):
                taken = k
                break
        out.append(Pool(p.kind, p.price, p.label, p.index, p.strength, taken))
    return out


def equal_levels(sw: Sequence[Swing], tol: float, min_count: int = 2) -> list[Pool]:
    """동일 고점 / 동일 저점 — 스톱이 가장 두텁게 쌓이는 자리."""
    out: list[Pool] = []
    for is_high, kind, label in ((True, "BSL", "EQH"), (False, "SSL", "EQL")):
        pts = sorted([s for s in sw if s.is_high == is_high], key=lambda s: s.price)
        cluster: list[Swing] = []
        for s in pts:
            if cluster and abs(s.price - cluster[0].price) <= tol:
                cluster.append(s)
                continue
            if len(cluster) >= min_count:
                out.append(_pool_from(cluster, kind, label))
            cluster = [s]
        if len(cluster) >= min_count:
            out.append(_pool_from(cluster, kind, label))
    return out


def _pool_from(cluster: Sequence[Swing], kind: str, label: str) -> Pool:
    price = sum(s.price for s in cluster) / len(cluster)
    return Pool(kind, price, label, max(s.index for s in cluster), len(cluster))  # type: ignore[arg-type]


# ----------------------------------------------------------------------
# 습격
# ----------------------------------------------------------------------
def find_raids(
    candles: Sequence[Candle],
    pools: Sequence[Pool],
    start: int = 0,
    require_close_back: bool = True,
) -> list[Raid]:
    """풀을 처음 뚫은 봉을 찾는다.

    **풀 하나당 한 번만** 기록한다. 스톱은 한 번 털리면 사라진다.
    이미 뚫린 레벨을 매 봉 '습격' 으로 세면, 가격이 그 아래 있는 동안
    모든 봉이 습격이 되어 판정이 무의미해진다.
    """
    out: list[Raid] = []
    spent: set[int] = set()
    for i in range(max(1, start), len(candles)):
        c = candles[i]
        for pi, p in enumerate(pools):
            if pi in spent or i <= p.index:
                continue
            if p.kind == "SSL" and c.low < p.price:
                spent.add(pi)
                back = c.close > p.price
                if back or not require_close_back:
                    out.append(Raid(i, c.ts, BULL, p, c.low, back))
            elif p.kind == "BSL" and c.high > p.price:
                spent.add(pi)
                back = c.close < p.price
                if back or not require_close_back:
                    out.append(Raid(i, c.ts, BEAR, p, c.high, back))
    return out


def last_raid(raids: Sequence[Raid], direction: Dir, now: int, within: int) -> Raid | None:
    """`now` 기준 `within` 봉 안의, 해당 방향 최신 습격."""
    best = None
    for r in raids:
        if r.index > now or now - r.index > within:
            continue
        if r.direction != direction:
            continue
        if best is None or r.index > best.index:
            best = r
    return best


def draw_targets(pools: Sequence[Pool], side: Side, price: float) -> list[Pool]:
    """진입 방향에서 노릴 만한 미회수 유동성 (가까운 순)."""
    if side == "buy":
        cand = [p for p in pools if p.kind == "BSL" and p.untapped and p.price > price]
        return sorted(cand, key=lambda p: p.price)
    cand = [p for p in pools if p.kind == "SSL" and p.untapped and p.price < price]
    return sorted(cand, key=lambda p: -p.price)
