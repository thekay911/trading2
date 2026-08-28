"""세션 TPO 프로파일.

진짜 TPO 는 30분마다 알파벳을 하나씩 붙여 가격 사다리를 채운다.
여기서는 M15/M30 OHLC 로 그걸 근사한다 — 각 브래킷(기본 30분)이
닿은 가격 버킷 전부에 한 글자씩.

  POC   글자가 가장 많이 쌓인 가격 (Point of Control)
  VA    전체 TPO 의 70% 를 담는 구간, POC 에서 위아래로 넓혀 간다
  IB    Initial Balance — 세션 첫 한 시간의 고·저
  단일 프린트  글자가 하나뿐인 가격대. 경매가 비정상이었던 흔적
  poor high/low  극점에 글자가 2개 이상. 경매가 안 끝났다는 뜻
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Sequence

from crowcode.data import Candle


@dataclass
class Profile:
    """한 세션의 프로파일."""

    start: datetime
    end: datetime
    tick: float                       #: 가격 버킷 크기
    counts: dict[int, int] = field(default_factory=dict)   #: 버킷 -> TPO 수
    brackets: int = 0
    high: float = 0.0
    low: float = 0.0
    open: float = 0.0
    close: float = 0.0
    ib_high: float = 0.0
    ib_low: float = 0.0
    volume: float = 0.0

    # --- 기본값 ------------------------------------------------------
    def price(self, bucket: int) -> float:
        return bucket * self.tick

    @property
    def poc(self) -> float:
        """글자가 가장 많이 쌓인 가격. 동점이면 가운데에 가까운 쪽."""
        if not self.counts:
            return 0.0
        top = max(self.counts.values())
        cands = [b for b, n in self.counts.items() if n == top]
        mid = (self.high + self.low) / 2.0
        return self.price(min(cands, key=lambda b: abs(self.price(b) - mid)))

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    def value_area(self, share: float = 0.70) -> tuple[float, float]:
        """POC 에서 위아래로 넓혀 전체의 `share` 를 담는 구간 (VAL, VAH)."""
        if not self.counts:
            return (0.0, 0.0)
        target = self.total * share
        buckets = sorted(self.counts)
        poc_b = min(buckets, key=lambda b: abs(self.price(b) - self.poc))
        lo = hi = buckets.index(poc_b)
        got = self.counts[buckets[lo]]
        while got < target and (lo > 0 or hi < len(buckets) - 1):
            up = self.counts.get(buckets[hi + 1], 0) if hi < len(buckets) - 1 else -1
            dn = self.counts.get(buckets[lo - 1], 0) if lo > 0 else -1
            if up >= dn and hi < len(buckets) - 1:
                hi += 1
                got += self.counts[buckets[hi]]
            elif lo > 0:
                lo -= 1
                got += self.counts[buckets[lo]]
            else:
                break
        return (self.price(buckets[lo]), self.price(buckets[hi]))

    @property
    def val(self) -> float:
        return self.value_area()[0]

    @property
    def vah(self) -> float:
        return self.value_area()[1]

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def va_width(self) -> float:
        lo, hi = self.value_area()
        return hi - lo

    @property
    def ib_range(self) -> float:
        return self.ib_high - self.ib_low

    # --- 자료에 나오는 판정들 ----------------------------------------
    def single_prints(self) -> list[float]:
        """글자가 하나뿐인 가격대 (IMB). 나중에 '고쳐질' 자리라고 본다."""
        return [self.price(b) for b, n in sorted(self.counts.items()) if n == 1]

    def poor_high(self, min_tpo: int = 2) -> bool:
        """극점에 글자가 2개 이상 = 경매가 안 끝났다 -> 뚫리기 쉽다."""
        if not self.counts:
            return False
        top = max(self.counts)
        return self.counts.get(top, 0) >= min_tpo

    def poor_low(self, min_tpo: int = 2) -> bool:
        if not self.counts:
            return False
        bot = min(self.counts)
        return self.counts.get(bot, 0) >= min_tpo

    def va_inside_ib(self) -> bool:
        """VA 가 IB 안에 들어가면 그 세션은 데이 트레이더가 굴린 것."""
        lo, hi = self.value_area()
        return self.ib_low <= lo and hi <= self.ib_high

    def contains(self, price: float) -> bool:
        lo, hi = self.value_area()
        return lo <= price <= hi

    def close_outside_va(self) -> bool:
        """종가가 VA 밖이면 그 VA 는 제대로 된 가치 구간이 아니다."""
        return not self.contains(self.close)

    def node_at(self, price: float) -> float:
        """그 가격의 TPO 수를 평균 대비 비율로. 1.0 미만이면 저볼륨 노드."""
        if not self.counts:
            return 0.0
        b = int(round(price / self.tick))
        avg = self.total / len(self.counts)
        return self.counts.get(b, 0) / avg if avg > 0 else 0.0

    def describe(self) -> str:
        lo, hi = self.value_area()
        flags = []
        if self.poor_high():
            flags.append("poor high")
        if self.poor_low():
            flags.append("poor low")
        if self.va_inside_ib():
            flags.append("VA in IB")
        if self.close_outside_va():
            flags.append("종가 VA 밖")
        return (f"{self.start:%Y-%m-%d %H:%M}~{self.end:%H:%M}  "
                f"고 {self.high:.2f} 저 {self.low:.2f}  "
                f"POC {self.poc:.2f}  VA {lo:.2f}~{hi:.2f}  "
                f"IB {self.ib_low:.2f}~{self.ib_high:.2f}"
                + (f"  [{', '.join(flags)}]" if flags else ""))


def build(candles: Sequence[Candle], tick: float, bracket_min: int = 30,
          ib_min: int = 60) -> Profile:
    """봉들로 프로파일 하나를 만든다.

    tick 은 가격 버킷 크기다. 금이면 $0.50~$1.00 정도가 읽기 좋다 —
    너무 잘게 쪼개면 모든 가격이 단일 프린트가 되고, 너무 굵으면
    POC 가 의미를 잃는다.
    """
    bars = list(candles)
    if not bars:
        raise ValueError("봉이 없다")

    p = Profile(start=bars[0].ts, end=bars[-1].ts, tick=tick)
    p.open = bars[0].open
    p.close = bars[-1].close
    p.high = max(c.high for c in bars)
    p.low = min(c.low for c in bars)
    p.volume = sum(c.volume for c in bars)

    ib = [c for c in bars if (c.ts - bars[0].ts) < timedelta(minutes=ib_min)]
    if ib:
        p.ib_high = max(c.high for c in ib)
        p.ib_low = min(c.low for c in ib)

    # 브래킷마다 한 글자씩
    seen: set[tuple[int, int]] = set()
    for c in bars:
        k = int((c.ts - bars[0].ts).total_seconds() // (bracket_min * 60))
        lo = int(c.low // tick)
        hi = int(c.high // tick)
        for b in range(lo, hi + 1):
            if (k, b) in seen:
                continue
            seen.add((k, b))
            p.counts[b] = p.counts.get(b, 0) + 1
        p.brackets = max(p.brackets, k + 1)
    return p
