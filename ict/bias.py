"""일일 편향과 DOL (Draw On Liquidity).

ICT 에서 "방향" 은 추세선이나 이동평균이 아니라 **가격이 끌려갈 유동성**이다.
오늘 알고리즘이 어디를 가지러 갈 것인가를 정하고, 그 반대편에서 진입한다.

판단 재료 (ICT 가 실제로 쓰는 것들)
  1. 뉴욕 자정 오픈 대비 현재 위치 — 위면 프리미엄, 아래면 디스카운트
  2. 미회수 유동성이 위·아래 중 어디에 더 가까이 있는가
  3. 주간 레인지 안에서의 위치
  4. 채워지지 않은 FVG 가 어느 쪽에 있는가 (자석 역할)
  5. 전일 종가가 전일 레인지의 어디에서 끝났는가
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal, Sequence

from crowcode.data import Candle
from ict.gold import GoldProfile, STANDARD, Volatility
from ict.liquidity import Pool, daily_levels, reference_pools
from ict.pdarrays import fair_value_gaps, is_filled
from ict.structure import BEAR, BULL, Dir
from ict.timeops import ny_date, to_ny

Lean = Literal["bullish", "bearish", "neutral"]


@dataclass
class Bias:
    lean: Lean
    direction: Dir | None
    midnight_open: float | None
    price: float
    target: Pool | None                 # DOL — 가격이 끌려갈 곳
    reasons: list[str] = field(default_factory=list)
    score: float = 0.0

    @property
    def side(self) -> str | None:
        return {BULL: "buy", BEAR: "sell"}.get(self.direction) if self.direction else None

    def describe(self) -> str:
        arrow = {"bullish": "▲ 매수 편향", "bearish": "▼ 매도 편향",
                 "neutral": "— 중립"}[self.lean]
        lines = [f"{arrow}   현재 {self.price:.2f}   점수 {self.score:+.1f}"]
        if self.midnight_open is not None:
            rel = "위" if self.price > self.midnight_open else "아래"
            lines.append(f"  자정 오픈 {self.midnight_open:.2f} 대비 {rel} "
                         f"({'프리미엄' if self.price > self.midnight_open else '디스카운트'})")
        if self.target:
            lines.append(f"  DOL → {self.target.label} {self.target.price:.2f} "
                         f"({abs(self.target.price - self.price):.2f} 떨어짐)")
        lines += [f"  · {r}" for r in self.reasons]
        return "\n".join(lines)


def midnight_open(candles: Sequence[Candle], day: date) -> float | None:
    """뉴욕 자정(00:00) 시가. ICT 의 프리미엄/디스카운트 기준선."""
    best = None
    for c in candles:
        n = to_ny(c.ts)
        if n.date() != day:
            continue
        if best is None or n < best[0]:
            best = (n, c.open)
    return best[1] if best else None


def evaluate(candles: Sequence[Candle], now: int,
             gold: GoldProfile = STANDARD) -> Bias:
    """`now` 시점의 일일 편향. 점수가 양수면 매수, 음수면 매도 쪽이다."""
    bars = list(candles)[:now + 1]
    if len(bars) < 60:
        return Bias("neutral", None, None, bars[-1].close if bars else 0.0, None)

    price = bars[-1].close
    today = ny_date(bars[-1].ts)
    mo = midnight_open(bars, today)
    v = Volatility.measure(bars)
    b = Bias("neutral", None, mo, price, None)
    score = 0.0

    # --- 1) 자정 오픈 -------------------------------------------------
    if mo is not None:
        if price > mo:
            score -= 1.0                     # 프리미엄 → 매도 쪽으로 기운다
            b.reasons.append(f"자정 오픈 위 (프리미엄) → 위쪽 유동성 회수 가능성")
        elif price < mo:
            score += 1.0
            b.reasons.append(f"자정 오픈 아래 (디스카운트) → 아래쪽 회수 가능성")

    # --- 2) 미회수 유동성이 어느 쪽에 가까운가 -------------------------
    pools = reference_pools(bars, len(bars) - 1)
    above = [p for p in pools if p.kind == "BSL" and p.untapped and p.price > price]
    below = [p for p in pools if p.kind == "SSL" and p.untapped and p.price < price]
    near_up = min((p.price - price for p in above), default=None)
    near_dn = min((price - p.price for p in below), default=None)

    if near_up is not None and near_dn is not None:
        if near_up < near_dn * 0.7:
            score += 1.0
            b.reasons.append("위쪽 미회수 유동성이 더 가깝다 → 위를 먼저 가지러 간다")
        elif near_dn < near_up * 0.7:
            score -= 1.0
            b.reasons.append("아래쪽 미회수 유동성이 더 가깝다")
    elif near_up is not None:
        score += 0.5
        b.reasons.append("위쪽에만 미회수 유동성이 남아 있다")
    elif near_dn is not None:
        score -= 0.5
        b.reasons.append("아래쪽에만 미회수 유동성이 남아 있다")

    # --- 3) 전일 종가 위치 --------------------------------------------
    levels = daily_levels(bars)
    days = sorted(levels)
    if len(days) >= 2:
        prev = days[-2]
        hi, lo, idx = levels[prev]
        close_prev = bars[idx].close
        rng = hi - lo
        if rng > 0:
            pos = (close_prev - lo) / rng
            if pos > 0.7:
                score += 0.5
                b.reasons.append(f"전일 고점 부근 마감 ({pos:.0%}) → 상승 지속 성향")
            elif pos < 0.3:
                score -= 0.5
                b.reasons.append(f"전일 저점 부근 마감 ({pos:.0%}) → 하락 지속 성향")

    # --- 4) 채워지지 않은 FVG 가 자석 ---------------------------------
    gaps = fair_value_gaps(bars[-400:], min_size=gold.min_fvg(v))
    off = max(0, len(bars) - 400)
    open_up = open_dn = 0
    for g in gaps:
        if is_filled(bars[off:], g, len(bars) - off - 1):
            continue
        if g.bottom > price:
            open_up += 1
        elif g.top < price:
            open_dn += 1
    if open_up or open_dn:
        if open_up > open_dn * 1.5:
            score += 0.5
            b.reasons.append(f"위쪽에 미충족 FVG {open_up}개 (아래 {open_dn}개)")
        elif open_dn > open_up * 1.5:
            score -= 0.5
            b.reasons.append(f"아래쪽에 미충족 FVG {open_dn}개 (위 {open_up}개)")

    # --- 결론 ---------------------------------------------------------
    b.score = score
    if score >= 1.5:
        b.lean, b.direction = "bullish", BULL
    elif score <= -1.5:
        b.lean, b.direction = "bearish", BEAR
    else:
        b.lean, b.direction = "neutral", None

    if b.direction == BULL and above:
        b.target = min(above, key=lambda p: p.price)
    elif b.direction == BEAR and below:
        b.target = max(below, key=lambda p: p.price)
    return b
