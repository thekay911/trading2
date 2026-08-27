#!/usr/bin/env python3
"""실제 금 시세를 받아 ict/crowcode 가 읽는 CSV 로 만든다.

MT5 에서 내보내기가 번거로울 때 쓴다. MT5 export 가 있으면 그걸 쓰는 게
낫다 — 이건 COMEX 금 선물(GC=F)이고 XAUUSD 현물이 아니다.

  차이: 선물은 보유비용만큼 현물보다 비싸다(수십 달러). 세션은
        Globex 18:00~17:00 ET 로 현물과 거의 같고, 구조·킬존·변동성
        패턴은 사실상 같이 움직인다. 절대가격이 아니라 구조를 보는
        용도라면 대체재로 쓸 만하다.

  한계: 5분봉은 최근 60일, 1시간봉은 최근 730일까지만 준다.
        2018년부터가 필요하면 MT5 에서 내보내야 한다.

사용:
    python3 tools/fetch_gold.py --interval 5m  --out gold_m5.csv
    python3 tools/fetch_gold.py --interval 1h  --out gold_h1.csv
    python3 tools/fetch_gold.py --symbol SI=F --interval 1h --out silver_h1.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone

CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{sym}?range={rng}&interval={iv}"

#: 야후가 간격별로 허용하는 최대 구간
MAX_RANGE = {"1m": "7d", "2m": "60d", "5m": "60d", "15m": "60d",
             "30m": "60d", "1h": "730d", "1d": "max"}


def fetch(symbol: str, interval: str, rng: str | None = None) -> list[tuple]:
    rng = rng or MAX_RANGE.get(interval, "60d")
    url = CHART.format(sym=urllib.parse.quote(symbol), rng=rng, iv=interval)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as fh:
        doc = json.load(fh)

    err = doc.get("chart", {}).get("error")
    if err:
        raise SystemExit(f"받기 실패: {err}")
    r = doc["chart"]["result"][0]
    ts = r["timestamp"]
    q = r["indicators"]["quote"][0]
    vol = q.get("volume") or [0] * len(ts)

    rows, holes = [], 0
    for i, t in enumerate(ts):
        o, h, l, c = q["open"][i], q["high"][i], q["low"][i], q["close"][i]
        if None in (o, h, l, c):
            holes += 1                    # 세션 휴장·호가 공백
            continue
        rows.append((t, o, h, l, c, vol[i] or 0))
    if holes:
        print(f"  빈 봉 {holes}개 버림 (휴장 구간)", file=sys.stderr)
    return rows


def write(rows: list[tuple], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["time", "open", "high", "low", "close", "volume"])
        w.writerows(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--symbol", default="GC=F", help="기본 GC=F (COMEX 금 선물)")
    ap.add_argument("--interval", default="5m", choices=sorted(MAX_RANGE))
    ap.add_argument("--range", help="기본값은 간격별 최대치")
    ap.add_argument("--out", default="gold.csv")
    a = ap.parse_args()

    rows = fetch(a.symbol, a.interval, a.range)
    if not rows:
        raise SystemExit("받은 봉이 없다")
    write(rows, a.out)
    lo = datetime.fromtimestamp(rows[0][0], timezone.utc)
    hi = datetime.fromtimestamp(rows[-1][0], timezone.utc)
    print(f"{a.out}: {a.symbol} {a.interval} {len(rows):,}봉  "
          f"{lo:%Y-%m-%d} ~ {hi:%Y-%m-%d}")
    print(f"  현재가 {rows[-1][4]:,.2f}")


if __name__ == "__main__":
    main()
