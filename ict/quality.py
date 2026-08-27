"""좋은 셋업만 고르기 — 실측으로 남은 것만.

문제
----
EA 가 하루 24건씩 주문을 냈다. 파이썬 모델도 조건에 맞는 걸 전부 잡았다.
"조건에 맞으면 전부" 는 트레이딩이 아니라 그냥 노출이다.

무엇을 시도했고 무엇이 남았나
-----------------------------
ICT Ep.2 의 요소들을 하나씩 게이트로 만들어 21년 4,275거래에 대해
따로따로 재봤다. 대부분이 **작동하지 않았다.** 그대로 적어 둔다.

  상태(Context)          Reversal +0.429R / Retracement +0.169R
                         / Expansion +0.129R        <- 작동함. 채택.
  프리미엄/디스카운트      맞음 +0.169R / 틀림 +0.137R  <- 약함. 참고만.
  DOL 방향               맞음 +0.172R / 반대 +0.165R  <- 구분 못 함. 버림.
  주간·일간 편향 합의      합의 +0.064R / 편향없음 +0.011R
                         / 일간만 +0.239R           <- 뒤집혀 있다. 버림.
  합류 점수(9개 항목 합산)  점수 0 -> +0.326R,
                         점수 8 -> +0.077R          <- 상관 없음. 버림.

합류 점수를 만들어 봤다가 버린 이유가 중요하다. 근거를 많이 셀수록 좋은
셋업일 거라고 가정했는데, 데이터는 그렇지 않다고 답했다. 그럴듯한
점수표를 만드는 건 쉽고, 그게 맞는지 재는 건 따로 해야 하는 일이다.

남은 규칙
---------
1. **Expansion 중에는 안 잡는다.** 이미 간 걸 쫓는 자리다.
   ICT 가 진입을 프레임할 수 있다고 말한 상태(Retracement/Reversal)와
   실측이 일치한 유일한 항목이다.
2. **하루 상한.** 좋은 게 여러 개여도 그날 몇 개까지만. 우위를 늘리는 게
   아니라 노출만 늘리는 걸 막는다. Reversal 을 Retracement 보다 먼저 잡는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Sequence

from ict.context import TRADEABLE, Context, Range
from ict.models import Setup
from ict.timeops import ny_date

#: 하루에 몇 개까지 — 형이 정한 값 (좋은 것 2~3개, 최대 10개)
PER_DAY = 3
HARD_CAP_PER_DAY = 10

#: 상태별 우선순위. 숫자가 클수록 먼저 잡는다.
PRIORITY: dict[str, int] = {"Reversal": 2, "Retracement": 1}


@dataclass
class Scored:
    setup: Setup
    context: Context
    rank: int
    note: str

    @property
    def day(self) -> date:
        return ny_date(self.setup.ts)


def judge(s: Setup, ctx: Context, rng: Range) -> Scored | None:
    """상태 게이트. 통과하면 우선순위를 매겨 돌려준다."""
    if ctx not in TRADEABLE:
        return None
    rank = PRIORITY.get(ctx, 0)
    note = f"상태 {ctx}"
    if rng.size > 0:
        pos = rng.position(s.entry)
        ok = rng.is_discount(s.entry) if s.side == "buy" else rng.is_premium(s.entry)
        note += f", 레인지 {pos:.0%}{' 유리' if ok else ''}"
        if ok:
            rank += 1          # 동점일 때만 갈리는 약한 가산점
    return Scored(s, ctx, rank, note)


def best_per_day(scored: Sequence[Scored], per_day: int = PER_DAY,
                 hard_cap: int = HARD_CAP_PER_DAY) -> list[Setup]:
    """그날 우선순위 높은 순으로 `per_day` 개까지만."""
    take = max(1, min(per_day, hard_cap))
    by_day: dict[date, list[Scored]] = {}
    for x in scored:
        by_day.setdefault(x.day, []).append(x)

    out: list[Setup] = []
    for day in sorted(by_day):
        best = sorted(by_day[day], key=lambda z: (-z.rank, z.setup.index))
        for x in best[:take]:
            x.setup.notes.append(x.note)
            out.append(x.setup)
    out.sort(key=lambda s: s.index)
    return out
