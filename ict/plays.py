"""모델별 청산 계획.

ICT 모델은 시간 스케일이 서로 다르다. 유다 스윙은 세션의 조작이 풀리는
동안 가고, 터틀 수프는 가짜 돌파가 되돌아가는 동안 간다. 전부 같은 목표,
같은 보유시간으로 묶으면 각각의 성격이 사라진다.

값의 출처
---------
실제 XAUUSD 30분봉 **2004-06 ~ 2026-01, 248,912봉, 셋업 8,241건** 에서
목표R x 보유시간 격자를 훑었다. 격자 1등이 아니라 주변 칸이 같이 양수인
고원의 중앙을 골랐다.

이 표본이 나오기 전에는 60일짜리 데이터로 값을 잡았었는데, 21년으로
늘리자 결론이 통째로 뒤집혔다. 그때 1등이던 터틀수프가 21년에서는
-69R 이었다. **표본이 작으면 격자 1등은 잡음이다.**

검증한 것
---------
· 매수·매도 양방향 모두 양수 — 금의 21년 상승추세를 탄 게 아니다
  (추세 편승이면 매도가 음수여야 한다)
· 2004~2007 +0.180R, 2008~2011 +0.260R, 2012~2015 +0.162R,
  2016~2019 +0.162R, 2020~2023 +0.189R — 다섯 구간 내내 일정
· 2024~2026 은 -0.001R. 최근 구간만 우위가 사라졌다. 아래 경고 참조.
· 본전 이동은 6개 모델 전부에서 손해였다. 기대값을 낮추고 낙폭까지
  키운다 — 금은 진입가로 깊게 되돌린 뒤 가는 일이 잦아서, 본전 스톱이
  결국 이길 거래를 먼저 털어낸다. 그래서 전부 껐다.

거래당 리스크는 표본이 두껍고 우위가 확실한 모델에 2%, 얇은 모델에 1% 다.
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


#: 21년 실측 기대값 순
PLAYS: dict[str, Play] = {
    "Unicorn": Play(
        "Unicorn", target_rr=4.0, max_hold=96, be_at=0.0, risk_pct=2.0,
        enabled=True, trades=1224, expectancy=0.317,
        why="브레이커와 FVG 가 겹치는 자리. 2.0R 이상 x 2시간 이상 구간이 통째로 "
            "양수(+0.115~+0.330)인 넓은 고원. 매수 +0.326R / 매도 +0.316R 로 "
            "양방향 대칭 — 추세 편승이 아니다."),
    "JudasSwing": Play(
        "JudasSwing", target_rr=4.0, max_hold=288, be_at=0.0, risk_pct=1.0,
        enabled=True, trades=212, expectancy=0.435,
        why="격자 42칸이 전부 양수고 보유가 길수록 좋아진다 — 세션 조작은 "
            "그날 안에 풀린다. 기대값은 가장 높지만 표본이 212거래뿐이라 "
            "리스크는 1% 로 둔다."),
    "TurtleSoup": Play(
        "TurtleSoup", target_rr=4.0, max_hold=96, be_at=0.0, risk_pct=2.0,
        enabled=True, trades=3609, expectancy=0.179,
        why="표본이 가장 두껍다(3,609거래). 2.0R 위로 전부 양수이고 목표를 "
            "키울수록 좋아진다 — 가짜 돌파가 풀리면 반대편까지 간다. "
            "1.0~1.5R 로 짧게 자르면 오히려 음수다."),
    "OTE": Play(
        "OTE", target_rr=3.0, max_hold=288, be_at=0.0, risk_pct=1.0,
        enabled=True, trades=1223, expectancy=0.043,
        why="1,223거래로 표본은 두꺼운데 우위가 얇다(+0.043R). 2.0R 위에서만 "
            "양수다. 실측상 OTE 진입의 72%가 90% 넘게 더 되돌리는데, 그래도 "
            "목표를 크게 잡는 쪽이 낫다. 얇으니 리스크 1%."),
    "TJR": Play(
        "TJR", target_rr=3.0, max_hold=288, be_at=0.0, risk_pct=1.0,
        enabled=True, trades=540, expectancy=0.049,
        why="스윕 -> 변위 -> 기원 오더블록 되돌림. 2.0R 위 x 4시간 이상에서 "
            "일관되게 양수지만 폭이 작다(+0.025~+0.043). TJR 본인이 말하는 "
            "리스크 1% 상한을 그대로 쓴다."),
    "ICT2022": Play(
        "ICT2022", target_rr=2.5, max_hold=288, be_at=0.0, risk_pct=1.0,
        enabled=False, trades=576, expectancy=0.047,
        why="격자 행 평균이 대부분 음수고 8~24시간 열에만 작은 양수 섬이 있다. "
            "고원이 아니라 섬이다. FVG 의 CE 에 거는 이 진입은 같은 재료를 쓰는 "
            "TJR(오더블록 진입)보다 일관성이 낮았다. 기본 해제."),
    "SilverBullet": Play(
        "SilverBullet", target_rr=2.0, max_hold=12, be_at=0.0, risk_pct=1.0,
        enabled=False, trades=0, expectancy=0.0,
        why="30분봉에서는 셋업이 0건이다 — 실버불릿 창이 1시간이라 봉 3개가 "
            "안 나온다. 판단 불가지 실패가 아니다. M5 나 M15 데이터가 있어야 잰다."),
}

#: 기본 실행 모델
ACTIVE = [n for n, p in PLAYS.items() if p.enabled]

#: 이 계획들을 뽑아낸 표본
SAMPLE = ("XAUUSD M30  2004-06-11 ~ 2026-01-30  248,912봉  셋업 8,241건 "
          "(GMT+2 고정 서버시간을 UTC 로 변환)")

#: 계획을 고른 데이터와 성적을 잰 데이터가 같다는 경고
CAVEAT = """주의
  · 계획을 고른 표본과 성적을 잰 표본이 같다. 진짜 아웃오브샘플이 아니다.
    그래서 격자 1등 대신 고원 중앙을 골랐고, 매수/매도와 4년 구간별로
    나눠서 무너지지 않는지 확인했다. 그래도 완전한 검증은 아니다.
  · 2024~2026 구간만 기대값이 0.00R 이다. 나머지 다섯 구간은 +0.16~+0.26R
    로 일정했다. 우위가 최근에 사라진 것인지, 금이 파라볼릭으로 가던
    특수 구간이라 그런 것인지, 표본이 630거래로 짧아서인지 아직 모른다.
    실계좌 전에 이걸 먼저 확인해야 한다.
  · 실버불릿은 30분봉으로 잴 수 없었다. M5 데이터가 필요하다."""


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
