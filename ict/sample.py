"""금다운 합성 데이터.

crowcode 의 `synthetic` 은 무작위 보행이라 세션 구조가 없다. 그래서
킬존 통계가 24% 로 나오고(우연 수준), 변동성이 작아 손절이 스프레드
문턱을 못 넘는다. ICT 엔진을 검증하려면 최소한 이 셋은 있어야 한다.

  1. 세션별 변동성 차이 — 킬존에서 2~3배 크게 움직인다
  2. 일중 구조         — 아시아는 좁고, 런던이 한쪽을 털고, 뉴욕이 방향을 낸다
  3. 유동성 습격       — 전일 고·저를 건드리고 되돌아온다

**이건 시뮬레이션이지 시장이 아니다.** 여기서 좋은 결과가 나와도 근거가
아니다. 다만 '엔진이 구조를 감지할 수 있는가' 는 여기서만 확인 가능하다.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timedelta, timezone

from crowcode.data import Candle, Series
from ict.timeops import LONDON_KZ, NY_AM_KZ, SILVER_BULLET_AM, ny_clock, to_ny


def _session_vol(moment: datetime) -> float:
    """뉴욕 시간대별 변동성 배수. 실제 금의 일중 프로필에 맞췄다."""
    h = ny_clock(moment)
    if 2.0 <= h < 5.0:        # 런던 킬존
        return 2.6
    if 7.0 <= h < 10.0:       # 뉴욕 오전
        return 3.0
    if 10.0 <= h < 11.0:      # 실버불릿
        return 2.4
    if 11.0 <= h < 12.0:
        return 1.6
    if 12.0 <= h < 16.0:
        return 1.4
    if 17.0 <= h < 20.0:      # 롤오버 — 죽은 구간
        return 0.35
    if 20.0 <= h or h < 2.0:  # 아시아
        return 0.6
    return 1.0


def _ny_midnight(after: datetime) -> datetime:
    """`after` 이후 첫 뉴욕 자정(UTC).

    시계열을 뉴욕 자정에 맞춰 시작해야 하루 경계가 깨끗하다. 첫날이
    5시간짜리로 잘리면 전일 고저·아시아 레인지가 다 어긋난다.
    """
    t = after.replace(minute=0, second=0, microsecond=0)
    for _ in range(48):
        if to_ny(t).hour == 0:
            return t
        t += timedelta(hours=1)
    return after


def gold(
    days: int = 120,
    minutes: int = 5,
    price: float = 3300.0,
    daily_range: float = 32.0,
    seed: int = 11,
    start: datetime | None = None,
) -> Series:
    """세션 구조가 있는 XAUUSD 형태의 M5 시계열.

    daily_range 는 목표 일중 변동폭(달러). 금은 $2,000 대에서 $25~35,
    $3,300 대에서 $30~45 정도다.
    """
    rnd = random.Random(seed)
    bars_per_day = 24 * 60 // minutes
    ts = start or _ny_midnight(datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc))

    # 세션 배수의 평균으로 기준 봉 변동성을 역산한다
    probe = [_session_vol(ts + timedelta(minutes=minutes * i)) for i in range(bars_per_day)]
    mean_mult = sum(probe) / len(probe)
    base_sigma = daily_range / (mean_mult * math.sqrt(bars_per_day) * 2.2)

    out: list[Candle] = []
    p = price
    day_bias = 0.0
    prev_high = prev_low = None
    day_high = day_low = p
    cur_day = to_ny(ts).date()

    for i in range(days * bars_per_day):
        n = to_ny(ts)
        if n.date() != cur_day:
            prev_high, prev_low = day_high, day_low
            day_high = day_low = p
            cur_day = n.date()
            day_bias = rnd.gauss(0, 1.0)          # 그날의 방향 성향

        mult = _session_vol(ts)
        sigma = base_sigma * mult
        drift = day_bias * sigma * 0.18

        # 유동성 습격 — 런던 킬존에서 전일 고·저를 건드리고 되돌아온다
        pull = 0.0
        if prev_high is not None and LONDON_KZ.contains(ts):
            if abs(p - prev_high) < sigma * 6 and rnd.random() < 0.05:
                pull = (prev_high - p) * 0.45
            elif abs(p - prev_low) < sigma * 6 and rnd.random() < 0.05:
                pull = (prev_low - p) * 0.45

        step = rnd.gauss(drift, sigma) + pull
        o = p
        c = p + step
        wick = abs(rnd.gauss(0, sigma * 0.55)) + sigma * 0.08
        h = max(o, c) + wick
        l = min(o, c) - wick
        out.append(Candle(ts, round(o, 2), round(h, 2), round(l, 2), round(c, 2),
                          volume=abs(rnd.gauss(200 * mult, 50))))
        p = c
        day_high = max(day_high, h)
        day_low = min(day_low, l)
        ts += timedelta(minutes=minutes)

    return Series(out, "XAUUSD", f"M{minutes}")
