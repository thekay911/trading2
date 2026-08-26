"""리스크 산수 — "이 설정이 말이 되는가" 를 숫자로 확인한다.

거래당 몇 %, RR 얼마 같은 값은 혼자 보면 의미가 없다. 승률과 묶어야
기대값이 나오고, 연속 손절 확률과 묶어야 하루 한도가 얼마나 자주 걸리는지
나온다.

여기 있는 건 전부 **가정 위의 계산**이다. 승률을 넣어야 답이 나오는데
그 승률은 실제 데이터로 백테스트해야 알 수 있다. 이 모듈은 "승률이 X 라면
이런 그림이 된다" 까지만 말한다.

**고정 랏 기준**으로 계산한다. 복리를 넣으면 수백 거래 뒤에는 어떤 설정이든
숫자가 폭발해서 좋아 보이는 착시만 남는다. 여기서 보려는 것은 수익률이
아니라 낙폭과 파산 확률이므로 고정 랏이 맞다.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from crowcode.config import CrowConfig


# ----------------------------------------------------------------------
# 해석적 계산
# ----------------------------------------------------------------------
def breakeven_win_rate(rr: float) -> float:
    """이 RR 에서 본전이 되는 승률. 1:3 이면 25%."""
    return 1.0 / (1.0 + rr) if rr > 0 else 1.0


def expectancy_r(win_rate: float, rr: float) -> float:
    """거래당 기대값 (R 단위)."""
    return win_rate * rr - (1.0 - win_rate)


def prob_streak(win_rate: float, length: int) -> float:
    """연속 `length` 회 손절 확률 (독립 가정)."""
    return (1.0 - win_rate) ** length


def trades_to_limit(risk_pct: float, limit_pct: float) -> int:
    """연속 몇 번 지면 그 한도에 닿는가 (고정 랏 기준)."""
    if risk_pct <= 0:
        return 0
    return max(1, math.ceil(limit_pct / risk_pct))


def expected_worst_streak(win_rate: float, trades: int) -> float:
    """`trades` 번 하는 동안 예상되는 최장 연패 길이 (근사).

    사람들이 가장 과소평가하는 숫자다. 승률 35% 로 150거래면
    평균적으로 7연패가 한 번은 나온다.
    """
    q = 1.0 - win_rate
    if trades <= 0 or q <= 0 or q >= 1:
        return 0.0
    return math.log(trades * win_rate) / math.log(1.0 / q)


# ----------------------------------------------------------------------
# 몬테카를로
# ----------------------------------------------------------------------
@dataclass
class SimResult:
    win_rate: float
    paths: int
    weeks: int
    total_r: list[float] = field(default_factory=list)
    max_dd_pct: list[float] = field(default_factory=list)
    worst_streak: list[int] = field(default_factory=list)
    trades: list[int] = field(default_factory=list)
    circuit_hits: int = 0
    ruined: int = 0

    @staticmethod
    def _q(values, q: float) -> float:
        if not values:
            return 0.0
        v = sorted(values)
        return float(v[min(len(v) - 1, max(0, int(q * (len(v) - 1))))])

    def q(self, values, quantile: float) -> float:
        return self._q(values, quantile)

    @property
    def median_r(self) -> float:
        return self._q(self.total_r, 0.5)

    @property
    def median_trades(self) -> float:
        return self._q(self.trades, 0.5)

    @property
    def p_circuit(self) -> float:
        return self.circuit_hits / self.paths if self.paths else 0.0

    @property
    def p_ruin(self) -> float:
        return self.ruined / self.paths if self.paths else 0.0

    @property
    def p_negative(self) -> float:
        if not self.total_r:
            return 0.0
        return sum(1 for r in self.total_r if r < 0) / len(self.total_r)


def simulate(
    cfg: CrowConfig,
    win_rate: float,
    weeks: int = 52,
    trades_per_week: float = 3.0,
    paths: int = 2000,
    seed: int = 11,
    ruin_pct: float = 50.0,
) -> SimResult:
    """설정의 리스크 규칙(연속손절·일일한도·서킷)을 그대로 적용해 돌린다.

    단순화한 부분 — 전부 **실제보다 낙관적인** 방향이다:
      · 거래 결과가 서로 독립. 실제 시장은 연속성이 있어 연패가 더 길다.
      · 승리는 정확히 target_rr, 패배는 정확히 -1R.
        갭·슬리피지로 -1R 을 넘는 경우가 빠져 있다.
      · 승률이 기간 내내 일정하다고 본다.
      · 서킷에 걸려도 다음 날 복기를 마치고 재개한다고 본다.
    """
    rnd = random.Random(seed)
    rr = cfg.target_rr
    risk = cfg.risk_pct                       # % of 초기 잔고 (고정 랏)
    slots = max(1, cfg.max_trades_per_day)
    p_slot = min(1.0, (trades_per_week / 5.0) / slots)
    res = SimResult(win_rate=win_rate, paths=paths, weeks=weeks)

    for _ in range(paths):
        equity = 0.0                          # 누적 손익 (%)
        peak = 0.0
        max_dd = 0.0
        n_trades = 0
        streak = 0
        worst_streak = 0
        hit_circuit = False
        locked_until_reviewed = False

        for _ in range(weeks * 5):
            if locked_until_reviewed:
                locked_until_reviewed = False   # 복기하고 다음 날 재개
            day_pnl = 0.0
            day_streak = 0

            for _ in range(slots):
                if rnd.random() >= p_slot:
                    continue                    # 그 슬롯에는 셋업이 없었다
                if day_streak >= cfg.max_consecutive_losses:
                    break
                if -day_pnl >= cfg.max_daily_loss_pct:
                    break
                if cfg.hard_stop_loss_pct > 0 and -day_pnl >= cfg.hard_stop_loss_pct:
                    hit_circuit = True
                    locked_until_reviewed = True
                    break

                n_trades += 1
                if rnd.random() < win_rate:
                    day_pnl += risk * rr
                    streak = 0
                else:
                    day_pnl -= risk
                    day_streak += 1
                    streak += 1
                    worst_streak = max(worst_streak, streak)

                equity += (risk * rr) if streak == 0 else -risk
                peak = max(peak, equity)
                max_dd = max(max_dd, peak - equity)

            if cfg.hard_stop_loss_pct > 0 and -day_pnl >= cfg.hard_stop_loss_pct:
                hit_circuit = True
                locked_until_reviewed = True

        res.total_r.append(equity / risk if risk else 0.0)
        res.max_dd_pct.append(max_dd)
        res.worst_streak.append(worst_streak)
        res.trades.append(n_trades)
        if hit_circuit:
            res.circuit_hits += 1
        if max_dd >= ruin_pct:
            res.ruined += 1
    return res


# ----------------------------------------------------------------------
# 리포트
# ----------------------------------------------------------------------
def observations(cfg: CrowConfig, sim: SimResult, trades_per_week: float,
                 weeks: int) -> list[str]:
    """설정과 시뮬레이션에서 바로 읽히는 사실들."""
    out: list[str] = []

    # 서킷브레이커가 실제로 걸릴 수 있는가?
    day_floor = min(cfg.max_daily_loss_pct,
                    cfg.risk_pct * cfg.max_consecutive_losses)
    if cfg.hard_stop_loss_pct > day_floor:
        out.append(
            f"서킷({cfg.hard_stop_loss_pct:g}%)은 정상 매매로는 거의 안 걸린다 — "
            f"연속 {cfg.max_consecutive_losses}회 손절({cfg.risk_pct * cfg.max_consecutive_losses:g}%)이나 "
            f"일일 한도({cfg.max_daily_loss_pct:g}%)가 먼저 멈추기 때문이다. "
            "즉 서킷은 갭·슬리피지처럼 손절이 안 지켜진 날을 잡는 안전망이다.")
    else:
        out.append(f"서킷({cfg.hard_stop_loss_pct:g}%)이 일일 한도보다 먼저 걸린다. "
                   "일일 한도가 사실상 무의미하다.")

    # 연패는 사다리가 아니라 낙폭으로 온다
    streak = sim.q(sim.worst_streak, 0.5)
    if streak >= 1:
        out.append(
            f"승률 35% 기준 1년에 최장 {streak:.0f}연패가 예상된다 "
            f"(중앙값). 하루 {cfg.max_consecutive_losses}회 제한 때문에 "
            f"이 연패는 여러 날에 걸쳐 나뉘고, 그래서 낙폭이 "
            f"{sim.q(sim.max_dd_pct, 0.5):.0f}% 까지 간다 — 하루가 아니라 몇 주에 걸쳐서.")

    # 거래 기회가 얼마나 남는가
    expected = trades_per_week * weeks
    actual = sim.median_trades
    if expected > 0 and actual < expected * 0.9:
        out.append(
            f"연속 손절 규칙 때문에 연 {expected:.0f}건 중 실제로는 "
            f"{actual:.0f}건만 실행된다 ({actual / expected:.0%}). "
            "규칙이 기회를 깎는 만큼 표본이 늦게 쌓인다.")

    # 최소 자본
    if cfg.min_sl_price > 0:
        from crowcode.gold import min_viable_balance
        lo = min_viable_balance(cfg, cfg.min_sl_price)
        hi = min_viable_balance(cfg, cfg.max_sl_price)
        out.append(f"리스크 {cfg.risk_pct:g}% 기준 최소 자본은 "
                   f"{lo:,.0f}~{hi:,.0f} 이다 (손절 폭에 따라).")
    return out



def ladder(cfg: CrowConfig) -> list[str]:
    r = cfg.risk_pct
    return [
        f"   거래당            -{r:g}%",
        f"   연속 {cfg.max_consecutive_losses}회 손절     "
        f"-{r * cfg.max_consecutive_losses:g}%   → 그날 매매 종료",
        f"   일일 한도         -{cfg.max_daily_loss_pct:g}%   → 그날 매매 종료",
        f"   서킷브레이커      -{cfg.hard_stop_loss_pct:g}%  → 잠금 (복기 전까지 재개 불가)",
    ]


def report(cfg: CrowConfig, win_rates=(0.25, 0.30, 0.35, 0.40, 0.50),
           weeks: int = 52, trades_per_week: float = 3.0, paths: int = 2000) -> str:
    rr = cfg.target_rr
    be = breakeven_win_rate(rr)
    lines = [
        "=" * 74,
        f" 리스크 점검 — 거래당 {cfg.risk_pct:g}%, RR 1:{rr:g}  [{cfg.name}]",
        "=" * 74,
        " 손실 사다리",
        *ladder(cfg),
        "-" * 74,
        f" 손익분기 승률  {be:.0%}   ← 1:{rr:g} 에서 본전이 되는 승률",
        f" 가정            주 {trades_per_week:g}건, {weeks}주 "
        f"(≈{int(trades_per_week * weeks)}거래), 고정 랏, 경로 {paths}개",
        "-" * 74,
        f"   {'승률':<5}{'기대값':>9}{'연간 R':>10}{'수익률':>9}"
        f"{'최대낙폭':>10}{'최장연패':>9}{'서킷':>7}{'손실마감':>9}",
    ]
    for wr in win_rates:
        sim = simulate(cfg, wr, weeks=weeks, trades_per_week=trades_per_week, paths=paths)
        lines.append(
            f"   {wr:<5.0%}{expectancy_r(wr, rr):>+8.2f}R{sim.median_r:>+9.0f}R"
            f"{sim.median_r * cfg.risk_pct:>+8.0f}%"
            f"{sim.q(sim.max_dd_pct, 0.5):>9.1f}%"
            f"{sim.q(sim.worst_streak, 0.5):>8.0f}회"
            f"{sim.p_circuit:>7.0%}{sim.p_negative:>9.0%}")

    # --- 설정 자체에서 바로 읽히는 관찰 -------------------------------
    base = simulate(cfg, 0.35, weeks=weeks, trades_per_week=trades_per_week, paths=paths)
    lines += ["-" * 74, " 관찰"]
    for note in observations(cfg, base, trades_per_week, weeks):
        lines.append(f"   · {note}")

    # --- 리스크 % 를 바꾸면 낙폭이 어떻게 되는가 ------------------------
    lines += ["-" * 74,
              " 거래당 리스크만 바꿔 보면 (승률 35% 가정)",
              f"   {'리스크':<8}{'연간 수익률':>12}{'최대낙폭':>11}{'연속2패 시':>12}"]
    for r in (1.0, 1.5, 2.0, 3.0):
        alt = cfg.with_(risk_pct=r,
                        max_daily_loss_pct=max(cfg.max_daily_loss_pct,
                                               r * cfg.max_consecutive_losses))
        sim = simulate(alt, 0.35, weeks=weeks, trades_per_week=trades_per_week, paths=paths)
        mark = "  ← 현재" if abs(r - cfg.risk_pct) < 1e-9 else ""
        lines.append(f"   {r:<8g}{sim.median_r * r:>+11.0f}%"
                     f"{sim.q(sim.max_dd_pct, 0.5):>10.1f}%"
                     f"{-r * cfg.max_consecutive_losses:>11.1f}%{mark}")

    lines += [
        "-" * 74,
        " 읽는 법",
        f"   · 승률 {be:.0%} 아래면 아무리 오래 해도 잃는다. 1:{rr:g} 의 의미가 이것이다.",
        "   · 수익률은 고정 랏 기준(누적 R × 거래당 리스크%)이다. 복리가 아니다.",
        "   · 최대낙폭·최장연패는 중앙값이다. 절반의 경로는 이보다 더 나쁘다.",
        f"   · '손실마감' 은 1년을 마이너스로 끝낸 경로 비율이다.",
        "-" * 74,
        " 이 숫자를 믿지 말 것",
        "   승률을 안다는 가정 위에 있다. 실제 승률은 진짜 XAUUSD 데이터로",
        "   백테스트해야 나온다. 게다가 이 모형은 거래를 독립으로 보고 손실을",
        "   항상 -1R 로 두므로 (갭·슬리피지 제외) 실제보다 낙관적이다.",
        "=" * 74,
    ]
    return "\n".join(lines)
