"""ICT 명령줄.

  python3 -m ict study    --csv XAUUSD_M5.csv          # 2018~현재 개념 실측
  python3 -m ict backtest --csv XAUUSD_M5.csv          # 2022 모델 백테스트
  python3 -m ict signal   --csv XAUUSD_M5.csv          # 최신 봉 셋업
  python3 -m ict setups   --csv XAUUSD_M5.csv --limit 20
"""

from __future__ import annotations

import argparse
import sys

from crowcode.data import Series, load_csv, resample, synthetic
from ict.backtest import run
from ict.models import Config, find_setup, scan
from ict.study import full_report


def _series(args) -> Series:
    if args.csv:
        s = load_csv(args.csv, args.symbol, args.timeframe)
        if args.resample:
            s = resample(s, args.resample)
        if args.since:
            from datetime import datetime, timezone
            cut = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
            s = Series([c for c in s if c.ts >= cut], s.symbol, s.timeframe)
        return s
    print("--csv 가 없어 합성 데이터로 실행합니다 (배관 확인용, 결과는 근거가 아님).\n",
          file=sys.stderr)
    return synthetic(args.bars, minutes=args.base_minutes)


def _config(args) -> Config:
    cfg = Config()
    for item in (args.set or []):
        if "=" not in item:
            raise SystemExit(f"--set 형식 오류: {item}")
        k, v = item.split("=", 1)
        if not hasattr(cfg, k):
            raise SystemExit(f"알 수 없는 항목: {k}")
        cur = getattr(cfg, k)
        if isinstance(cur, bool):
            val = v.strip().lower() in ("1", "true", "yes", "on")
        elif isinstance(cur, int):
            val = int(v)
        elif isinstance(cur, float):
            val = float(v)
        elif isinstance(cur, tuple):
            val = tuple(x.strip() for x in v.split(","))
        else:
            val = v
        cfg = Config(**{**vars(cfg), k: val})
    return cfg


def cmd_study(args) -> int:
    s = _series(args)
    if len(s) < 500:
        print("데이터가 너무 짧습니다.", file=sys.stderr)
        return 1
    print(full_report(s, args.title or f"{s.symbol or 'XAUUSD'} ICT 실측"))
    return 0


def cmd_backtest(args) -> int:
    s = _series(args)
    res = run(s, _config(args), spread=args.spread, max_hold=args.max_hold)
    print(res.report(args.title or "ICT 2022 모델"))
    if args.trades:
        print()
        for t in res.trades:
            print(f"  {t.setup.ts:%Y-%m-%d %H:%M} {t.setup.side:<4} "
                  f"{t.setup.entry:>9.2f} → {t.exit_price:>9.2f}  "
                  f"{t.outcome:<7} {t.r:+6.2f}R  {t.setup.killzone}")
    return 0


def cmd_signal(args) -> int:
    s = _series(args)
    setup = find_setup(list(s), len(s) - 1, _config(args))
    if setup is None:
        print("셋업 없음")
        return 1
    print(setup.describe())
    return 0


def cmd_setups(args) -> int:
    s = _series(args)
    found = scan(list(s), _config(args))
    print(f"셋업 {len(found)}건")
    for x in found[-args.limit:]:
        print()
        print(x.describe())
    return 0


def main(argv=None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--csv", help="time,open,high,low,close[,volume] (UTC)")
    common.add_argument("--symbol", default="XAUUSD")
    common.add_argument("--timeframe", default="M5", help="CSV 의 기준 타임프레임")
    common.add_argument("--resample", help="상위 프레임으로 변환 (예: M15)")
    common.add_argument("--since", help="시작일 (예: 2018-01-01)")
    common.add_argument("--bars", type=int, default=30000)
    common.add_argument("--base-minutes", type=int, default=5, dest="base_minutes")
    common.add_argument("--title")
    common.add_argument("--set", action="append", metavar="KEY=VALUE")

    p = argparse.ArgumentParser("ict", description="ICT 개념 엔진 (XAUUSD)", parents=[common])
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("study", help="ICT 개념을 데이터로 검증", parents=[common])
    a.set_defaults(func=cmd_study)

    b = sub.add_parser("backtest", help="2022 모델 백테스트", parents=[common])
    b.add_argument("--spread", type=float, default=0.25)
    b.add_argument("--max-hold", type=int, default=288, dest="max_hold")
    b.add_argument("--trades", action="store_true")
    b.set_defaults(func=cmd_backtest)

    c = sub.add_parser("signal", help="최신 봉 셋업", parents=[common])
    c.set_defaults(func=cmd_signal)

    d = sub.add_parser("setups", help="구간 전체 셋업 목록", parents=[common])
    d.add_argument("--limit", type=int, default=10)
    d.set_defaults(func=cmd_setups)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
