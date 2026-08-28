"""SNR 과 H1 확인봉 주장 검증."""

from __future__ import annotations

import statistics
from typing import Sequence

from crowcode.data import Candle
from ict.ea_sim import Bars, rolling_atr
from snr.levels import Level, find, find_scaled, track


def _atr_series(bars: Sequence[Candle], period: int = 20) -> list[float]:
    """봉마다의 ATR.

    21년 전체에서 ATR 을 하나만 뽑으면 안 된다 — 금은 $384 에서 시작해
    $5,000 을 넘었다. 전역 ATR 로 임계값을 잡으면 초반 구간이 통째로
    걸러지고(레벨 67개), 대조군까지 0.1% 로 무너진다. 실제로 그렇게 됐다.
    """
    return rolling_atr(Bars(bars), period)


def _pct(a: int, b: int) -> str:
    return f"{a / b:.1%}" if b else "-"


def reaction(candles: Sequence[Candle], j: int, side: str, r_atr: float,
             horizon: int = 12) -> bool:
    """레벨에 닿은 뒤 기대 방향으로 ATR 만큼 갔는가."""
    if j + horizon >= len(candles):
        return False
    base = candles[j].close
    seg = candles[j + 1:j + 1 + horizon]
    if side == "support":
        return max(c.high for c in seg) - base >= r_atr
    return base - min(c.low for c in seg) >= r_atr


def run(candles: Sequence[Candle], horizon: int = 12) -> str:
    bars = list(candles)
    atrs = _atr_series(bars)
    levels = find_scaled(bars, atrs, body_mult=0.3)
    track(bars, levels)

    lines = ["=" * 74,
             f" SNR / H1 주장 검증 — 봉 {len(bars):,}개, 레벨 {len(levels):,}개",
             "=" * 74]

    # 1. 신선한 레벨이 더 잘 반응하는가
    buckets: dict[str, list[int]] = {}
    for lv in levels:
        for j in range(lv.index + 1, min(lv.index + 600, len(bars))):
            c = bars[j]
            if not (c.low <= lv.price <= c.high):
                continue
            n = sum(1 for k in range(lv.index + 1, j)
                    if bars[k].low <= lv.price <= bars[k].high)
            key = "1회차(신선)" if n == 0 else "2회차" if n == 1 else "3회차 이상"
            buckets.setdefault(key, []).append(
                1 if reaction(bars, j, lv.side, atrs[j], horizon) else 0)
            if n >= 3:
                break
    lines += ["-" * 74,
              " 1. 신선한 레벨이 더 잘 반응한다 (닿은 뒤 ATR 만큼 되돌림)"]
    for k in ("1회차(신선)", "2회차", "3회차 이상"):
        v = buckets.get(k, [])
        if len(v) >= 100:
            lines.append(f"   {k:<12}{_pct(sum(v), len(v)):>7}  ({len(v):,}회)")
    lines.append("   → 회차가 늘수록 뚜렷하게 떨어져야 '신선도' 개념이 성립한다")

    # 2. A형 vs V형
    ka: dict[str, list[int]] = {}
    for lv in levels:
        for j in range(lv.index + 1, min(lv.index + 300, len(bars))):
            c = bars[j]
            if c.low <= lv.price <= c.high:
                ka.setdefault(lv.kind, []).append(
                    1 if reaction(bars, j, lv.side, atrs[j], horizon) else 0)
                break
    lines += ["-" * 74, " 2. A형과 V형이 다른가"]
    for k in ("A", "V"):
        v = ka.get(k, [])
        if len(v) >= 100:
            lines.append(f"   {k}형        {_pct(sum(v), len(v)):>7}  ({len(v):,}회)")

    # 3. 대조군 — 아무 가격에서나 같은 반응이 나오는 비율
    ctrl: list[int] = []
    step = max(1, len(bars) // 4000)
    for j in range(200, len(bars) - horizon - 1, step):
        ctrl.append(1 if reaction(bars, j, "support", atrs[j], horizon) else 0)
        ctrl.append(1 if reaction(bars, j, "resistance", atrs[j], horizon) else 0)
    lines += ["-" * 74,
              " 3. 대조군: 아무 봉에서나 ATR 만큼 움직이는 비율",
              f"   아무 가격    {_pct(sum(ctrl), len(ctrl)):>7}  ({len(ctrl):,}회)",
              "   → 위 숫자들이 이걸 넘지 못하면 레벨이 아무 의미가 없다는 뜻이다"]

    lines.append("=" * 74)
    return "\n".join(lines)


def h1_candle_claim(candles: Sequence[Candle], rr: float = 3.0,
                    stop_atr: float = 1.0, horizon: int = 48) -> str:
    """'H1 반전 확인봉 + 공급/수요 = 승률 70~80%, RR 1:3' 주장 검증.

    자료가 백테스트로 검증했다고 단언하는 숫자다. 재현되는지 본다.
    """
    from crowcode.data import Series, resample
    h1 = list(resample(Series(list(candles), "XAUUSD", "M15"), "H1"))
    if len(h1) < 300:
        return "H1 표본이 부족하다"
    atrs = _atr_series(h1)

    wins = losses = 0
    for i in range(50, len(h1) - horizon):
        prev, cur = h1[i - 1], h1[i]
        # 반전 확인봉: 직전 봉과 방향이 반대이고 몸통이 충분히 크다
        up_prev = prev.close > prev.open
        up_cur = cur.close > cur.open
        if up_prev == up_cur:
            continue
        a = atrs[i]
        if a <= 0 or abs(cur.close - cur.open) < a * 0.5:
            continue
        # 추세 방향(20봉 기울기)과 같은 쪽만 — 자료의 '역추세 금지'
        trend_up = h1[i].close > h1[i - 20].close
        if up_cur != trend_up:
            continue
        entry = cur.close
        risk = a * stop_atr
        stop = entry - risk if up_cur else entry + risk
        target = entry + risk * rr if up_cur else entry - risk * rr
        for j in range(i + 1, min(i + 1 + horizon, len(h1))):
            c = h1[j]
            hit_s = c.low <= stop if up_cur else c.high >= stop
            hit_t = c.high >= target if up_cur else c.low <= target
            if hit_s:
                losses += 1
                break
            if hit_t:
                wins += 1
                break
    n = wins + losses
    if n == 0:
        return "거래 없음"
    wr = wins / n
    exp = wr * rr - (1 - wr)
    return ("\n".join([
        "=" * 74,
        " 'H1 반전 확인봉 + 추세 = 승률 70~80%, RR 1:3' 주장 검증",
        "=" * 74,
        f"   거래 {n:,}건   승률 {wr:.1%}   기대값 {exp:+.3f}R",
        f"   자료의 주장: 70~80%",
        f"   RR 1:{rr:g} 에서 손익분기 승률은 {100 / (1 + rr):.1f}% 다",
        "=" * 74]))
