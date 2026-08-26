"""세션 / 뉴스 필터.

  "유럽·미국 세션에 M5 로 매매한다"
  "GDP, 비농업(19:30) 앞뒤로는 물량을 줄인다"
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Sequence

from crowcode.config import SessionWindow


@dataclass(frozen=True)
class NewsEvent:
    ts: datetime
    name: str
    impact: str = "high"


def _utc(ts: datetime) -> datetime:
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def in_session(ts: datetime, windows: Sequence[SessionWindow]) -> SessionWindow | None:
    t = _utc(ts)
    hour = t.hour + t.minute / 60.0
    for w in windows:
        if w.start_hour <= hour < w.end_hour:
            return w
    return None


def news_blackout(
    ts: datetime,
    events: Iterable[NewsEvent],
    before_min: int = 15,
    after_min: int = 30,
    impacts: Sequence[str] = ("high",),
) -> NewsEvent | None:
    t = _utc(ts)
    for e in events:
        if e.impact not in impacts:
            continue
        et = _utc(e.ts)
        if et - timedelta(minutes=before_min) <= t <= et + timedelta(minutes=after_min):
            return e
    return None


def friday_close_block(ts: datetime, cutoff_hour: float = 19.0) -> bool:
    """금요일 마감 직전은 갭 리스크로 신규 진입 금지."""
    t = _utc(ts)
    return t.weekday() == 4 and (t.hour + t.minute / 60.0) >= cutoff_hour
