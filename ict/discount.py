"""가치 구조 + 피보 할인존 진입.

출처: 프롭 트레이더 Chris 인터뷰(IQ Capital). 그가 말한 순서는
**환경 -> 위치 -> 확인** 이고, 각 단계가 꽤 기계적이다.

  1. 환경   상위 시간대가 value-up 인가 value-down 인가 (고점·저점 갱신)
            + GEX 레짐 (옵션 데이터 -> **여기서는 못 잰다**)
  2. 위치   추세 방향으로만, 그리고 **할인**에서만.
            할인 = 스윙 저점~고점 피보의 0.705 ~ 0.886 구간.
            **그 구간이 Value Area 밖에 있어야 한다.** VA 안이면 안 쓴다.
            0.886 아래로 내려가면 그 아이디어는 무효다.
  3. 확인   오더플로우 흡수 + 지배권 전환 (**틱 데이터가 없어 못 잰다**).
            여기서는 그 자리에서의 캔들 되돌림 실패로 대체한다.

  손절: 실패한 매도 아래.  목표: 스윙 포인트.  대개 1.5~2R.

못 재는 두 가지(GEX, 오더플로우)가 그의 방식에서 작지 않은 부분이다.
그래서 여기 결과는 '그의 전략' 이 아니라 '그의 위치 규칙' 의 성적이다.
"""

from __future__ import annotations

from typing import Literal, Sequence

from ict import pdarrays as pda
from ict.engine import Market
from ict.models import Config, Setup
from ict.strategy import _build, _gate, _raid
from ict.structure import BEAR, BULL

Side = Literal["buy", "sell"]

#: 그가 쓰는 피보 레벨. 0.886 이 마지막 방어선이다.
FIB_START, FIB_MID, FIB_END = 0.705, 0.788, 0.886


def value_structure(m: Market, now: int, lookback: int = 400) -> int:
    """상위 구조가 value-up(+1) 인가 value-down(-1) 인가, 아니면 0.

    '고점과 저점이 계속 높아지는가' 를 두 구간 비교로 본다.
    """
    if now < lookback:
        return 0
    a0, a1 = now - lookback, now - lookback // 2
    b0, b1 = a1, now + 1
    ah = max(c.high for c in m.candles[a0:a1])
    al = min(c.low for c in m.candles[a0:a1])
    bh = max(c.high for c in m.candles[b0:b1])
    bl = min(c.low for c in m.candles[b0:b1])
    if bh > ah and bl > al:
        return +1
    if bh < ah and bl < al:
        return -1
    return 0


def leg(m: Market, now: int, up: bool, lookback: int = 200):
    """되돌림을 잴 기준 레그. 상승이면 (저점, 고점) 순서로 만들어진 것."""
    lo = max(0, now - lookback)
    seg = m.candles[lo:now + 1]
    if len(seg) < 20:
        return None
    hi_i = max(range(len(seg)), key=lambda i: seg[i].high)
    lo_i = min(range(len(seg)), key=lambda i: seg[i].low)
    if up and lo_i >= hi_i:
        return None                      # 저점이 고점보다 나중이면 상승 레그가 아니다
    if not up and hi_i >= lo_i:
        return None
    high, low = seg[hi_i].high, seg[lo_i].low
    if high <= low:
        return None
    return low, high, lo + lo_i, lo + hi_i


def fib_zone(low: float, high: float, up: bool) -> tuple[float, float]:
    """할인(상승) 또는 프리미엄(하락) 존. (아래, 위)."""
    size = high - low
    if up:
        return high - size * FIB_END, high - size * FIB_START
    return low + size * FIB_START, low + size * FIB_END


def outside_value_area(zone: tuple[float, float], val: float, vah: float,
                       up: bool) -> bool:
    """존이 Value Area 밖에 있어야 한다.

    그가 명시적으로 말한 조건이다 — 피보 레벨이 VA 안에 앉아 있으면 안 쓴다.
    """
    zlo, zhi = zone
    if vah <= val:
        return False
    return (zhi <= val) if up else (zlo >= vah)


def discount(m: Market, now: int, cfg: Config,
             require_va: bool = True, vol_pct: float = 0.35) -> Setup | None:
    """환경 -> 위치 -> 확인."""
    kz = _gate(m, now, cfg)
    if kz is None:
        return None

    # --- 1) 환경 --------------------------------------------------
    vs = value_structure(m, now)
    if vs == 0:
        return None
    side: Side = "buy" if vs > 0 else "sell"
    up = (vs > 0)

    # 참여가 죽은 구간은 건드리지 않는다 (그의 20,000 계약 임계값 대체)
    if vol_pct > 0:
        lo = max(0, now - 500)
        vols = sorted(c.volume for c in m.candles[lo:now + 1] if c.volume > 0)
        if vols:
            cut = vols[int(len(vols) * vol_pct)]
            if m.candles[now].volume < cut:
                return None

    lg = leg(m, now, up)
    if lg is None:
        return None
    low, high, lo_i, hi_i = lg
    zone = fib_zone(low, high, up)
    zlo, zhi = zone

    # --- 2) 위치 --------------------------------------------------
    bar = m.candles[now]
    if not (zlo <= bar.close <= zhi):
        return None                      # 아직 존 안이 아니다

    # 0.886 을 넘어가면 무효
    size = high - low
    guard = high - size * FIB_END if up else low + size * FIB_END
    if (up and bar.close < guard) or (not up and bar.close > guard):
        return None

    if require_va:
        prev = m.prev_session_va(now)
        if prev is None:
            return None
        val, vah, closed_inside = prev
        if not closed_inside:
            return None                  # 종가가 VA 밖이면 그 VA 는 무효
        if not outside_value_area(zone, val, vah, up):
            return None                  # 존이 VA 안에 있으면 안 쓴다

    # --- 3) 확인 (오더플로우 대체) ---------------------------------
    # 존 안에서 반대편이 밀어붙였는데 결과가 없다: 직전 봉이 존 안에서
    # 극점을 만들었고, 이번 봉이 그 방향을 되돌려 마감.
    if now < 2:
        return None
    p = m.candles[now - 1]
    if up:
        if not (p.low <= zhi and bar.close > p.close and bar.close > bar.open):
            return None
        extreme = min(p.low, bar.low)
    else:
        if not (p.high >= zlo and bar.close < p.close and bar.close < bar.open):
            return None
        extreme = max(p.high, bar.high)

    v = m.volatility(now)
    buf = m.gold.stop_buffer(v)
    entry = bar.close
    stop = extreme - buf if up else extreme + buf
    if (up and stop >= entry) or (not up and stop <= entry):
        return None

    arr = pda.PDArray("FIB", BULL if up else BEAR, zhi, zlo, now)
    notes = [f"{'value-up' if up else 'value-down'} 구조",
             f"피보 {FIB_START:g}~{FIB_END:g} 존 {zlo:.2f}~{zhi:.2f}",
             f"레그 {low:.2f}~{high:.2f}",
             "존 안에서 반대편 실패 후 되돌림"]
    return _build(m, now, "Discount", side, entry, stop, arr,
                  m.last_mss(now, 200), _raid(m, now, side, 20),  # type: ignore[arg-type]
                  kz, notes, cfg)
