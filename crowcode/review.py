"""복기 엔진 — "왜 안 됐는가" 를 기계적으로 분류한다.

입력
----
  1) 매매 기록 (state/journal.jsonl) — 러너가 남긴 `order` / `closed` 항목
  2) 가격 데이터 (선택) — MT5 에서 내보낸 CSV. 있으면 MAE/MFE 까지 계산한다

하는 일
-------
  · 거래별 판정: 손절만 털렸는가 / 방향이 틀렸는가 / 먹은 걸 토했는가 ...
  · 손실의 편중 확인: 특정 세션·시간대·방향·존 종류에 몰려 있는가
  · 그 편중을 **실제 설정 항목**에 연결한 수정 제안

판정은 힌트지 정답이 아니다. 표본이 적으면 우연이 패턴처럼 보인다.
그래서 리포트는 항상 표본 수를 함께 보여 준다.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable, Sequence

from crowcode.config import CrowConfig
from crowcode.data import Candle, Series
from crowcode.sessions import in_session

# 판정 이름 → 사람이 읽는 설명
VERDICTS: dict[str, str] = {
    "win": "목표 도달",
    "stop_hunted": "손절만 털리고 목표 방향으로 갔다",
    "gave_back": "충분히 갔다가 되돌려줬다",
    "near_miss": "목표 코앞에서 돌아섰다",
    "wrong_way": "방향이 틀렸다 (거의 유리하게 간 적 없음)",
    "chop": "레인지에서 이도 저도 아니게 끝났다",
    "breakeven": "본절 청산",
    "overrun": "손절보다 더 크게 잃었다 (갭·슬리피지)",
    "unknown": "가격 데이터 없어 판정 보류",
}


@dataclass
class ReviewTrade:
    ticket: int
    side: str
    entry: float
    sl: float
    tp: float
    volume: float
    opened_at: datetime | None
    closed_at: datetime | None
    pnl: float
    r: float
    moved_to_be: bool = False
    partial_done: bool = False
    reasons: tuple[str, ...] = ()
    order_type: str = ""
    # --- 가격 데이터가 있을 때만 채워지는 값 ---
    mfe_r: float | None = None       # 최대 유리 이탈 (얼마나 갔었나)
    mae_r: float | None = None       # 최대 불리 이탈 (얼마나 밀렸나)
    tp_after_stop: bool | None = None
    bars_held: int | None = None
    verdict: str = "unknown"

    @property
    def risk(self) -> float:
        return abs(self.entry - self.sl)

    @property
    def won(self) -> bool:
        return self.pnl > 0

    @property
    def session(self) -> str:
        if self.opened_at is None:
            return "-"
        from crowcode.config import LONDON, NEWYORK, ASIA
        w = in_session(self.opened_at, (LONDON, NEWYORK, ASIA))
        return w.name if w else "밖"

    @property
    def hour(self) -> int | None:
        return self.opened_at.hour if self.opened_at else None

    @property
    def poi_kind(self) -> str:
        """진입 존 종류. 파이썬 러너와 MQL5 EA 의 표기를 모두 받는다."""
        for r in self.reasons:
            for marker in ("진입 POI:", "POI:"):
                if marker in r:
                    rest = r.split(marker, 1)[1].strip()
                    return rest.split()[0] if rest else "-"
        return "-"

    @property
    def htf_note(self) -> str:
        for r in self.reasons:
            if r.startswith("HTF"):
                return r
        return "-"


# ----------------------------------------------------------------------
# 1) 기록 읽기
# ----------------------------------------------------------------------
def load_journal(path: str) -> list[dict]:
    if not path or not os.path.exists(path):
        return []
    out: list[dict] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


def _dt(value) -> datetime | None:
    if not value:
        return None
    try:
        ts = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def build_trades(records: Sequence[dict]) -> list[ReviewTrade]:
    """`order` 와 `closed` 를 티켓으로 짝지어 한 건의 거래로 만든다."""
    orders: dict[int, dict] = {}
    for r in records:
        if r.get("kind") == "order":
            t = r.get("result_ticket") or r.get("ticket")
            if t is not None:
                orders[int(t)] = r

    trades: list[ReviewTrade] = []
    for r in records:
        if r.get("kind") != "closed" or r.get("pnl") is None:
            continue
        ticket = int(r["ticket"])
        o = orders.get(ticket, {})
        entry = float(r.get("entry", o.get("entry", 0.0)) or 0.0)
        sl = float(r.get("initial_sl", o.get("sl", 0.0)) or 0.0)
        if entry <= 0 or sl <= 0:
            continue
        trades.append(ReviewTrade(
            ticket=ticket,
            side=o.get("side") or ("buy" if float(r.get("tp", 0)) > entry else "sell"),
            entry=entry, sl=sl,
            tp=float(r.get("tp", o.get("tp", 0.0)) or 0.0),
            volume=float(r.get("volume", o.get("volume", 0.0)) or 0.0),
            opened_at=_dt(r.get("opened_at")) or _dt(o.get("ts")),
            closed_at=_dt(r.get("closed_at")),
            pnl=float(r["pnl"]), r=float(r.get("r", 0.0) or 0.0),
            moved_to_be=bool(r.get("moved_to_be")),
            partial_done=bool(r.get("partial_done")),
            reasons=tuple(o.get("reasons", ())),
            order_type=o.get("type", ""),
        ))
    trades.sort(key=lambda t: t.opened_at or datetime.min.replace(tzinfo=timezone.utc))
    return trades


# ----------------------------------------------------------------------
# 2) 가격으로 살 붙이기
# ----------------------------------------------------------------------
def enrich(trades: Sequence[ReviewTrade], series: Series | None,
           lookahead_factor: int = 3, min_lookahead: int = 30) -> None:
    """MAE/MFE 와 '손절 뒤 목표까지 갔는가' 를 계산한다 (제자리 수정)."""
    if series is None or len(series) == 0:
        return
    candles = list(series)
    times = [c.ts for c in candles]

    for t in trades:
        if t.opened_at is None or t.risk <= 0:
            continue
        i0 = _index_at(times, t.opened_at)
        if i0 is None:
            continue
        i1 = _index_at(times, t.closed_at) if t.closed_at else None
        if i1 is None or i1 <= i0:
            i1 = min(i0 + min_lookahead, len(candles) - 1)
        t.bars_held = i1 - i0

        during = candles[i0:i1 + 1]
        if t.side == "buy":
            mfe = max(c.high for c in during) - t.entry
            mae = t.entry - min(c.low for c in during)
        else:
            mfe = t.entry - min(c.low for c in during)
            mae = max(c.high for c in during) - t.entry
        t.mfe_r = mfe / t.risk
        t.mae_r = mae / t.risk

        # 손절로 끝난 거래가, 그 뒤에 목표까지 갔는가?
        if t.pnl < 0 and t.tp > 0:
            span = max(min_lookahead, (t.bars_held or 0) * lookahead_factor)
            after = candles[i1 + 1: i1 + 1 + span]
            if after:
                t.tp_after_stop = (max(c.high for c in after) >= t.tp) if t.side == "buy" \
                    else (min(c.low for c in after) <= t.tp)


def _index_at(times: Sequence[datetime], ts: datetime | None) -> int | None:
    if ts is None or not times:
        return None
    import bisect
    i = bisect.bisect_right(times, ts) - 1
    return i if 0 <= i < len(times) else None


# ----------------------------------------------------------------------
# 3) 판정
# ----------------------------------------------------------------------
def classify(t: ReviewTrade, cfg: CrowConfig) -> str:
    if t.mfe_r is None:
        if t.pnl > 0:
            return "win"
        if t.r <= -1.3:
            return "overrun"                 # 가격 데이터 없이도 판정 가능하다
        return "breakeven" if abs(t.r) < 0.15 else "unknown"

    if t.pnl > 0:
        return "win"
    # 손절이 지켜졌다면 -1R 근처여야 한다. 크게 넘었다면 전략이 아니라
    # 체결 쪽 문제다 (주말 갭, 지표 급변, SL 미설정, 슬리피지).
    if t.r <= -1.3:
        return "overrun"
    if t.tp_after_stop:
        return "stop_hunted"
    if t.mfe_r >= cfg.breakeven_at_r:
        return "gave_back"
    if t.mfe_r >= cfg.target_rr * 0.7:
        return "near_miss"
    if t.mfe_r < 0.5:
        return "wrong_way"
    if abs(t.r) < 0.15:
        return "breakeven"
    return "chop"


# ----------------------------------------------------------------------
# 4) 집계와 제안
# ----------------------------------------------------------------------
@dataclass
class Finding:
    weight: int          # 몇 건이 이 패턴에 해당하는가
    title: str
    detail: str
    knob: str = ""       # 관련된 설정 항목

    def line(self) -> str:
        out = [f"  [{self.weight}건] {self.title}", f"         {self.detail}"]
        if self.knob:
            out.append(f"         → 설정: {self.knob}")
        return "\n".join(out)


@dataclass
class Diagnosis:
    trades: list[ReviewTrade]
    cfg: CrowConfig
    findings: list[Finding] = field(default_factory=list)

    @property
    def losses(self) -> list[ReviewTrade]:
        return [t for t in self.trades if t.pnl < 0]

    @property
    def wins(self) -> list[ReviewTrade]:
        return [t for t in self.trades if t.pnl > 0]

    @property
    def flats(self) -> list[ReviewTrade]:
        return [t for t in self.trades if t.pnl == 0]

    @property
    def subjects(self) -> list[ReviewTrade]:
        """복기 대상 = 이기지 못한 거래.

        본절 청산도 포함한다. 2.8R 까지 갔다가 본절로 끝난 거래는
        손실은 아니지만 분명히 고칠 거리가 있다.
        """
        return [t for t in self.trades if t.pnl <= 0]

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for t in self.trades:
            out[t.verdict] = out.get(t.verdict, 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def _skew(d: "Diagnosis", key, label: str, knob: str,
          min_bad: int = 3, bad_rate: float = 0.7, gap: float = 0.25) -> list[Finding]:
    """어떤 구간의 비승리 비율이 나머지보다 뚜렷하게 높은지 찾는다."""
    buckets = _bucket(d.trades, key)
    if len(buckets) < 2:
        return []                       # 비교 대상이 없으면 편중을 말할 수 없다
    out: list[Finding] = []
    for name, items in buckets.items():
        bad = [t for t in items if t.pnl <= 0]
        if len(bad) < min_bad:
            continue
        rate = len(bad) / len(items)
        rest = [t for t in d.trades if key(t) != name]
        rest_rate = len([t for t in rest if t.pnl <= 0]) / len(rest) if rest else 0.0
        if rate >= bad_rate and rate - rest_rate >= gap:
            out.append(Finding(
                len(bad), f"{label} '{name}' 에서 유독 안 된다",
                f"{name}: {len(items)}건 중 {len(bad)}건이 비승리({rate:.0%}), "
                f"나머지는 {rest_rate:.0%}. 여기만 빼도 결과가 달라진다.",
                knob))
    return out


def _bucket(items: Sequence[ReviewTrade], key) -> dict:
    out: dict = {}
    for t in items:
        out.setdefault(key(t), []).append(t)
    return out


def diagnose(trades: Sequence[ReviewTrade], cfg: CrowConfig) -> Diagnosis:
    for t in trades:
        t.verdict = classify(t, cfg)
    d = Diagnosis(list(trades), cfg)
    losses = d.subjects
    n = len(losses)
    if n == 0:
        return d

    counts = d.counts()
    third = max(2, n // 3)

    def add(k: str, title: str, detail: str, knob: str = "") -> None:
        c = counts.get(k, 0)
        if c >= third:
            d.findings.append(Finding(c, title, detail, knob))

    add("overrun", "손절 폭보다 더 크게 잃었다",
        "손절이 지켜졌다면 -1R 근처여야 한다. 이건 전략이 아니라 체결 문제다 — "
        "주말 갭, 지표 급변, SL 미설정, 또는 슬리피지. 해당 거래의 시각을 "
        "먼저 확인할 것.",
        "news 목록 보강 / trade_on_friday_close=False 확인 / 브로커 슬리피지")
    add("stop_hunted", "손절만 털리고 목표 방향으로 갔다",
        "진입 논리는 맞았는데 손절 자리가 노이즈 안에 있었다. "
        "스윕 극점 바깥 여유를 늘리면 같은 셋업이 살아난다.",
        f"sl_buffer_atr (현재 {cfg.sl_buffer_atr}) → {cfg.sl_buffer_atr + 0.2:.2f} 로 올려 재검증")
    add("wrong_way", "방향 자체가 틀렸다",
        "유리하게 간 적이 거의 없다면 진입 트리거가 아니라 상위 프레임 판단이 문제다. "
        "HTF 를 한 단계 올리거나 Wyckoff 국면 일치를 필수로 둘 것.",
        f"htf (현재 {cfg.htf}) 상향 / require_choch, require_liquidity_sweep 유지 확인")
    add("gave_back", "충분히 갔다가 되돌려줬다",
        f"{cfg.breakeven_at_r}R 이상 갔는데 이익 없이 끝났다. 본절 이동이 이르거나 "
        "분할 청산 시점이 멀다.",
        f"breakeven_at_r {cfg.breakeven_at_r} → {max(1.0, cfg.breakeven_at_r - 0.5):.1f}, "
        f"partial_at_r {cfg.partial_at_r} → {max(1.5, cfg.partial_at_r - 1):.1f}")
    add("near_miss", "목표 코앞에서 돌아섰다",
        "목표가 시장이 주는 것보다 멀다. 반대편 유동성까지 못 가는 구간이다.",
        f"target_rr {cfg.target_rr} → {max(cfg.min_rr, cfg.target_rr - 1):.1f}")
    add("chop", "레인지에서 이도 저도 아니게 끝났다",
        "추세가 없는 구간에 진입하고 있다. Wyckoff 국면이 A/B 인 곳은 걸러야 한다.",
        "max_entry_distance_atr 축소 / HTF Wyckoff 편향 필수화")

    # --- 편중 확인 -----------------------------------------------------
    # 단순히 "손실이 여기 많다" 가 아니라 **나머지와 비교해서** 유독 나쁜
    # 구간을 찾는다. 거래가 원래 런던에 몰려 있으면 손실도 런던에 많은 게
    # 당연하므로, 그걸 발견이라고 부르면 안 된다.
    for label, key, knob in (
        ("세션", lambda t: t.session, "sessions"),
        ("방향", lambda t: t.side, ""),
        ("진입 존", lambda t: t.poi_kind, "poi_types"),
        ("시간대", lambda t: f"{t.hour:02d}시" if t.hour is not None else "-",
         "sessions / news"),
    ):
        d.findings.extend(_skew(d, key, label, knob))


    # --- 목표 대비 실제 도달 폭 ---------------------------------------
    mfes = [t.mfe_r for t in d.trades if t.mfe_r is not None]
    if len(mfes) >= 5:
        avg = sum(mfes) / len(mfes)
        if avg < cfg.target_rr * 0.6:
            d.findings.append(Finding(
                len(mfes), f"평균 최대 도달폭이 {avg:.2f}R 에 그친다",
                f"목표 {cfg.target_rr}R 은 이 구간의 움직임보다 멀다. "
                "목표를 낮추면 승률이 오르고 기대값이 바뀐다.",
                f"target_rr {cfg.target_rr} → {max(cfg.min_rr, round(avg, 1))}"))

    d.findings.sort(key=lambda f: -f.weight)
    return d


# ----------------------------------------------------------------------
# 5) 텍스트 리포트
# ----------------------------------------------------------------------
def text_report(d: Diagnosis, title: str = "복기") -> str:
    t = d.trades
    n = len(t)
    lines = ["=" * 66, f" {title} — 거래 {n}건", "=" * 66]
    if n == 0:
        lines.append(" 기록된 거래가 없다. --journal 경로를 확인할 것.")
        lines.append("=" * 66)
        return "\n".join(lines)

    wins, losses = d.wins, d.losses
    total_r = sum(x.r for x in t)
    total_pnl = sum(x.pnl for x in t)
    lines += [
        f" 승/패/본절   : {len(wins)} / {len(losses)} / {len(d.flats)}"
        f"   (승률 {len(wins)/n:.1%})",
        f" 총 R         : {total_r:+.2f}R    손익 {total_pnl:+,.2f}",
        f" 평균          : {total_r/n:+.3f}R / 거래",
        "-" * 66,
        " 판정 분포",
    ]
    for k, c in d.counts().items():
        lines.append(f"   {VERDICTS.get(k, k):<28} {c:>3}건")

    lines += ["-" * 66, " 거래별"]
    lines.append(f"   {'시각':<17}{'방향':<5}{'R':>7}  {'최대도달':>8}{'최대역행':>9}  판정")
    for x in t:
        when = x.opened_at.strftime("%m-%d %H:%M") if x.opened_at else "-"
        mfe = f"{x.mfe_r:+.2f}R" if x.mfe_r is not None else "   -  "
        mae = f"{x.mae_r:+.2f}R" if x.mae_r is not None else "   -  "
        lines.append(f"   {when:<17}{x.side:<5}{x.r:>+7.2f}  {mfe:>8}{mae:>9}  "
                     f"{VERDICTS.get(x.verdict, x.verdict)}")

    lines += ["-" * 66, " 확인할 것"]
    if not d.findings:
        lines.append("   뚜렷한 편중이 없다. 표본이 더 쌓일 때까지 설정을 건드리지 말 것.")
    else:
        for f in d.findings:
            lines.append(f.line())
    lines += [
        "-" * 66,
        f" 표본 {n}건. 20건 미만이면 패턴이 아니라 우연일 수 있다 —",
        " 설정을 바꾸기 전에 같은 구간을 백테스트로 다시 돌려서 확인할 것:",
        "   python3 -m crowcode backtest --csv <데이터> --set sl_buffer_atr=0.5 ...",
        "=" * 66,
    ]
    return "\n".join(lines)


# ----------------------------------------------------------------------
# 6) HTML 리포트 — 차트를 직접 보면서 복기하기 위한 것
# ----------------------------------------------------------------------
def _svg_chart(t: ReviewTrade, candles: Sequence[Candle], i0: int, i1: int,
               before: int = 40, after: int = 25, w: int = 880, h: int = 260) -> str:
    """거래 전후 캔들을 SVG 로 그린다. 진입·손절·목표선을 함께 표시한다."""
    lo = max(0, i0 - before)
    hi = min(len(candles) - 1, (i1 if i1 > i0 else i0) + after)
    view = candles[lo:hi + 1]
    if len(view) < 3:
        return "<p>구간 캔들이 부족하다.</p>"

    pad_l, pad_r, pad_t, pad_b = 8, 62, 12, 18
    pw, ph = w - pad_l - pad_r, h - pad_t - pad_b

    levels = [t.entry, t.sl] + ([t.tp] if t.tp else [])
    hi_p = max(max(c.high for c in view), max(levels))
    lo_p = min(min(c.low for c in view), min(levels))
    span = (hi_p - lo_p) or 1.0
    hi_p += span * 0.05
    lo_p -= span * 0.05
    span = hi_p - lo_p

    step = pw / len(view)
    body = max(1.2, step * 0.62)

    def x(i: int) -> float:
        return pad_l + i * step + step / 2

    def y(p: float) -> float:
        return pad_t + (hi_p - p) / span * ph

    parts: list[str] = [
        f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" '
        f'aria-label="{t.side} 거래 차트">']

    # 진입~청산 구간 음영
    a, b = x(i0 - lo) - step / 2, x(min(i1, hi) - lo) + step / 2
    parts.append(f'<rect x="{a:.1f}" y="{pad_t}" width="{max(1.0, b - a):.1f}" '
                 f'height="{ph}" class="span"/>')

    # 수평선 (손절/진입/목표)
    for price, cls, label in ((t.sl, "sl", "SL"), (t.entry, "entry", "진입"),
                              (t.tp, "tp", "TP")):
        if not price:
            continue
        yy = y(price)
        parts.append(f'<line x1="{pad_l}" y1="{yy:.1f}" x2="{w - pad_r}" y2="{yy:.1f}" '
                     f'class="lvl {cls}"/>')
        parts.append(f'<text x="{w - pad_r + 4}" y="{yy + 3.5:.1f}" class="lbl {cls}">'
                     f'{label} {price:.2f}</text>')

    # 캔들
    for i, c in enumerate(view):
        cx = x(i)
        cls = "up" if c.close >= c.open else "dn"
        parts.append(f'<line x1="{cx:.1f}" y1="{y(c.high):.1f}" x2="{cx:.1f}" '
                     f'y2="{y(c.low):.1f}" class="wick {cls}"/>')
        top, bot = y(max(c.open, c.close)), y(min(c.open, c.close))
        parts.append(f'<rect x="{cx - body / 2:.1f}" y="{top:.1f}" width="{body:.1f}" '
                     f'height="{max(1.0, bot - top):.1f}" class="body {cls}"/>')

    # 진입 화살표
    ex, ey = x(i0 - lo), y(t.entry)
    tri = f"{ex:.1f},{ey - 7:.1f} {ex - 5:.1f},{ey + 2:.1f} {ex + 5:.1f},{ey + 2:.1f}" \
        if t.side == "buy" else \
        f"{ex:.1f},{ey + 7:.1f} {ex - 5:.1f},{ey - 2:.1f} {ex + 5:.1f},{ey - 2:.1f}"
    parts.append(f'<polygon points="{tri}" class="mark {t.side}"/>')
    parts.append("</svg>")
    return "".join(parts)


_CSS = """
:root{--bg:#fbfbfa;--fg:#1d1c1a;--muted:#6b6862;--card:#fff;--line:#e5e2dc;
 --up:#2f855a;--dn:#c0392b;--entry:#2b6cb0;--sl:#c0392b;--tp:#2f855a;--span:rgba(43,108,176,.07)}
:root:not([data-theme="light"]){}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
 --bg:#171614;--fg:#eceae5;--muted:#9b968d;--card:#211f1c;--line:#33302b;
 --up:#4ade80;--dn:#f87171;--entry:#7dabf8;--sl:#f87171;--tp:#4ade80;--span:rgba(125,171,248,.10)}}
:root[data-theme="dark"]{--bg:#171614;--fg:#eceae5;--muted:#9b968d;--card:#211f1c;--line:#33302b;
 --up:#4ade80;--dn:#f87171;--entry:#7dabf8;--sl:#f87171;--tp:#4ade80;--span:rgba(125,171,248,.10)}
*{box-sizing:border-box}
body{background:var(--bg);color:var(--fg);margin:0;padding:24px 16px;
 font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI","Noto Sans KR",sans-serif}
.wrap{max-width:960px;margin:0 auto}
h1{font-size:23px;margin:0 0 4px} h2{font-size:17px;margin:32px 0 10px}
.sub{color:var(--muted);margin:0 0 24px;font-size:14px}
.kpis{display:flex;flex-wrap:wrap;gap:10px;margin-bottom:20px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:9px;
 padding:10px 14px;min-width:110px}
.kpi b{display:block;font-size:19px;font-variant-numeric:tabular-nums}
.kpi span{color:var(--muted);font-size:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:11px;
 padding:14px 16px;margin-bottom:14px}
.head{display:flex;flex-wrap:wrap;gap:10px;align-items:baseline;margin-bottom:8px}
.tag{font-size:12px;padding:2px 8px;border-radius:99px;border:1px solid var(--line);color:var(--muted)}
.tag.loss{color:var(--dn);border-color:var(--dn)} .tag.win{color:var(--up);border-color:var(--up)}
.meta{color:var(--muted);font-size:13px;margin:8px 0 0}
.meta code{font-size:12px}
ul.why{margin:8px 0 0;padding-left:18px;color:var(--muted);font-size:13.5px}
.wick{stroke-width:1} .wick.up{stroke:var(--up)} .wick.dn{stroke:var(--dn)}
.body.up{fill:var(--up)} .body.dn{fill:var(--dn)}
.lvl{stroke-width:1;stroke-dasharray:4 3} .lvl.entry{stroke:var(--entry)}
.lvl.sl{stroke:var(--sl)} .lvl.tp{stroke:var(--tp)}
.lbl{font-size:10px;font-variant-numeric:tabular-nums} .lbl.entry{fill:var(--entry)}
.lbl.sl{fill:var(--sl)} .lbl.tp{fill:var(--tp)}
.span{fill:var(--span)} .mark.buy{fill:var(--up)} .mark.sell{fill:var(--dn)}
.finding{border-left:3px solid var(--entry);padding-left:12px;margin:12px 0}
.finding b{display:block} .finding p{margin:3px 0;color:var(--muted);font-size:13.5px}
.knob{font-family:ui-monospace,Menlo,monospace;font-size:12.5px;color:var(--fg)}
.note{color:var(--muted);font-size:13px;border-top:1px solid var(--line);
 margin-top:28px;padding-top:14px}
table{border-collapse:collapse;width:100%;font-size:13.5px}
th,td{text-align:right;padding:6px 8px;border-bottom:1px solid var(--line);
 font-variant-numeric:tabular-nums}
th:first-child,td:first-child,th:nth-child(2),td:nth-child(2),
th:last-child,td:last-child{text-align:left}
.scroll{overflow-x:auto}
"""


def _esc(text: str) -> str:
    return (str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def html_report(d: Diagnosis, series: Series | None, title: str = "복기",
                max_charts: int = 12) -> str:
    t = d.trades
    n = len(t)
    wins, losses = len(d.wins), len(d.losses)
    total_r = sum(x.r for x in t)
    candles = list(series) if series is not None else []
    times = [c.ts for c in candles]

    out = [f"<title>{_esc(title)}</title>", f"<style>{_CSS}</style>",
           '<div class="wrap">', f"<h1>{_esc(title)}</h1>"]
    out.append(f'<p class="sub">거래 {n}건 · 표본이 20건 미만이면 패턴이 아니라 '
               f'우연일 수 있다.</p>')

    out.append('<div class="kpis">')
    for label, value in (("거래", f"{n}"),
                         ("승/패/본절", f"{wins} / {losses} / {len(d.flats)}"),
                         ("승률", f"{(wins / n if n else 0):.0%}"),
                         ("총 R", f"{total_r:+.2f}"),
                         ("평균", f"{(total_r / n if n else 0):+.2f}R")):
        out.append(f'<div class="kpi"><b>{value}</b><span>{label}</span></div>')
    out.append("</div>")

    # --- 확인할 것 ---------------------------------------------------
    out.append("<h2>확인할 것</h2>")
    if not d.findings:
        out.append('<p class="sub">뚜렷한 편중이 없다. 표본이 더 쌓일 때까지 '
                   '설정을 건드리지 말 것.</p>')
    for f in d.findings:
        out.append('<div class="finding">'
                   f'<b>[{f.weight}건] {_esc(f.title)}</b>'
                   f'<p>{_esc(f.detail)}</p>'
                   + (f'<p class="knob">→ {_esc(f.knob)}</p>' if f.knob else "")
                   + "</div>")

    # --- 거래 표 -----------------------------------------------------
    out.append("<h2>거래 목록</h2><div class='scroll'><table>")
    out.append("<tr><th>시각</th><th>방향</th><th>R</th><th>최대도달</th>"
               "<th>최대역행</th><th>판정</th></tr>")
    for x in t:
        when = x.opened_at.strftime("%m-%d %H:%M") if x.opened_at else "-"
        mfe = f"{x.mfe_r:+.2f}R" if x.mfe_r is not None else "-"
        mae = f"{x.mae_r:+.2f}R" if x.mae_r is not None else "-"
        out.append(f"<tr><td>{when}</td><td>{x.side}</td><td>{x.r:+.2f}</td>"
                   f"<td>{mfe}</td><td>{mae}</td>"
                   f"<td>{_esc(VERDICTS.get(x.verdict, x.verdict))}</td></tr>")
    out.append("</table></div>")

    # --- 차트 (손실 우선) --------------------------------------------
    if candles:
        picks = d.subjects + d.wins
        picks = picks[:max_charts]
        out.append("<h2>차트</h2>")
        for x in picks:
            i0 = _index_at(times, x.opened_at)
            if i0 is None:
                continue
            i1 = _index_at(times, x.closed_at) or i0
            cls = "loss" if x.pnl < 0 else "win"
            when = x.opened_at.strftime("%Y-%m-%d %H:%M") if x.opened_at else "-"
            out.append('<div class="card"><div class="head">'
                       f"<b>{when} · {x.side.upper()}</b>"
                       f'<span class="tag {cls}">{x.r:+.2f}R</span>'
                       f'<span class="tag">{_esc(VERDICTS.get(x.verdict, x.verdict))}</span>'
                       f'<span class="tag">{_esc(x.poi_kind)}</span></div>')
            out.append(_svg_chart(x, candles, i0, i1))
            bits = [f"진입 {x.entry:.2f}", f"손절 {x.sl:.2f}", f"목표 {x.tp:.2f}"]
            if x.mfe_r is not None:
                bits.append(f"최대도달 {x.mfe_r:+.2f}R")
            if x.bars_held is not None:
                bits.append(f"보유 {x.bars_held}봉")
            if x.moved_to_be:
                bits.append("본절 이동함")
            out.append(f'<p class="meta">{" · ".join(bits)}</p>')
            if x.reasons:
                out.append("<ul class='why'>"
                           + "".join(f"<li>{_esc(r)}</li>" for r in x.reasons)
                           + "</ul>")
            out.append("</div>")

    out.append('<p class="note">판정은 힌트지 정답이 아니다. '
               '설정을 바꾸기 전에 같은 구간을 백테스트로 다시 돌려 확인할 것 — '
               '<span class="knob">crowcode backtest --csv &lt;데이터&gt; '
               '--set sl_buffer_atr=0.5</span></p>')
    out.append("</div>")
    return "\n".join(out)


# ----------------------------------------------------------------------
# 7) 백테스트 결과도 같은 방식으로 복기할 수 있게
# ----------------------------------------------------------------------
def from_backtest(result) -> list[ReviewTrade]:
    """`Backtester` 의 거래를 복기 대상으로 변환한다.

    실전 기록이 쌓이기 전에도 같은 복기 도구를 쓸 수 있어야
    "무엇을 보고 무엇을 고칠지" 를 미리 익힐 수 있다.
    """
    out: list[ReviewTrade] = []
    for tr in result.trades:
        s = tr.signal
        out.append(ReviewTrade(
            ticket=0, side=s.side, entry=s.entry, sl=s.sl, tp=s.tp,
            volume=s.lots, opened_at=tr.opened_at, closed_at=tr.closed_at,
            pnl=tr.pnl, r=tr.r_multiple,
            moved_to_be=any("본절" in n for n in tr.notes),
            partial_done=any("분할" in n for n in tr.notes),
            reasons=s.reasons, order_type=s.order_type,
        ))
    return out
