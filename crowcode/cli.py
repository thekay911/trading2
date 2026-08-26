"""명령줄 인터페이스.

  python -m crowcode signal   --csv data.csv --preset scalp --balance 1000
  python -m crowcode backtest --csv data.csv --preset scalp --balance 1000
  python -m crowcode rules    --preset scalp
  python -m crowcode split    --capital 5000
  python -m crowcode demo
"""

from __future__ import annotations

import argparse
import json
import sys

from crowcode.config import PRESETS, preset
from crowcode.data import load_csv, synthetic
from crowcode.risk import RiskState, split_capital
from crowcode.strategy import CrowStrategy
from crowcode.backtest import Backtester


def _series(args):
    if args.csv:
        return load_csv(args.csv, args.symbol, args.timeframe)
    return synthetic(args.bars, minutes=args.base_minutes)


def cmd_signal(args) -> int:
    cfg = preset(args.preset)
    s = _series(args)
    strat = CrowStrategy(cfg, args.symbol)
    sig = strat.evaluate(s, args.balance, RiskState(balance=args.balance))
    if sig is None:
        last = strat.rejections[-1] if strat.rejections else None
        print("시그널 없음" + (f" — {last.rule}: {last.detail}" if last else ""))
        return 1
    print(json.dumps(sig.to_dict(), ensure_ascii=False, indent=2) if args.json else sig.pretty())
    return 0


def cmd_backtest(args) -> int:
    cfg = preset(args.preset)
    s = _series(args)
    bt = Backtester(cfg, args.balance, args.symbol, spread=args.spread,
                    warmup=args.warmup, eval_every=args.eval_every)
    res = bt.run(s)
    print(res.report())
    if args.trades:
        for t in res.trades:
            print(f"  {t.opened_at:%Y-%m-%d %H:%M} {t.signal.side:<4} "
                  f"{t.signal.entry:.3f} → {t.exit_price:.3f}  "
                  f"{t.outcome:<10} {t.r_multiple:+.2f}R  {t.pnl:+.2f}")
    return 0


def cmd_rules(args) -> int:
    cfg = preset(args.preset)
    print(f"[{cfg.name}] 프리셋 규칙")
    for k, v in sorted(vars(cfg).items()):
        print(f"  {k:<28} {v}")
    return 0


def cmd_split(args) -> int:
    cfg = preset(args.preset)
    sp = split_capital(args.capital, cfg)
    print("계좌 분리 (스윙 / 스캘핑 / 고위험):")
    for k, v in sp.as_dict().items():
        print(f"  {k:<10} {v:,.2f}")
    return 0


# 프리셋별 데모 데이터: 스윙은 D1 편향이 필요하므로 더 긴 기간을 M15 로 만든다.
_DEMO_DATA = {
    "swing": dict(bars=9000, minutes=15, warmup=600, eval_every=2),
    "scalp": dict(bars=25000, minutes=1, warmup=800, eval_every=5),
    "highrisk": dict(bars=25000, minutes=1, warmup=800, eval_every=5),
}


def cmd_demo(args) -> int:
    for name in PRESETS:
        d = _DEMO_DATA[name]
        s = synthetic(d["bars"], minutes=d["minutes"])
        res = Backtester(preset(name), args.balance, "XAUUSD", spread=0.20,
                         warmup=d["warmup"], eval_every=d["eval_every"]).run(s)
        print(f"\n### 프리셋: {name}  ({d['bars']}봉 / M{d['minutes']} 합성 데이터)")
        print(res.report())
    print("\n주의: 합성 데이터는 무작위 보행이라 우위(edge)가 없다. "
          "여기 숫자는 '규칙이 그대로 작동하는지'를 보는 용도이지 성과 근거가 아니다.")
    return 0


def main(argv=None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--preset", default="scalp", choices=sorted(PRESETS))
    common.add_argument("--csv", help="time,open,high,low,close[,volume] CSV")
    common.add_argument("--symbol", default="XAUUSD")
    common.add_argument("--timeframe", default="M1", help="CSV 의 기준 타임프레임")
    common.add_argument("--balance", type=float, default=1000.0)
    common.add_argument("--bars", type=int, default=6000, help="CSV 미지정 시 합성 봉 수")
    common.add_argument("--base-minutes", type=int, default=1, dest="base_minutes",
                        help="합성 데이터의 기준 봉 길이(분)")

    p = argparse.ArgumentParser("crowcode", description="Crow Concept 트레이딩 규칙 엔진",
                                parents=[common])
    sub = p.add_subparsers(dest="cmd", required=True)

    s1 = sub.add_parser("signal", help="최신 봉 기준 시그널 산출", parents=[common])
    s1.add_argument("--json", action="store_true")
    s1.set_defaults(func=cmd_signal)

    s2 = sub.add_parser("backtest", help="규칙 그대로 백테스트", parents=[common])
    s2.add_argument("--spread", type=float, default=0.20)
    s2.add_argument("--warmup", type=int, default=600)
    s2.add_argument("--eval-every", type=int, default=5, dest="eval_every")
    s2.add_argument("--trades", action="store_true")
    s2.set_defaults(func=cmd_backtest)

    s3 = sub.add_parser("rules", help="프리셋 파라미터 출력", parents=[common])
    s3.set_defaults(func=cmd_rules)

    s4 = sub.add_parser("split", help="계좌 3분할 계산", parents=[common])
    s4.add_argument("--capital", type=float, default=5000.0)
    s4.set_defaults(func=cmd_split)

    s5 = sub.add_parser("demo", help="합성 데이터로 전 프리셋 실행", parents=[common])
    s5.set_defaults(func=cmd_demo)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
