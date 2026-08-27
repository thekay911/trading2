"""뉴욕 시간과 ICT 시간 창.

ICT 의 모든 시각은 **뉴욕 로컬 시간**이다. 서머타임을 직접 처리한다
(미국은 3월 둘째 일요일 ~ 11월 첫째 일요일 EDT = UTC-4, 나머지는 EST = UTC-5).
zoneinfo 가 있으면 그걸 쓰고, 없으면 이 규칙으로 계산한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

try:                                            # pragma: no cover - 환경 의존
    from zoneinfo import ZoneInfo
    _NY = ZoneInfo("America/New_York")
except Exception:                               # pragma: no cover
    _NY = None


def _second_sunday(year: int, month: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(6 - d.weekday()) % 7)   # 첫 일요일
    return d + timedelta(days=7)


def _first_sunday(year: int, month: int) -> date:
    d = date(year, month, 1)
    return d + timedelta(days=(6 - d.weekday()) % 7)


def ny_offset_hours(moment: datetime) -> int:
    """해당 UTC 시각의 뉴욕 오프셋. EDT 면 -4, EST 면 -5."""
    u = moment.astimezone(timezone.utc) if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
    start = datetime.combine(_second_sunday(u.year, 3), time(7), tzinfo=timezone.utc)   # 02:00 EST
    end = datetime.combine(_first_sunday(u.year, 11), time(6), tzinfo=timezone.utc)     # 02:00 EDT
    return -4 if start <= u < end else -5


def to_ny(moment: datetime) -> datetime:
    """UTC(또는 naive=UTC) → 뉴욕 시간."""
    u = moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
    if _NY is not None:
        return u.astimezone(_NY)
    return u.astimezone(timezone(timedelta(hours=ny_offset_hours(u))))


def ny_clock(moment: datetime) -> float:
    """뉴욕 기준 '시.분' 을 실수로. 09:30 → 9.5"""
    n = to_ny(moment)
    return n.hour + n.minute / 60.0


def ny_date(moment: datetime) -> date:
    return to_ny(moment).date()


# ----------------------------------------------------------------------
# 킬존과 시간 창 (전부 뉴욕 시간)
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class Window:
    name: str
    start: float
    end: float

    def contains(self, moment: datetime) -> bool:
        h = ny_clock(moment)
        if self.start <= self.end:
            return self.start <= h < self.end
        return h >= self.start or h < self.end     # 자정을 넘는 창

    @property
    def minutes(self) -> int:
        span = (self.end - self.start) % 24
        return int(round(span * 60))


#: 아시아 레인지 — 축적 구간. 여기 고저가 다음 세션의 유동성이 된다.
ASIAN_RANGE = Window("AsianRange", 20.0, 0.0)

#: 런던 킬존 — 하루의 고점 또는 저점이 자주 여기서 나온다.
LONDON_KZ = Window("LondonKZ", 2.0, 5.0)

#: 뉴욕 오전 킬존
NY_AM_KZ = Window("NY_AM_KZ", 7.0, 10.0)

#: 실버 불릿 (오전) — ICT 가 가장 좁게 특정한 창
SILVER_BULLET_AM = Window("SilverBulletAM", 10.0, 11.0)

#: 런던 클로즈 킬존
LONDON_CLOSE_KZ = Window("LondonCloseKZ", 10.0, 12.0)

#: 뉴욕 오후 / 실버 불릿 (오후)
SILVER_BULLET_PM = Window("SilverBulletPM", 14.0, 15.0)

KILLZONES: tuple[Window, ...] = (
    LONDON_KZ, NY_AM_KZ, SILVER_BULLET_AM, LONDON_CLOSE_KZ, SILVER_BULLET_PM,
)

#: 주요 기준 시각
MIDNIGHT_OPEN = 0.0      # 뉴욕 자정 — 프리미엄/디스카운트의 기준선
NEWS_830 = 8.5           # 미국 지표
EQUITIES_OPEN = 9.5      # 뉴욕 증시 개장


def in_killzone(moment: datetime) -> Window | None:
    for w in KILLZONES:
        if w.contains(moment):
            return w
    return None


def active_windows(moment: datetime) -> tuple[str, ...]:
    """겹치는 창을 모두 돌려준다 (10:00~11:00 은 실버불릿이자 런던클로즈다)."""
    out = [w.name for w in KILLZONES if w.contains(moment)]
    if ASIAN_RANGE.contains(moment):
        out.append(ASIAN_RANGE.name)
    return tuple(out)


def in_macro(moment: datetime) -> bool:
    """ICT 매크로 — 매시 :50 부터 다음 :10 까지 알고리즘이 움직이는 구간."""
    m = to_ny(moment).minute
    return m >= 50 or m < 10


def session_of(moment: datetime) -> str:
    h = ny_clock(moment)
    if 20.0 <= h or h < 2.0:
        return "asia"
    if 2.0 <= h < 7.0:
        return "london"
    if 7.0 <= h < 12.0:
        return "ny_am"
    if 12.0 <= h < 17.0:
        return "ny_pm"
    return "off"


def is_ny_midnight(moment: datetime) -> bool:
    n = to_ny(moment)
    return n.hour == 0 and n.minute == 0
