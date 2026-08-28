"""세션 분할.

자료의 전제: 아시아가 유럽의 레인지를 만들고, 유럽이 CME 의 레인지를 만든다.
그래서 세션 경계가 곧 프로파일 경계다. 뉴욕 시간 기준.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterator, Sequence

from crowcode.data import Candle
from ict.timeops import ny_clock, ny_date

#: (이름, 시작시각, 끝시각) — 뉴욕 시간. 아시아는 자정을 넘는다.
SESSIONS = (("Asia", 20.0, 2.0), ("Europe", 2.0, 8.0), ("CME", 8.0, 17.0))


def _in(name: str, lo: float, hi: float, h: float) -> bool:
    return (lo <= h or h < hi) if lo > hi else (lo <= h < hi)


@dataclass
class Session:
    name: str
    day: date               #: 이 세션이 속한 뉴욕 날짜 (아시아는 끝나는 날)
    bars: list[Candle]


def split(candles: Sequence[Candle]) -> list[Session]:
    """봉들을 세션 단위로 자른다. 봉이 너무 적은 세션은 버린다."""
    out: list[Session] = []
    cur: Session | None = None
    for c in candles:
        h = ny_clock(c.ts)
        d = ny_date(c.ts)
        name = None
        for nm, lo, hi in SESSIONS:
            if _in(nm, lo, hi, h):
                name = nm
                # 아시아 20시~자정은 다음날 세션에 속한다
                if nm == "Asia" and h >= 20.0:
                    d = d + timedelta(days=1)
                break
        if name is None:
            cur = None
            continue
        if cur is None or cur.name != name or cur.day != d:
            cur = Session(name, d, [])
            out.append(cur)
        cur.bars.append(c)
    return [s for s in out if len(s.bars) >= 4]


def by_day(sessions: Sequence[Session]) -> dict[date, dict[str, Session]]:
    days: dict[date, dict[str, Session]] = {}
    for s in sessions:
        days.setdefault(s.day, {})[s.name] = s
    return days


def chain(sessions: Sequence[Session]) -> Iterator[tuple[Session, Session]]:
    """앞 세션 -> 뒤 세션 쌍. 자료가 말하는 '아시아가 유럽을 만든다' 관계."""
    order = {"Asia": 0, "Europe": 1, "CME": 2}
    days = by_day(sessions)
    for d in sorted(days):
        got = days[d]
        seq = [got[n] for n in ("Asia", "Europe", "CME") if n in got]
        for a, b in zip(seq, seq[1:]):
            yield a, b
