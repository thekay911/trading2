"""명령줄 인터페이스.

  python -m crowcode signal   --csv data.csv --preset scalp --balance 1000
  python -m crowcode backtest --csv data.csv --preset scalp --balance 1000
  python -m crowcode rules    --preset scalp
  python -m crowcode split    --capital 5000
  python -m crowcode demo
  python -m crowcode live     --symbol XAUUSD --preset scalp            # 드라이런
  python -m crowcode live     --symbol XAUUSD --preset scalp --live     # 실주문
  python -m crowcode live     --paper --bars 25000                      # 단말 없이 시뮬레이션
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


def cmd_live(args) -> int:
    from crowcode.mt5 import Journal, LiveConfig, LiveRunner, PaperBroker

    cfg = preset(args.preset)
    live = LiveConfig(
        symbol=args.symbol, preset_name=args.preset, base_timeframe=args.timeframe,
        bars=args.bars, magic=args.magic, deviation=args.deviation,
        dry_run=not args.live, state_path=args.state, poll_seconds=args.poll,
        max_spread_points=args.max_spread,
    )
    journal = Journal(args.journal, echo=True)

    if args.paper:
        broker = PaperBroker(_series(args), balance=args.balance, start_index=args.warmup)
        runner = LiveRunner(broker, live, cfg, journal)
        n = 0
        while broker.advance():
            if runner.step():
                n += 1
        print(f"\n페이퍼 실행 완료 — 시그널 {n}건, 최종 잔고 {broker.account().balance:,.2f}")
        return 0

    from crowcode.mt5.terminal import Mt5Broker

    if args.live:
        print("!! 실주문 모드입니다. 계좌에 실제 주문이 나갑니다. !!\n"
              f"   심볼={args.symbol} 프리셋={args.preset} 매직={args.magic} "
              f"리스크={cfg.risk_pct}%/거래", flush=True)
    broker = Mt5Broker(login=args.login, password=args.password, server=args.server,
                       terminal_path=args.terminal_path,
                       server_utc_offset=args.server_offset)
    try:
        runner = LiveRunner(broker, live, cfg, journal)
        runner.run(max_iterations=1 if args.once else None)
    finally:
        broker.shutdown()
    return 0


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

    s6 = sub.add_parser("live", help="MT5 단말에 붙어 실행 (기본은 드라이런)", parents=[common])
    s6.add_argument("--live", action="store_true",
                    help="실제 주문 전송 (기본은 드라이런: 주문을 만들되 보내지 않음)")
    s6.add_argument("--paper", action="store_true", help="단말 없이 시뮬레이션 브로커로 실행")
    s6.add_argument("--magic", type=int, default=700911, help="이 봇의 주문 식별 번호")
    s6.add_argument("--deviation", type=int, default=20, help="시장가 슬리피지 허용(포인트)")
    s6.add_argument("--max-spread", type=int, default=60, dest="max_spread",
                    help="이보다 스프레드가 넓으면 진입 안 함(포인트)")
    s6.add_argument("--poll", type=int, default=5, help="폴링 간격(초)")
    s6.add_argument("--once", action="store_true", help="1회만 평가하고 종료")
    s6.add_argument("--journal", default="state/journal.jsonl")
    s6.add_argument("--state", default="state/crowcode_state.json")
    s6.add_argument("--warmup", type=int, default=800, help="--paper 시작 인덱스")
    s6.add_argument("--login", type=int)
    s6.add_argument("--password")
    s6.add_argument("--server")
    s6.add_argument("--terminal-path", dest="terminal_path")
    s6.add_argument("--server-offset", type=float, dest="server_offset",
                    help="서버시간 - UTC (시간). 생략하면 자동 추정")
    s6.set_defaults(func=cmd_live)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
