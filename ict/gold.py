"""XAUUSD 보정 — ICT 개념을 금에 맞춘다.

가장 중요한 제약이 하나 있다.

  **2018년 금은 $1,300, 지금은 $3,300+ 다.**

절대 달러로 임계값을 박으면 그 기간을 관통하지 못한다. $2 짜리 FVG 는
2018년엔 큰 갭이었지만 지금은 노이즈다. 그래서 이 엔진의 모든 기준은
둘 중 하나로만 표현한다.

  · ATR 배수      — 그 시점의 실제 변동성 대비
  · bp (베이시스포인트) — 가격 대비 만분율. $1,300 의 15bp = $1.95,
                          $3,300 의 15bp = $4.95. 같은 '비중' 이다.

금의 성격
--------
  1랏 = 100oz  ·  $1 움직임 = $100/랏  ·  0.01랏이면 $1 움직임 = $1
  1핍 = $0.10 (= MT5 10포인트)  ·  호가 단위 $0.01
  스프레드: Standard $0.20~0.35 / Raw $0.10~0.20
  달러(DXY)와 역상관 → SMT 다이버전스 파트너로 DXY 를 쓴다
  뉴욕 17:00~20:00 은 롤오버 구간. 스프레드가 벌어지고 방향이 없다.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Sequence

from crowcode.data import Candle, atr
from ict.timeops import ny_clock

CONTRACT_SIZE = 100.0        # 1랏 = 100 트로이온스
PIP = 0.10                   # 1핍 = $0.10 (엑스네스)
POINT = 0.01


# ----------------------------------------------------------------------
# 단위 변환
# ----------------------------------------------------------------------
def bp_to_price(bp: float, price: float) -> float:
    """베이시스포인트 → 달러. 가격 수준에 자동으로 맞춰진다."""
    return price * bp / 10_000.0


def price_to_bp(delta: float, price: float) -> float:
    return delta / price * 10_000.0 if price > 0 else 0.0


def pips(delta: float) -> float:
    return delta / PIP


def money(lots: float, price_move: float) -> float:
    """`lots` 랏에서 금이 `price_move` 달러 움직였을 때 손익."""
    return lots * CONTRACT_SIZE * price_move


# ----------------------------------------------------------------------
# 변동성 프로필
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class Volatility:
    """그 시점의 금 변동성. 임계값은 전부 여기서 파생된다."""
    price: float
    atr: float
    atr_bp: float

    @classmethod
    def measure(cls, candles: Sequence[Candle], period: int = 20) -> "Volatility":
        bars = list(candles)
        price = bars[-1].close if bars else 0.0
        a = atr(bars[-max(period * 4, 60):], period) if bars else 0.0
        return cls(price, a, price_to_bp(a, price))

    def scaled(self, atr_mult: float = 0.0, bp: float = 0.0) -> float:
        """ATR 배수와 bp 중 **큰 쪽**을 쓴다.

        변동성이 죽은 구간에서 ATR 만 쓰면 임계값이 스프레드보다 작아진다.
        반대로 급변 구간에서 bp 만 쓰면 노이즈를 다 통과시킨다.
        둘 중 큰 값을 쓰면 양쪽 모두 막힌다.
        """
        return max(self.atr * atr_mult, bp_to_price(bp, self.price))


# ----------------------------------------------------------------------
# 금 전용 설정
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class GoldProfile:
    """XAUUSD 에 맞춘 ICT 임계값. 전부 상대값이다."""

    # --- 변위: 금은 킬존에서 ATR 2배 이상 한 번에 나간다 ---------------
    displacement_atr: float = 1.5
    displacement_bp: float = 8.0          # $2,000 기준 $1.60, $3,300 기준 $2.64

    # --- FVG: 스프레드보다 의미 있게 커야 한다 ------------------------
    min_fvg_atr: float = 0.20
    min_fvg_bp: float = 1.5               # $2,000 → $0.30, $3,300 → $0.50
    fvg_spread_multiple: float = 2.0      # 최소한 스프레드의 2배

    # --- 유동성: 동일 고저로 묶는 허용 오차 ---------------------------
    equal_level_atr: float = 0.15
    equal_level_bp: float = 1.0

    # --- 손절 버퍼 ----------------------------------------------------
    stop_buffer_atr: float = 0.25
    stop_buffer_bp: float = 1.5

    # --- 진입 거리: 이보다 먼 PD Array 는 이번 셋업이 아니다 ----------
    max_entry_distance_atr: float = 3.0

    # --- 금 고유 시간 -------------------------------------------------
    avoid_rollover: bool = True           # 뉴욕 17:00~20:00 (롤오버)
    rollover_start: float = 17.0
    rollover_end: float = 20.0

    # --- 체결 비용 ----------------------------------------------------
    spread: float = 0.25                  # 엑스네스 Standard 기준
    max_spread_to_stop: float = 0.15      # 스프레드가 손절폭의 15% 넘으면 스킵

    def displacement(self, v: Volatility) -> float:
        return v.scaled(self.displacement_atr, self.displacement_bp)

    def min_fvg(self, v: Volatility) -> float:
        return max(v.scaled(self.min_fvg_atr, self.min_fvg_bp),
                   self.spread * self.fvg_spread_multiple)

    def equal_tolerance(self, v: Volatility) -> float:
        return v.scaled(self.equal_level_atr, self.equal_level_bp)

    def stop_buffer(self, v: Volatility) -> float:
        return v.scaled(self.stop_buffer_atr, self.stop_buffer_bp)

    def in_rollover(self, moment: datetime) -> bool:
        if not self.avoid_rollover:
            return False
        h = ny_clock(moment)
        return self.rollover_start <= h < self.rollover_end

    def spread_ok(self, stop_distance: float) -> bool:
        if self.max_spread_to_stop <= 0 or stop_distance <= 0:
            return True
        return self.spread <= stop_distance * self.max_spread_to_stop

    def with_(self, **kw) -> "GoldProfile":
        return replace(self, **kw)


#: 엑스네스 Standard 기본값
STANDARD = GoldProfile()

#: Raw / Zero 계좌 — 스프레드가 좁으니 더 작은 FVG 도 쓸 수 있다
RAW = GoldProfile(spread=0.15, max_spread_to_stop=0.12)

#: 보수적 — 변위와 FVG 기준을 올려 셋업 수를 줄인다
STRICT = GoldProfile(displacement_atr=2.0, displacement_bp=12.0,
                     min_fvg_atr=0.30, min_fvg_bp=2.5, max_spread_to_stop=0.10)

PROFILES = {"standard": STANDARD, "raw": RAW, "strict": STRICT}


def profile(name: str) -> GoldProfile:
    key = name.lower()
    if key not in PROFILES:
        raise ValueError(f"알 수 없는 프로필: {name} (가능: {', '.join(PROFILES)})")
    return PROFILES[key]


# ----------------------------------------------------------------------
# 진단
# ----------------------------------------------------------------------
def describe(v: Volatility, p: GoldProfile = STANDARD) -> str:
    lines = [
        f"금 {v.price:,.2f}   ATR {v.atr:.2f} ({v.atr_bp:.1f}bp)",
        f"  변위 기준      ${p.displacement(v):.2f}  ({pips(p.displacement(v)):.0f}핍)",
        f"  최소 FVG       ${p.min_fvg(v):.2f}  ({pips(p.min_fvg(v)):.0f}핍)",
        f"  동일고저 오차   ${p.equal_tolerance(v):.2f}",
        f"  손절 버퍼      ${p.stop_buffer(v):.2f}",
        f"  스프레드       ${p.spread:.2f}  (손절폭의 {p.max_spread_to_stop:.0%} 까지 허용)",
    ]
    return "\n".join(lines)
