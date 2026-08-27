"""ICT 개념 실측 — "차트를 다 본다" 를 통계로 한다.

눈으로 몇 년치 차트를 넘기는 대신, ICT 가 주장하는 것들을 하나씩
데이터에 물어본다. 답이 안 나오는 개념은 코드에 넣지 않는 게 맞다.

측정 항목
  1. 킬존별 당일 고·저 형성 빈도   — "고점/저점은 킬존에서 만들어진다"
  2. 전일 고·저 습격 빈도          — "가격은 PDH/PDL 을 가지러 간다"
  3. 유다 스윙                     — 세션 오픈의 가짜 움직임
  4. FVG 메움 비율                 — "불균형은 되돌아와 채운다"
  5. Power of 3                    — 일봉의 축적·조작·분배 구조
  6. OTE 도달 비율                 — 변위 뒤 62~79% 되돌림
  7. MSS 이후 추종                 — 변위 MSS 뒤 반대편 유동성까지 가는가
  8. 요일별 프로필
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Sequence

from crowcode.data import Candle, Series, atr
from ict.pdarrays import fair_value_gaps, is_filled
from ict.ranges import OTE_END, OTE_START, leg_range
from ict.structure import BEAR, BULL, analyze, swings
from ict.timeops import (
    ASIAN_RANGE, KILLZONES, LONDON_KZ, NY_AM_KZ, SILVER_BULLET_AM,
    ny_clock, ny_date, session_of, to_ny,
)

WEEKDAYS = ("월", "화", "수", "목", "금", "토", "일")


def _pct(a: int, b: int) -> str:
    return f"{a / b:.1%}" if b else "-"


@dataclass
class Section:
    title: str
    rows: list[tuple[str, str]] = field(default_factory=list)
    note: str = ""

    def render(self, width: int = 66) -> str:
        out = ["-" * width, f" {self.title}"]
        for label, value in self.rows:
            out.append(f"   {label:<32} {value}")
        if self.note:
            out.append(f"   → {self.note}")
        return "\n".join(out)


# ----------------------------------------------------------------------
def group_by_day(candles: Sequence[Candle]) -> dict[date, list[Candle]]:
    days: dict[date, list[Candle]] = defaultdict(list)
    for c in candles:
        days[ny_date(c.ts)].append(c)
    return dict(days)


def killzone_extremes(days: dict[date, list[Candle]]) -> Section:
    """당일 고점·저점이 어느 킬존에서 만들어졌는가."""
    hi_count: Counter = Counter()
    lo_count: Counter = Counter()
    total = 0
    for day, bars in days.items():
        if len(bars) < 50:
            continue
        total += 1
        hb = max(bars, key=lambda c: c.high)
        lb = min(bars, key=lambda c: c.low)
        hi_count[_zone_name(hb)] += 1
        lo_count[_zone_name(lb)] += 1

    s = Section(f"1. 당일 고·저가 만들어진 시간대  (표본 {total}일)")
    for name in sorted(set(hi_count) | set(lo_count),
                       key=lambda k: -(hi_count[k] + lo_count[k])):
        s.rows.append((name, f"고점 {_pct(hi_count[name], total):>6}   "
                             f"저점 {_pct(lo_count[name], total):>6}"))
    kz = sum(v for k, v in hi_count.items() if k not in ("기타", "아시아")) + \
         sum(v for k, v in lo_count.items() if k not in ("기타", "아시아"))
    s.note = (f"킬존 안에서 만들어진 고·저 비율 {_pct(kz, total * 2)} — "
              "이 값이 50% 를 크게 넘지 않으면 킬존 필터는 의미가 약하다")
    return s


def _zone_name(c: Candle) -> str:
    h = ny_clock(c.ts)
    for w in (LONDON_KZ, NY_AM_KZ, SILVER_BULLET_AM):
        if w.contains(c.ts):
            return w.name
    if ASIAN_RANGE.contains(c.ts):
        return "아시아"
    if 12.0 <= h < 17.0:
        return "뉴욕오후"
    return "기타"


def pd_level_raids(days: dict[date, list[Candle]]) -> Section:
    """전일 고·저를 얼마나 자주 가지러 가는가."""
    order = sorted(days)
    took_h = took_l = took_both = neither = 0
    total = 0
    for prev, cur in zip(order, order[1:]):
        pb, cb = days[prev], days[cur]
        if len(pb) < 50 or len(cb) < 50:
            continue
        total += 1
        pdh = max(c.high for c in pb)
        pdl = min(c.low for c in pb)
        h = max(c.high for c in cb) > pdh
        l = min(c.low for c in cb) < pdl
        took_h += h
        took_l += l
        if h and l:
            took_both += 1
        if not h and not l:
            neither += 1

    s = Section(f"2. 전일 고·저 습격  (표본 {total}일)")
    s.rows += [
        ("전일 고점(PDH) 돌파", _pct(took_h, total)),
        ("전일 저점(PDL) 이탈", _pct(took_l, total)),
        ("양쪽 다 건드림", _pct(took_both, total)),
        ("어느 쪽도 안 건드림", _pct(neither, total)),
    ]
    s.note = ("'가격은 전일 고·저를 가지러 간다' 가 맞다면 "
              "'어느 쪽도 안 건드림' 이 낮아야 한다")
    return s


def judas_swing(days: dict[date, list[Candle]], open_hour: float = 2.0,
                window_h: float = 3.0) -> Section:
    """런던 오픈의 가짜 움직임 — 먼저 한쪽을 털고 반대로 간다."""
    hits = 0
    total = 0
    sizes: list[float] = []
    for day, bars in days.items():
        pre = [c for c in bars if ny_clock(c.ts) < open_hour]
        win = [c for c in bars if open_hour <= ny_clock(c.ts) < open_hour + window_h]
        rest = [c for c in bars if ny_clock(c.ts) >= open_hour + window_h]
        if len(pre) < 10 or len(win) < 5 or len(rest) < 10:
            continue
        total += 1
        ah, al = max(c.high for c in pre), min(c.low for c in pre)
        wh, wl = max(c.high for c in win), min(c.low for c in win)
        rh, rl = max(c.high for c in rest), min(c.low for c in rest)
        # 아시아 고점을 털고 → 이후 아시아 저점 아래로
        if wh > ah and rl < al:
            hits += 1
            sizes.append(wh - ah)
        elif wl < al and rh > ah:
            hits += 1
            sizes.append(al - wl)

    s = Section(f"3. 유다 스윙 (런던 오픈)  (표본 {total}일)")
    s.rows.append(("아시아 레인지 한쪽 털고 반대로", _pct(hits, total)))
    if sizes:
        sizes.sort()
        s.rows.append(("가짜 움직임 크기 (중앙값)", f"${sizes[len(sizes)//2]:.2f}"))
    s.note = "이 비율이 높으면 런던 오픈 반대매매(터틀수프)가 성립한다"
    return s


def fvg_fill_rates(candles: Sequence[Candle], horizon: int = 288) -> Section:
    """FVG 는 정말 되돌아와 채워지는가."""
    candles = list(candles)
    a = atr(candles[-2000:] if len(candles) > 2000 else candles)
    gaps = fair_value_gaps(candles, min_size=a * 0.15)
    ce = full = 0
    counted = 0
    for g in gaps:
        if g.index + horizon >= len(candles):
            continue
        counted += 1
        upto = g.index + horizon
        if is_filled(candles, g, upto, full=False):
            ce += 1
        if is_filled(candles, g, upto, full=True):
            full += 1

    s = Section(f"4. FVG 메움 비율  (표본 {counted}개, {horizon}봉 이내)")
    s.rows += [
        ("50%(CE) 까지 되돌아옴", _pct(ce, counted)),
        ("완전히 메움", _pct(full, counted)),
    ]
    s.note = "CE 비율이 높으면 FVG 중앙 진입(기본값)이 타당하다"
    return s


def power_of_three(days: dict[date, list[Candle]]) -> Section:
    """일봉의 시가 위치 — 축적·조작·분배 구조가 실제로 있는가."""
    up_days = down_days = 0
    open_near_low = open_near_high = 0
    total = 0
    for day, bars in days.items():
        if len(bars) < 50:
            continue
        total += 1
        o, cl = bars[0].open, bars[-1].close
        hi = max(c.high for c in bars)
        lo = min(c.low for c in bars)
        rng = hi - lo
        if rng <= 0:
            continue
        pos = (o - lo) / rng
        if cl > o:
            up_days += 1
            if pos < 0.35:
                open_near_low += 1
        else:
            down_days += 1
            if pos > 0.65:
                open_near_high += 1

    s = Section(f"5. Power of 3 (일봉 구조)  (표본 {total}일)")
    s.rows += [
        ("상승 마감일", _pct(up_days, total)),
        ("  그중 시가가 저점 부근(<35%)", _pct(open_near_low, up_days)),
        ("하락 마감일", _pct(down_days, total)),
        ("  그중 시가가 고점 부근(>65%)", _pct(open_near_high, down_days)),
    ]
    s.note = ("상승일에 시가가 저점 근처라는 건 '먼저 아래를 털고 올린다' 는 뜻이다. "
              "이 비율이 높을수록 유다 스윙 전제가 강해진다")
    return s


def ote_reach(candles: Sequence[Candle], min_disp_atr: float = 1.5,
              horizon: int = 96) -> Section:
    """변위 뒤에 정말 OTE(62~79%)까지 되돌아오는가."""
    st = analyze(candles, require_displacement=True, min_displacement_atr=min_disp_atr)
    reached = deep = counted = 0
    for e in st.events:
        d = e.displacement
        if d is None or e.index + horizon >= len(candles):
            continue
        dr = leg_range(candles, d.start, d.end)
        if dr is None:
            continue
        counted += 1
        side = "buy" if e.direction == BULL else "sell"
        lo, hi = dr.ote(side)
        window = candles[e.index + 1:e.index + 1 + horizon]
        touched = any(c.low <= hi and c.high >= lo for c in window)
        if touched:
            reached += 1
        beyond = dr.retracement(0.90, side)
        if any((c.low <= beyond) if side == "buy" else (c.high >= beyond) for c in window):
            deep += 1

    s = Section(f"6. 변위 뒤 OTE 도달  (표본 {counted}건, {horizon}봉 이내)")
    s.rows += [
        (f"OTE({OTE_START:.0%}~{OTE_END:.0%}) 도달", _pct(reached, counted)),
        ("90% 이상 깊게 되돌림 (셋업 실패)", _pct(deep, counted)),
    ]
    s.note = "도달률이 낮으면 OTE 지정가는 체결이 안 된다는 뜻이다"
    return s


def mss_followthrough(candles: Sequence[Candle], min_disp_atr: float = 1.0,
                      horizon: int = 288) -> Section:
    """변위를 동반한 MSS 뒤에 실제로 얼마나 가는가 (R 단위)."""
    st = analyze(candles, require_displacement=True, min_displacement_atr=min_disp_atr)
    rows: list[float] = []
    for e in st.events:
        if e.kind != "MSS" or e.displacement is None or e.index + horizon >= len(candles):
            continue
        d = e.displacement
        risk = abs(d.gap_high - d.gap_low) if d.has_gap else 0.0
        dr = leg_range(candles, d.start, d.end)
        if dr is None or dr.size <= 0:
            continue
        risk = dr.size * 0.35                    # 변위 레그의 35% 를 1R 로 가정
        if risk <= 0:
            continue
        entry = candles[e.index].close
        window = candles[e.index + 1:e.index + 1 + horizon]
        if e.direction == BULL:
            best = max(c.high for c in window) - entry
        else:
            best = entry - min(c.low for c in window)
        rows.append(best / risk)

    rows.sort()
    s = Section(f"7. 변위 MSS 이후 최대 도달  (표본 {len(rows)}건)")
    if rows:
        s.rows += [
            ("중앙값", f"{rows[len(rows)//2]:+.2f}R"),
            ("1R 이상 도달", _pct(sum(1 for r in rows if r >= 1), len(rows))),
            ("2R 이상 도달", _pct(sum(1 for r in rows if r >= 2), len(rows))),
            ("3R 이상 도달", _pct(sum(1 for r in rows if r >= 3), len(rows))),
        ]
    s.note = "3R 도달률이 낮으면 1:3 목표는 시장이 주지 않는 것이다"
    return s


def weekday_profile(days: dict[date, list[Candle]]) -> Section:
    rng: dict[int, list[float]] = defaultdict(list)
    for day, bars in days.items():
        if len(bars) < 50:
            continue
        rng[day.weekday()].append(max(c.high for c in bars) - min(c.low for c in bars))
    s = Section("8. 요일별 변동폭")
    for wd in sorted(rng):
        v = sorted(rng[wd])
        s.rows.append((WEEKDAYS[wd], f"중앙값 ${v[len(v)//2]:.2f}   ({len(v)}일)"))
    s.note = "변동폭이 작은 요일은 손절 폭 대비 목표가 안 나온다"
    return s


# ----------------------------------------------------------------------
def full_report(series: Series, title: str = "XAUUSD ICT 실측") -> str:
    candles = list(series)
    days = group_by_day(candles)
    span = f"{candles[0].ts:%Y-%m-%d} ~ {candles[-1].ts:%Y-%m-%d}"
    head = [
        "=" * 66,
        f" {title}",
        f" {span}   {len(candles):,}봉   {len(days):,}일",
        "=" * 66,
    ]
    parts = [
        killzone_extremes(days),
        pd_level_raids(days),
        judas_swing(days),
        fvg_fill_rates(candles),
        power_of_three(days),
        ote_reach(candles),
        mss_followthrough(candles),
        weekday_profile(days),
    ]
    body = [p.render() for p in parts]
    tail = [
        "=" * 66,
        " 읽는 법: 비율이 우연 수준(50% 근처)이면 그 개념은 이 시장에서",
        " 통계적 근거가 없다는 뜻이다. 코드에 넣기 전에 여기서 걸러야 한다.",
        "=" * 66,
    ]
    return "\n".join(head + body + tail)
