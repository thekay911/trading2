"""MT5 내보내기 → ICT 엔진용 CSV.

MT5 의 '데이터 내보내기' 는 보통 이렇게 나온다.

    <DATE>	<TIME>	<OPEN>	<HIGH>	<LOW>	<CLOSE>	<TICKVOL>	<VOL>	<SPREAD>
    2018.01.02	00:00	1302.55	1303.10	1302.30	1302.85	120	0	25

시각이 **브로커 서버 시간**이므로 UTC 로 바꿔야 한다. 이걸 안 하면
킬존이 2~3시간 밀려서 ICT 분석이 통째로 어긋난다.

    python3 tools/mt5_to_csv.py raw.csv --out XAUUSD_M5.csv --server-offset 3
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime, timedelta, timezone


def sniff(path: str) -> str:
    with open(path, encoding="utf-8-sig", errors="replace") as fh:
        head = fh.readline()
    for d in ("\t", ";", ","):
        if head.count(d) >= 4:
            return d
    return ","


def parse_time(date_s: str, time_s: str | None) -> datetime | None:
    raw = f"{date_s} {time_s}".strip() if time_s else date_s.strip()
    raw = raw.replace(".", "-").replace("/", "-")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def main(argv=None) -> int:
    p = argparse.ArgumentParser("mt5_to_csv")
    p.add_argument("source", help="MT5 에서 내보낸 파일")
    p.add_argument("--out", required=True)
    p.add_argument("--server-offset", type=float, default=0.0, dest="offset",
                   help="서버시간 - UTC (시간). 엑스네스는 겨울 2 / 여름 3")
    p.add_argument("--since", help="이 날짜부터만 (예: 2018-01-01)")
    args = p.parse_args(argv)

    delim = sniff(args.source)
    cut = None
    if args.since:
        cut = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)

    written = skipped = 0
    with open(args.source, encoding="utf-8-sig", errors="replace") as src, \
            open(args.out, "w", newline="", encoding="utf-8") as dst:
        reader = csv.reader(src, delimiter=delim)
        writer = csv.writer(dst)
        writer.writerow(["time", "open", "high", "low", "close", "volume"])

        for row in reader:
            if len(row) < 5:
                continue
            cells = [c.strip().strip("<>") for c in row]
            if not cells[0] or cells[0][0].isalpha():
                continue                                   # 헤더

            # 날짜/시각이 한 칸인지 두 칸인지 자동 판별
            if ":" in cells[1]:
                ts = parse_time(cells[0], cells[1])
                nums = cells[2:]
            else:
                ts = parse_time(cells[0], None)
                nums = cells[1:]
            if ts is None or len(nums) < 4:
                skipped += 1
                continue
            try:
                o, h, l, c = (float(x) for x in nums[:4])
                vol = float(nums[4]) if len(nums) > 4 and nums[4] else 0.0
            except ValueError:
                skipped += 1
                continue

            utc = ts.replace(tzinfo=timezone.utc) - timedelta(hours=args.offset)
            if cut and utc < cut:
                continue
            writer.writerow([utc.isoformat().replace("+00:00", "Z"),
                             f"{o:.5f}", f"{h:.5f}", f"{l:.5f}", f"{c:.5f}", f"{vol:.0f}"])
            written += 1

    print(f"{written:,}봉 저장 → {args.out}" + (f"  (건너뜀 {skipped})" if skipped else ""))
    if written == 0:
        print("한 줄도 못 읽었습니다. 파일 앞 몇 줄을 보여주시면 파서를 맞추겠습니다.",
              file=sys.stderr)
        return 1
    print(f"서버시간 -{args.offset:g}h 적용해서 UTC 로 저장했습니다. "
          "이 값이 틀리면 킬존이 밀립니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
