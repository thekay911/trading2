#!/usr/bin/env python3
"""MT5/TradingView 내보내기 CSV 를 ict/crowcode 형식으로 바꾼다.

입력 형식 (세미콜론, 브로커 서버 시간):
    Date;Open;High;Low;Close;Volume
    2004.06.11 07:00;384;384.3;383.3;383.8;44

출력 형식 (쉼표, UTC):
    time,open,high,low,close,volume

--server-offset 은 '서버시간 - GMT' 다. MT5 브로커는 보통 +2 또는 +3.
모르면 --detect 로 추정한다: 하루 휴장(뉴욕 17~18시)이 서버시간 몇 시에
오는지 세어서 역산한다.
"""

from __future__ import annotations

import argparse
import collections
import csv
import sys
from datetime import datetime, timedelta, timezone


def read(path: str, delim: str = ";") -> list[tuple]:
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as fh:
        r = csv.reader(fh, delimiter=delim)
        head = next(r, None)
        for x in r:
            if len(x) < 5:
                continue
            try:
                t = datetime.strptime(x[0].strip(), "%Y.%m.%d %H:%M")
            except ValueError:
                try:
                    t = datetime.fromisoformat(x[0].strip())
                except ValueError:
                    continue
            try:
                o, h, l, c = (float(x[1]), float(x[2]), float(x[3]), float(x[4]))
            except ValueError:
                continue
            v = float(x[5]) if len(x) > 5 and x[5] else 0.0
            rows.append((t, o, h, l, c, v))
    rows.sort(key=lambda r: r[0])
    return rows


def detect_offset(rows: list[tuple]) -> float:
    """휴장 시각으로 서버-GMT 오프셋을 추정한다.

    금 선물은 뉴욕 17:00~18:00 에 쉰다. 겨울(뉴욕 = GMT-5)이면 그게
    GMT 22:00~23:00 이므로, 그 시각의 서버시(hour)를 알면 오프셋이 나온다.
    """
    per_hour = collections.Counter()
    for t, *_ in rows:
        if t.year < rows[-1][0].year - 3:
            continue
        if 11 <= t.month or t.month <= 3:          # 겨울만 (미국 서머타임 밖)
            per_hour[t.hour] += 1
    if not per_hour:
        return 0.0
    quiet = min(per_hour, key=lambda h: per_hour[h])
    # 휴장 시작 = GMT 22시  ->  offset = quiet - 22 (mod 24, -12..+12 로 접기)
    off = (quiet - 22) % 24
    if off > 12:
        off -= 24
    return float(off)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src")
    ap.add_argument("--out", required=True)
    ap.add_argument("--server-offset", type=float, default=None,
                    help="서버시간 - GMT, 시간 단위 (예: 2)")
    ap.add_argument("--detect", action="store_true", help="오프셋 자동 추정")
    ap.add_argument("--delimiter", default=";")
    ap.add_argument("--since", help="이 날짜부터 (YYYY-MM-DD)")
    a = ap.parse_args()

    rows = read(a.src, a.delimiter)
    if not rows:
        raise SystemExit("읽은 봉이 없다 — 구분자나 형식을 확인")

    off = a.server_offset
    guess = detect_offset(rows)
    if off is None:
        if not a.detect:
            raise SystemExit(f"--server-offset 을 주거나 --detect 를 쓰세요 "
                             f"(추정값 {guess:+g})")
        off = guess
        print(f"오프셋 추정: 서버 = GMT{off:+g}", file=sys.stderr)
    elif abs(off - guess) > 0.5:
        print(f"경고: 준 값 {off:+g} 과 추정값 {guess:+g} 이 다릅니다. "
              f"세션 필터가 통째로 밀릴 수 있습니다.", file=sys.stderr)

    since = datetime.fromisoformat(a.since) if a.since else None
    shift = timedelta(hours=off)

    kept = 0
    with open(a.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["time", "open", "high", "low", "close", "volume"])
        for t, o, h, l, c, v in rows:
            if since and t < since:
                continue
            utc = (t - shift).replace(tzinfo=timezone.utc)
            w.writerow([int(utc.timestamp()), o, h, l, c, v])
            kept += 1

    lo = (rows[0][0] - shift)
    hi = (rows[-1][0] - shift)
    print(f"{a.out}: {kept:,}봉  {lo:%Y-%m-%d} ~ {hi:%Y-%m-%d} (UTC)")


if __name__ == "__main__":
    main()
