"""SNR 레벨과 신선도.

자료의 규칙
  · 레벨은 두 봉의 접점(시가/종가가 만나는 자리)에서 만들어진다
      A형   같은 방향 두 봉 (매수+매수, 매도+매도)
      V형   반대 방향 두 봉
  · 신선도(fresh): 아직 안 건드린 레벨. 꼬리가 닿으면 신선도를 잃는다.
  · 몸통으로 깨지면 반대 역할로 다시 신선해진다.
  · 한 레벨은 두 번까지만 쓴다.

여기서 재려는 것: **신선한 레벨이 정말로 더 잘 반응하는가.**
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Sequence

from crowcode.data import Candle

Kind = Literal["A", "V"]
Side = Literal["support", "resistance"]


@dataclass
class Level:
    price: float
    kind: Kind
    side: Side
    index: int
    ts: datetime
    touches: int = 0          #: 꼬리로 닿은 횟수
    broken_at: int = -1       #: 몸통으로 깨진 봉

    @property
    def fresh(self) -> bool:
        return self.touches == 0

    def describe(self) -> str:
        s = "지지" if self.side == "support" else "저항"
        return (f"{self.kind}형 {s} {self.price:.2f}  "
                f"{'신선' if self.fresh else f'{self.touches}회 사용'}")


def _bull(c: Candle) -> bool:
    return c.close > c.open


def find(candles: Sequence[Candle], min_body: float = 0.0) -> list[Level]:
    """인접한 두 봉에서 레벨을 만든다.

    A형: 같은 방향 두 봉 -> 두 번째 봉의 시가 (되돌림이 멈춘 자리)
    V형: 반대 방향 두 봉 -> 두 봉이 만나는 자리 = 두 번째 봉의 시가
    두 경우 다 접점은 같지만 성격이 다르므로 종류를 구분해 둔다.
    """
    out: list[Level] = []
    for i in range(1, len(candles)):
        a, b = candles[i - 1], candles[i]
        if abs(b.close - b.open) < min_body or abs(a.close - a.open) < min_body:
            continue
        same = _bull(a) == _bull(b)
        kind: Kind = "A" if same else "V"
        # 위로 가는 봉이 만든 접점은 지지, 아래로 가는 봉이 만든 접점은 저항
        side: Side = "support" if _bull(b) else "resistance"
        out.append(Level(price=b.open, kind=kind, side=side, index=i, ts=b.ts))
    return out


def find_scaled(candles: Sequence[Candle], atrs: Sequence[float],
                body_mult: float = 0.3) -> list[Level]:
    """봉마다의 ATR 로 최소 몸통 기준을 잡아 레벨을 찾는다.

    전역 상수 하나로 거르면 가격대가 크게 변한 시계열에서 한쪽 구간이
    통째로 사라진다.
    """
    out: list[Level] = []
    for i in range(1, len(candles)):
        a = atrs[i] if i < len(atrs) else 0.0
        if a <= 0:
            continue
        need = a * body_mult
        x, b = candles[i - 1], candles[i]
        if abs(b.close - b.open) < need or abs(x.close - x.open) < need:
            continue
        kind: Kind = "A" if (_bull(x) == _bull(b)) else "V"
        side: Side = "support" if _bull(b) else "resistance"
        out.append(Level(price=b.open, kind=kind, side=side, index=i, ts=b.ts))
    return out


def track(candles: Sequence[Candle], levels: Sequence[Level],
          max_uses: int = 2) -> None:
    """레벨의 신선도를 시간 순으로 갱신한다. levels 를 제자리에서 수정."""
    for lv in levels:
        for j in range(lv.index + 1, len(candles)):
            c = candles[j]
            body_lo, body_hi = min(c.open, c.close), max(c.open, c.close)
            if body_lo <= lv.price <= body_hi:
                lv.broken_at = j
                break
            if c.low <= lv.price <= c.high:
                lv.touches += 1
                if lv.touches >= max_uses:
                    break
