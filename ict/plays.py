"""모델별 청산 계획.

ICT 모델은 시간 스케일이 서로 다르다. 유다 스윙은 세션의 조작이 풀리는
동안 가고, 터틀 수프는 가짜 돌파가 되돌아가는 동안 간다. 전부 같은 목표,
같은 보유시간으로 묶으면 각각의 성격이 사라진다.

값의 출처
---------
실제 XAUUSD **2004-06 ~ 2026-01** 을 두 시간대로 각각 돌렸다.

  M30  248,912봉 · 셋업 8,241건
  M15  494,235봉 · 셋업 12,916건

같은 시장 같은 기간이지만 봉이 다르면 셋업 집합도 달라진다. **두 시간대에서
모두 살아남는가** 를 채택 기준으로 삼았다. 값은 격자 1등이 아니라 주변 칸이
같이 양수인 고원의 중앙이다.

왜 이렇게까지 하는가
--------------------
처음에는 60일 표본으로 값을 잡았다. 21년으로 늘리자 결론이 통째로 뒤집혔고
(그때 1등이던 터틀수프가 -69R), M30 에서 좋았던 유다스윙(+0.435R, 212거래)은
M15 에서 6개 구간 중 5개가 음수였다. **표본이 얇으면 격자 1등은 잡음이다.**

M15 모델 x 4년구간 기대값 — 이 표가 채택을 결정했다
--------------------------------------------------
                 2004   2008   2012   2016   2020   2024
    Unicorn      +0.19  +0.30  +0.16  +0.25  +0.32  +0.39   <- 6/6 양수
    TurtleSoup   +0.05  +0.19  -0.04  +0.04  +0.25  +0.12   <- 5/6 양수
    TJR          +0.33  +0.13  +0.12  -0.02  -0.02  -0.05   <- 최근 3구간 음수
    ICT2022      +0.14  +0.02  +0.00  +0.05  -0.03  -0.00
    OTE          +0.06  +0.06  -0.03  -0.11  -0.05  -0.07
    SilverBullet +0.11  -0.08  -0.00  +0.13  -0.06  -0.03
    JudasSwing   -0.15  -0.04  -0.03  +0.13  -0.15  -0.10

검증한 것
---------
· 매수·매도 양방향 모두 양수 — 금의 21년 상승추세를 탄 게 아니다
  (추세 편승이면 매도가 음수여야 한다)
· 두 시간대에서 격자 모양이 같다: 1.0~1.5R 은 음수, 2.5R 위로 양수,
  4~5R 에서 최대, 보유 4시간 위로는 평평
· 본전 이동은 6개 모델 전부에서 손해였다. 기대값을 낮추고 낙폭까지
  키운다 — 금은 진입가로 깊게 되돌린 뒤 가는 일이 잦아서, 본전 스톱이
  결국 이길 거래를 먼저 털어낸다. 그래서 전부 껐다.

거래당 리스크는 형이 정한 1~2% 안에서, 두 시간대·전 구간을 통과한
모델에만 2% 를 준다.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Play:
    """한 모델의 진입 후 운용 규칙."""

    model: str
    target_rr: float          #: 목표 (R). 유동성 목표가 이보다 멀면 여기서 자른다.
    max_hold: int             #: M5 봉 수. 실제 판정은 `hold_minutes` 로 한다 —
                              #: 30분봉 데이터에서 봉 수로 세면 6배가 된다.
    be_at: float              #: 이 R 에 닿으면 손절을 본전으로. 0 이면 안 옮긴다.
    risk_pct: float           #: 계좌 대비 거래당 리스크
    enabled: bool             #: 기본 실행 여부
    trades: int               #: 21년 표본에서 이 계획으로 체결된 거래 수
    expectancy: float         #: 그때의 거래당 기대값 (R)
    why: str

    @property
    def hold_minutes(self) -> int:
        """봉 크기와 무관한 보유 한도."""
        return self.max_hold * 5

    @property
    def hold_hours(self) -> float:
        return self.hold_minutes / 60.0

    def bars_to_hold(self, bar_minutes: float) -> int:
        """이 시계열의 봉 크기에서 몇 봉을 들고 있어야 하는가."""
        if bar_minutes <= 0:
            return self.max_hold
        return max(1, int(round(self.hold_minutes / bar_minutes)))

    def describe(self) -> str:
        mark = "O" if self.enabled else "X"
        be = f"{self.be_at:g}R" if self.be_at else "안함"
        return (f"[{mark}] {self.model:<13} 목표 {self.target_rr:g}R  "
                f"보유 {self.hold_hours:g}시간  본전 {be:<4}  "
                f"리스크 {self.risk_pct:g}%   "
                f"[{self.trades:,}거래 {self.expectancy:+.3f}R]\n"
                f"      {self.why}")


#: 21년 x 두 시간대 실측 기대값 순
PLAYS: dict[str, Play] = {
    "Unicorn": Play(
        "Unicorn", target_rr=4.0, max_hold=96, be_at=0.0, risk_pct=2.0,
        enabled=True, trades=1939, expectancy=0.297,
        why="유일하게 6개 구간 전부 양수고, 최근 구간(2024~)이 +0.39R 로 가장 좋다. "
            "M30 +0.317R / M15 +0.297R 로 시간대도 안 탄다. 매수 +0.326R / "
            "매도 +0.316R 로 대칭. 브레이커와 FVG 가 겹치는 자리 — 두 근거가 "
            "같은 가격에서 만나는 곳만 잡으니 셋업이 적고 질이 높다."),
    "TurtleSoup": Play(
        "TurtleSoup", target_rr=4.0, max_hold=96, be_at=0.0, risk_pct=2.0,
        enabled=True, trades=2954, expectancy=0.122,
        why="표본이 가장 두껍다(M15 2,954 / M30 3,609거래). 6개 구간 중 5개 양수, "
            "음수인 2012~2015 도 -0.04R 로 얕다. 두 시간대 격자 모양이 같다: "
            "2.5R 위로 양수, 짧게 자르면 음수 — 가짜 돌파가 풀리면 반대편까지 간다."),
    "TJR": Play(
        "TJR", target_rr=3.0, max_hold=288, be_at=0.0, risk_pct=1.0,
        enabled=False, trades=1218, expectancy=0.064,
        why="21년 전체로는 양수(M15 +77.7R)지만 구간별로 보면 우위가 사라지는 중이다: "
            "+0.33 -> +0.13 -> +0.12 -> -0.02 -> -0.02 -0.05. 최근 세 구간이 음수라 "
            "기본에서 뺐다. TJR 본인의 규칙(스윕 종가복귀 -> 구조전환 -> 기원 오더블록)은 "
            "그대로 구현돼 있으니 --models TJR 로 켜서 직접 확인할 수 있다."),
    "OTE": Play(
        "OTE", target_rr=3.0, max_hold=288, be_at=0.0, risk_pct=1.0,
        enabled=False, trades=2326, expectancy=-0.022,
        why="표본은 두꺼운데(2,326거래) M15 에서 6구간 중 4구간이 음수고 총 -52.2R. "
            "M30 에서는 +41.8R 이었다 — 시간대를 바꾸면 부호가 뒤집힌다. "
            "실측상 OTE 진입의 72%가 90% 넘게 더 되돌린다. 기본 해제."),
    "JudasSwing": Play(
        "JudasSwing", target_rr=4.0, max_hold=288, be_at=0.0, risk_pct=1.0,
        enabled=False, trades=416, expectancy=-0.059,
        why="M30 에서 +0.435R 로 기대값 1위였는데 표본이 212거래뿐이었다. M15 에서 "
            "416거래로 다시 재니 6구간 중 5구간 음수, 총 -24.5R. "
            "얇은 표본의 1등이 무엇인지 보여주는 사례다. 기본 해제."),
    "ICT2022": Play(
        "ICT2022", target_rr=2.5, max_hold=288, be_at=0.0, risk_pct=1.0,
        enabled=False, trades=1026, expectancy=0.018,
        why="21년 합계는 +18.8R 로 겨우 양수지만 구간별로 0 근처에서 흔들리다 "
            "최근 두 구간이 음수다. 같은 재료(스윕+변위)를 쓰는 유니콘이 "
            "모든 면에서 낫다. 기본 해제."),
    "SilverBullet": Play(
        "SilverBullet", target_rr=2.0, max_hold=12, be_at=0.0, risk_pct=1.0,
        enabled=False, trades=469, expectancy=0.013,
        why="M15 에서 드디어 셋업이 나왔다(469거래, 승률 47.3%). 승률은 전 모델 중 "
            "최고인데 총 +5.9R 로 거의 0 이다 — 자주 맞지만 크게 못 먹는다. "
            "구간별로도 +0.11/-0.08/0.00/+0.13/-0.06/-0.03 으로 부호가 오간다. "
            "30분봉에서는 창이 1시간이라 셋업 자체가 0건이었다."),
}

#: 기본 실행 모델
ACTIVE = [n for n, p in PLAYS.items() if p.enabled]

#: 이 계획들을 뽑아낸 표본
SAMPLE = ("XAUUSD 2004-06-11 ~ 2026-01-30 · M30 248,912봉(셋업 8,241) + "
          "M15 494,235봉(셋업 12,916) · GMT+2 고정 서버시간을 UTC 로 변환")

#: 계획을 고른 데이터와 성적을 잰 데이터가 같다는 경고
CAVEAT = """주의
  · 계획을 고른 표본과 성적을 잰 표본이 같은 기간이다. 시간대(M15/M30)와
    구간(4년씩)과 방향(매수/매도)으로 쪼개서 무너지지 않는지 확인했지만,
    2026년 이후는 아무도 안 본 데이터다. 진짜 검증은 그쪽에서 난다.
  · 켠 모델이 둘뿐이다. 적다고 느껴지겠지만, 껐다가 다시 켜는 것보다
    켰다가 잃는 게 비싸다. 나머지는 지운 게 아니라 --all-models 나
    --models 이름 으로 언제든 켤 수 있다.
  · M5 는 아직 못 봤다. 실버불릿처럼 창이 짧은 모델은 M15 에서도 여전히
    거칠 수 있다."""


def play(model: str) -> Play:
    if model not in PLAYS:
        raise KeyError(f"모르는 모델 {model!r} — 가능한 값: {', '.join(PLAYS)}")
    return PLAYS[model]


def table() -> str:
    lines = ["모델별 청산 계획", "=" * 74, f"표본: {SAMPLE}", "=" * 74]
    for p in PLAYS.values():
        lines.append(p.describe())
    lines.append("=" * 74)
    lines.append(f"기본 실행: {', '.join(ACTIVE)}")
    lines.append("")
    lines.append(CAVEAT)
    return "\n".join(lines)
