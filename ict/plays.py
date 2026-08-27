"""모델별 청산 계획.

ICT 모델은 시간 스케일이 서로 다르다. 유다 스윙은 런던의 조작을 뉴욕이
되돌리는 몇 시간짜리고, 터틀 수프는 가짜 돌파가 풀리는 동안 달린다.
전부 같은 목표 R, 같은 보유시간으로 묶으면 각각의 성격이 사라진다.

여기 값은 **격자 1등이 아니다.** 실제 금 데이터(M5 60일 / H1 730일)에서
목표R × 보유시간 격자를 훑어 보고, 주변 칸이 같이 양수인 '고원'의
중앙을 골랐다. 한 칸만 튀는 봉우리는 표본 잡음이라 버렸다.

  · 두 표본에서 모두 양수 → 채택
  · 한쪽만 양수, 반대쪽 음수 → 기본에서 뺀다 (지우진 않는다)

거래당 리스크는 기대값이 두꺼운 모델에 2%, 얇은 모델에 1% 다.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Play:
    """한 모델의 진입 후 운용 규칙."""

    model: str
    target_rr: float          #: 목표 (R). 유동성 목표가 이보다 멀면 여기서 자른다.
    max_hold: int             #: M5 봉 수. 넘기면 시장가 청산.
    be_at: float              #: 이 R 에 닿으면 손절을 본전으로. 0 이면 안 옮긴다.
    risk_pct: float           #: 계좌 대비 거래당 리스크
    enabled: bool             #: 기본 실행 여부
    why: str                  #: 이 값을 고른 이유

    @property
    def hold_hours(self) -> float:
        return self.max_hold * 5 / 60.0

    def describe(self) -> str:
        mark = "O" if self.enabled else "X"
        return (f"[{mark}] {self.model:<13} 목표 {self.target_rr:g}R  "
                f"보유 {self.hold_hours:g}시간  "
                f"본전 {f'{self.be_at:g}R' if self.be_at else '안함':<4}  "
                f"리스크 {self.risk_pct:g}%\n      {self.why}")


#: M5 기준. 순서는 실측 기대값 순.
PLAYS: dict[str, Play] = {
    "TurtleSoup": Play(
        "TurtleSoup", target_rr=4.0, max_hold=48, be_at=2.0, risk_pct=2.0,
        enabled=True,
        why="M5 격자 36칸이 전부 양수(+0.06~+0.75), H1 344건도 양수. "
            "가짜 돌파가 풀리면 반대편까지 달린다 — 목표를 짧게 자를 이유가 없다."),
    "JudasSwing": Play(
        "JudasSwing", target_rr=3.0, max_hold=36, be_at=1.5, risk_pct=2.0,
        enabled=True,
        why="M5 격자 30칸 전부 양수, 2~4시간에서 가장 두껍고 8시간부터 꺾인다. "
            "런던의 조작은 뉴욕 오픈 전후로 정리된다 — 그 이상 들고 있을 근거가 없다."),
    "OTE": Play(
        "OTE", target_rr=1.5, max_hold=48, be_at=0.0, risk_pct=1.0,
        enabled=True,
        why="1.5R 행만 일관되게 양수(+0.09~+0.22)고 2.5R 위로는 0 근처. "
            "실측상 OTE 진입의 73%가 90% 넘게 더 되돌린다 — 얇게 먹고 빠지는 자리다. "
            "우위가 얇아서 리스크 1%."),
    "ICT2022": Play(
        "ICT2022", target_rr=2.0, max_hold=24, be_at=1.0, risk_pct=1.0,
        enabled=False,
        why="M5 에서 30분 보유 열만 양수고 나머지는 전부 음수 — 고원이 아니라 봉우리다. "
            "반대로 H1 730일에서는 전 구간 양수(+0.24~+0.52). "
            "M5 에서 쓸 근거가 없어 기본 해제. 상위 시간대라면 켤 만하다."),
    "SilverBullet": Play(
        "SilverBullet", target_rr=2.0, max_hold=12, be_at=1.0, risk_pct=1.0,
        enabled=False,
        why="M5 격자 36칸이 사실상 전부 음수. H1 에서는 킬존이 1봉이라 셋업 자체가 안 나온다. "
            "이 표본에서는 근거가 없다."),
    "Unicorn": Play(
        "Unicorn", target_rr=2.0, max_hold=24, be_at=1.0, risk_pct=1.0,
        enabled=False,
        why="M5 에서 2.0R 행만 겨우 +0.03 이고 나머지 전부 음수. "
            "H1 은 양수지만 두 표본이 반대 방향이면 채택할 수 없다."),
}

#: 기본 실행 모델
ACTIVE = [n for n, p in PLAYS.items() if p.enabled]


def play(model: str) -> Play:
    if model not in PLAYS:
        raise KeyError(f"모르는 모델 {model!r} — 가능한 값: {', '.join(PLAYS)}")
    return PLAYS[model]


def table() -> str:
    lines = ["모델별 청산 계획 (M5 기준)", "=" * 66]
    for p in PLAYS.values():
        lines.append(p.describe())
    lines.append("=" * 66)
    lines.append(f"기본 실행: {', '.join(ACTIVE)}")
    return "\n".join(lines)
