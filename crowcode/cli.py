"""명령줄 인터페이스.

  python -m crowcode signal   --csv data.csv --preset scalp --balance 1000
  python -m crowcode backtest --csv data.csv --preset scalp --balance 1000
  python -m crowcode rules    --preset scalp
  python -m crowcode split    --capital 5000
  python -m crowcode demo
  python -m crowcode live     --symbol XAUUSD --preset scalp            # 드라이런
  python -m crowcode live     --symbol XAUUSD --preset scalp --live     # 실주문
  python -m crowcode live     --paper --bars 25000                      # 단말 없이 시뮬레이션
  python -m crowcode status                                             # 잠금 상태 확인
  python -m crowcode review   --journal state/journal.jsonl --csv d.csv --html r.html
  python -m crowcode release  --note "손절 버퍼 0.3→0.5 로 수정"
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from crowcode.config import PRESETS, preset
from crowcode.gold import CANONICAL, movers_table, parse_news, preflight, resolve_symbol
from crowcode.data import load_csv, synthetic
from crowcode.risk import RiskState, split_capital
from crowcode.strategy import CrowStrategy
from crowcode.backtest import Backtester


def _apply_pips(args, cfg):
    """--pip-size / --sl-pips 로 손절 범위를 핍 단위로 지정한다."""
    size = getattr(args, "pip_size", None)
    band = getattr(args, "sl_pips", None)
    if size is None and not band:
        return cfg
    if size is not None:
        cfg = cfg.with_(pip_size=size)
    if band:
        try:
            lo, hi = (float(x) for x in str(band).replace("-", ":").split(":"))
        except ValueError:
            raise SystemExit(f"--sl-pips 형식이 잘못됨: {band} (예: 20:25)")
        cfg = cfg.with_sl_pips(lo, hi)
    print(f"손절 범위: {cfg.sl_label()}   목표 1:{cfg.target_rr:g} → "
          f"${cfg.min_sl_price * cfg.target_rr:g}~${cfg.max_sl_price * cfg.target_rr:g}")
    for w in cfg.validate():
        print("  " + w)
    return cfg


def _overrides(args, cfg):
    """--set key=value 로 설정을 덮어쓴다 (타입은 원본 필드에서 추론)."""
    pairs = getattr(args, "set", None) or []
    cfg = _apply_pips(args, cfg)
    if not pairs:
        return cfg
    changes = {}
    for item in pairs:
        if "=" not in item:
            raise SystemExit(f"--set 형식이 잘못됨: {item} (key=value)")
        key, raw = item.split("=", 1)
        key = key.strip()
        if not hasattr(cfg, key):
            raise SystemExit(f"알 수 없는 설정 항목: {key}")
        cur = getattr(cfg, key)
        try:
            if isinstance(cur, bool):
                changes[key] = raw.strip().lower() in ("1", "true", "yes", "on")
            elif isinstance(cur, int):
                changes[key] = int(raw)
            elif isinstance(cur, float):
                changes[key] = float(raw)
            else:
                changes[key] = raw
        except ValueError:
            raise SystemExit(f"{key} 값을 해석할 수 없음: {raw}")
    print("설정 덮어쓰기:", ", ".join(f"{k}={v}" for k, v in changes.items()))
    return cfg.with_(**changes)


def _news(args):
    spec = getattr(args, "news", None)
    return parse_news(spec) if spec else []


def _series(args):
    if args.csv:
        return load_csv(args.csv, args.symbol, args.timeframe)
    return synthetic(args.bars, minutes=args.base_minutes)


def cmd_signal(args) -> int:
    cfg = _overrides(args, preset(args.preset))
    s = _series(args)
    strat = CrowStrategy(cfg, args.symbol, _news(args))
    sig = strat.evaluate(s, args.balance, RiskState(balance=args.balance))
    if sig is None:
        last = strat.rejections[-1] if strat.rejections else None
        print("시그널 없음" + (f" — {last.rule}: {last.detail}" if last else ""))
        return 1
    print(json.dumps(sig.to_dict(), ensure_ascii=False, indent=2) if args.json else sig.pretty())
    return 0


def cmd_backtest(args) -> int:
    cfg = _overrides(args, preset(args.preset))
    s = _series(args)
    bt = Backtester(cfg, args.balance, args.symbol, spread=args.spread,
                    swap_per_lot_night=args.swap, news=_news(args),
                    warmup=args.warmup, eval_every=args.eval_every)
    res = bt.run(s)
    print(res.report())
    if args.trades:
        for t in res.trades:
            print(f"  {t.opened_at:%Y-%m-%d %H:%M} {t.signal.side:<4} "
                  f"{t.signal.entry:.3f} → {t.exit_price:.3f}  "
                  f"{t.outcome:<10} {t.r_multiple:+.2f}R  {t.pnl:+.2f}")

    if args.review or args.review_html:
        from crowcode import review as rv

        trades = rv.from_backtest(res)
        rv.enrich(trades, s)
        diag = rv.diagnose(trades, cfg)
        if args.review:
            print()
            print(rv.text_report(diag, "백테스트 복기"))
        if args.review_html:
            _write_html(args.review_html, rv.html_report(diag, s, "백테스트 복기"))
    return 0


def _write_html(path: str, content: str) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    print(f"HTML 리포트: {path}")


def cmd_rules(args) -> int:
    cfg = _overrides(args, preset(args.preset))
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
    "intraday": dict(bars=9000, minutes=5, warmup=600, eval_every=3),
    "scalp": dict(bars=25000, minutes=1, warmup=800, eval_every=5),
    "highrisk": dict(bars=25000, minutes=1, warmup=800, eval_every=5),
}


def cmd_live(args) -> int:
    from crowcode.mt5 import Journal, LiveConfig, LiveRunner, PaperBroker

    cfg = _overrides(args, preset(args.preset))
    live = LiveConfig(
        symbol=args.symbol, preset_name=args.preset, base_timeframe=args.timeframe,
        bars=args.bars, magic=args.magic, deviation=args.deviation,
        dry_run=not args.live, state_path=args.state, poll_seconds=args.poll,
        max_spread_points=args.max_spread,
    )
    journal = Journal(args.journal, echo=True)
    news = _news(args)

    if args.paper:
        broker = PaperBroker(_series(args), balance=args.balance, start_index=args.warmup)
        runner = LiveRunner(broker, live, cfg, journal, news)
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
        live.symbol = _resolve(broker, args.symbol)
        print(preflight(cfg, broker.symbol(live.symbol), broker.account().balance,
                        broker.tick(live.symbol).spread).report())
        runner = LiveRunner(broker, live, cfg, journal, news)
        runner.run(max_iterations=1 if args.once else None)
    finally:
        broker.shutdown()
    return 0


def _resolve(broker, requested: str) -> str:
    """브로커마다 다른 금 심볼 이름(XAUUSD / XAUUSD.m / GOLD ...)을 맞춰 준다."""
    names = broker.list_symbols()
    if requested and requested.lower() != "auto" and requested in names:
        return requested
    found = resolve_symbol(names)
    if found is None:
        raise RuntimeError(
            f"금 심볼을 찾지 못했습니다. 마켓워치에서 정확한 이름을 확인해 "
            f"--symbol 로 지정하세요. (요청: {requested})")
    if requested and requested.lower() != "auto" and found != requested:
        print(f"심볼 자동 보정: {requested} → {found}")
    return found


def cmd_preflight(args) -> int:
    cfg = _overrides(args, preset(args.preset))
    if args.paper:
        from crowcode.mt5.paper import XAUUSD
        print(preflight(cfg, XAUUSD, args.balance, args.spread).report())
        return 0

    from crowcode.mt5.terminal import Mt5Broker

    broker = Mt5Broker(login=args.login, password=args.password, server=args.server,
                       terminal_path=args.terminal_path,
                       server_utc_offset=args.server_offset)
    try:
        sym = _resolve(broker, args.symbol)
        acct = broker.account()
        rep = preflight(cfg, broker.symbol(sym), acct.balance, broker.tick(sym).spread)
        print(rep.report())
        return 1 if rep.failed else 0
    finally:
        broker.shutdown()


def cmd_risk(args) -> int:
    from crowcode.riskmath import report

    cfg = _overrides(args, preset(args.preset))
    print(report(cfg, weeks=args.weeks, trades_per_week=args.per_week, paths=args.paths))
    return 0


def cmd_status(args) -> int:
    from crowcode.mt5.lockout import LockoutStore

    store = LockoutStore(args.lockout)
    cur = store.current()
    if cur is None:
        print("잠금 없음 — 매매 가능 상태.")
    else:
        print(cur.summary())
    hist = store.history()
    if hist:
        print(f"\n지난 잠금 {len(hist)}건")
        for h in hist[-5:]:
            print(f"  {h.trading_day}  손실 {h.loss_pct:.2f}%  "
                  f"해제 {h.released_at or '-'}  메모: {h.released_note or '-'}")
    return 1 if store.is_locked() else 0


def cmd_release(args) -> int:
    from crowcode.mt5.lockout import LockoutStore

    if not args.note or len(args.note.strip()) < 5:
        print("해제하려면 --note 에 복기 내용을 남겨야 한다.\n"
              '예: --release --note "손실 5건 중 4건이 stop_hunted → sl_buffer_atr 0.3→0.5"')
        return 1
    store = LockoutStore(args.lockout)
    released = store.release(args.note.strip())
    if released is None:
        print("활성 잠금이 없다. 해제할 것이 없음.")
        return 0
    print(f"잠금 해제됨 ({released.trading_day} 발생분).")
    print(f"  메모: {released.released_note}")
    print("다음 매매부터 정상 동작한다.")
    return 0


def cmd_review(args) -> int:
    from crowcode import review as rv

    cfg = _overrides(args, preset(args.preset))
    records = rv.load_journal(args.journal)
    trades = rv.build_trades(records)
    if not trades:
        print(f"복기할 거래가 없다: {args.journal}")
        print("러너가 'closed' 기록을 남긴 뒤에 다시 실행할 것.")
        return 1

    series = load_csv(args.csv, args.symbol, args.timeframe) if args.csv else None
    if series is None:
        print("주의: --csv 가 없어 최대도달/최대역행을 계산하지 못한다. "
              "판정 정확도가 크게 떨어진다.\n")
    rv.enrich(trades, series)
    diag = rv.diagnose(trades, cfg)
    print(rv.text_report(diag))
    if args.html:
        _write_html(args.html, rv.html_report(diag, series))
    return 0


def cmd_gold(args) -> int:
    from crowcode.gold import REFERENCE

    print("XAUUSD 기준 수치")
    print(f"  1랏 = {REFERENCE.contract_size:.0f} oz  →  $1 움직임 = "
          f"${REFERENCE.money_per_dollar_per_lot:.0f} / 랏")
    print(f"  0.01랏이면 $1 움직임 = $1  (호가 단위 {REFERENCE.point}, MT5 포인트 1 = $0.01)")
    print(f"  전형적 스프레드 {REFERENCE.typical_spread_points} 포인트 "
          f"(${REFERENCE.typical_spread_points * REFERENCE.point:.2f})")
    print()
    print("프리셋별 손절 폭 가드와 최소 필요 자본")
    from crowcode.gold import min_viable_balance
    for name, c in PRESETS.items():
        lo = min_viable_balance(c, c.min_sl_price)
        hi = min_viable_balance(c, c.max_sl_price)
        print(f"  {name:<9} {c.htf}>{c.mtf}>{c.ltf:<4} 리스크 {c.risk_pct:>4.1f}%  "
              f"손절 ${c.min_sl_price:>5.2f}~${c.max_sl_price:<6.2f} "
              f"필요 자본 {lo:>8,.0f} ~ {hi:>9,.0f}")
    print()
    print(movers_table())
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
    common.add_argument("--preset", default="intraday", choices=sorted(PRESETS))
    common.add_argument("--csv", help="time,open,high,low,close[,volume] CSV")
    common.add_argument("--symbol", default=CANONICAL,
                        help="금 심볼. 'auto' 로 두면 마켓워치에서 찾아낸다")
    common.add_argument("--timeframe", default="M1", help="CSV 의 기준 타임프레임")
    common.add_argument("--balance", type=float, default=1000.0)
    common.add_argument("--bars", type=int, default=6000, help="CSV 미지정 시 합성 봉 수")
    common.add_argument("--base-minutes", type=int, default=1, dest="base_minutes",
                        help="합성 데이터의 기준 봉 길이(분)")
    common.add_argument("--sl-pips", dest="sl_pips", metavar="LO:HI",
                        help="손절 범위를 핍으로 지정. 예: --sl-pips 20:25")
    common.add_argument("--pip-size", type=float, dest="pip_size",
                        help="1핍이 몇 달러인가. 금은 브로커마다 1.0 / 0.1 / 0.01")
    common.add_argument("--set", action="append", metavar="KEY=VALUE",
                        help="설정 덮어쓰기. 예: --set sl_buffer_atr=0.5 --set target_rr=2.5")
    common.add_argument("--lockout", default="state/lockout.json",
                        help="서킷브레이커 잠금 파일")
    common.add_argument("--news", default="",
                        help="차단할 지표 시각(GMT). 예: \"CPI@2026-09-11 12:30, 2026-10-02 12:30\"")

    p = argparse.ArgumentParser("crowcode", description="Crow Concept 트레이딩 규칙 엔진",
                                parents=[common])
    sub = p.add_subparsers(dest="cmd", required=True)

    s1 = sub.add_parser("signal", help="최신 봉 기준 시그널 산출", parents=[common])
    s1.add_argument("--json", action="store_true")
    s1.set_defaults(func=cmd_signal)

    s2 = sub.add_parser("backtest", help="규칙 그대로 백테스트", parents=[common])
    s2.add_argument("--spread", type=float, default=0.25, help="스프레드(금 달러)")
    s2.add_argument("--swap", type=float, default=0.0,
                    help="1랏 1박당 스왑(계좌 통화). 금은 보통 음수, 예: -12")
    s2.add_argument("--warmup", type=int, default=600)
    s2.add_argument("--eval-every", type=int, default=5, dest="eval_every")
    s2.add_argument("--trades", action="store_true")
    s2.add_argument("--review", action="store_true", help="끝나고 복기 리포트도 출력")
    s2.add_argument("--review-html", dest="review_html", help="복기 리포트를 HTML 로 저장")
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

    s7 = sub.add_parser("preflight", help="브로커·계좌·프리셋 조합 사전 점검", parents=[common])
    s7.add_argument("--paper", action="store_true", help="표준 XAUUSD 사양으로 점검")
    s7.add_argument("--spread", type=float, default=0.25, help="--paper 시 가정 스프레드(달러)")
    s7.add_argument("--login", type=int)
    s7.add_argument("--password")
    s7.add_argument("--server")
    s7.add_argument("--terminal-path", dest="terminal_path")
    s7.add_argument("--server-offset", type=float, dest="server_offset")
    s7.set_defaults(func=cmd_preflight)

    s8 = sub.add_parser("gold", help="XAUUSD 기준 수치와 필요 자본", parents=[common])
    s8.set_defaults(func=cmd_gold)

    s12 = sub.add_parser("risk", help="리스크 사다리와 승률별 결과 계산", parents=[common])
    s12.add_argument("--weeks", type=int, default=52, help="시뮬레이션 주 수")
    s12.add_argument("--per-week", type=float, default=3.0, dest="per_week",
                     help="주당 거래 수 (이 엔진은 주 3~5건 정도 나온다)")
    s12.add_argument("--paths", type=int, default=2000, help="시뮬레이션 경로 수")
    s12.set_defaults(func=cmd_risk)

    s9 = sub.add_parser("status", help="서킷브레이커 잠금 상태", parents=[common])
    s9.set_defaults(func=cmd_status)

    s10 = sub.add_parser("release", help="복기 후 잠금 해제", parents=[common])
    s10.add_argument("--note", default="", help="무엇을 확인했고 무엇을 고쳤는지 (필수)")
    s10.set_defaults(func=cmd_release)

    s11 = sub.add_parser("review", help="매매 기록 복기", parents=[common])
    s11.add_argument("--journal", default="state/journal.jsonl")
    s11.add_argument("--html", help="HTML 리포트 저장 경로")
    s11.set_defaults(func=cmd_review)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
