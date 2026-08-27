"""ICT 셋업의 자료형과 설정.

실제 탐지는 `ict.strategy` (사전계산된 `ict.engine.Market` 위에서 도는
빠른 경로) 가 한다. 여기에는 그 결과를 담는 `Setup` 과, 모든 모델이
공유하는 `Config` 만 둔다.

ICT 2022 모델 (핵심) — 순서가 전부다. 하나라도 어긋나면 셋업이 아니다.

  1. HTF 편향      가격이 끌려갈 유동성(DOL)을 정한다
  2. 유동성 습격    반대편 스톱을 먼저 턴다 (SSL 습격 → 매수 관점)
  3. MSS + 변위    습격 뒤에 구조가 깨지고 FVG 를 남긴다
  4. PD Array 복귀  그 변위가 남긴 FVG/OB 로 되돌아올 때 진입
  5. 목표          반대편 미회수 유동성

  · 진입은 디스카운트(매수)/프리미엄(매도) 에서만
  · 손절은 습격 극점 바깥
  · 킬존 안에서만
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from ict import liquidity as liq
from ict import pdarrays as pda
from ict.structure import StructureEvent

Side = Literal["buy", "sell"]


@dataclass
class Setup:
    ts: datetime
    index: int
    model: str
    side: Side
    entry: float
    stop: float
    target: float
    array: pda.PDArray
    raid: liq.Raid | None
    mss: StructureEvent
    target_pool: liq.Pool | None
    killzone: str
    mss_index: int = -1        # 전체 시계열 기준 절대 인덱스 (중복 제거용)
    notes: list[str] = field(default_factory=list)

    @property
    def risk(self) -> float:
        return abs(self.entry - self.stop)

    @property
    def reward(self) -> float:
        return abs(self.target - self.entry)

    @property
    def rr(self) -> float:
        return self.reward / self.risk if self.risk > 0 else 0.0

    def describe(self) -> str:
        arrow = "매수" if self.side == "buy" else "매도"
        lines = [
            f"[{self.model}] {arrow}  {self.ts:%Y-%m-%d %H:%M}  ({self.killzone})",
            f"  진입 {self.entry:.2f}  손절 {self.stop:.2f}  목표 {self.target:.2f}"
            f"   RR 1:{self.rr:.1f}  리스크 {self.risk:.2f}",
            f"  진입근거 {self.array.kind} {self.array.bottom:.2f}~{self.array.top:.2f}",
        ]
        if self.raid:
            lines.append(f"  유동성 습격 {self.raid.pool.label} {self.raid.pool.price:.2f}")
        if self.target_pool:
            lines.append(f"  목표 유동성 {self.target_pool.label} {self.target_pool.price:.2f}")
        lines += [f"  · {n}" for n in self.notes]
        return "\n".join(lines)


@dataclass
class Config:
    """ICT 모델 파라미터. 전부 ICT 개념에 1:1 대응한다."""
    # 시간
    require_killzone: bool = True
    allowed_windows: tuple[str, ...] = ("LondonKZ", "NY_AM_KZ", "SilverBulletAM",
                                        "LondonCloseKZ", "SilverBulletPM")
    # 구조
    swing_left: int = 1
    swing_right: int = 1
    min_displacement_atr: float = 1.0
    mss_lookback: int = 40            # MSS 가 이보다 오래되면 무효
    # 유동성
    raid_lookback: int = 60           # 습격은 MSS 이전 이 범위 안에 있어야
    require_raid: bool = True
    # 진입
    entry_style: str = "ce"           # ce / proximal / distal
    array_kinds: tuple[str, ...] = ("FVG", "OB", "BREAKER")
    require_discount_premium: bool = True
    stop_buffer_atr: float = 0.2
    # 목표
    min_rr: float = 2.0
    default_rr: float = 3.0           # 목표 유동성이 없을 때
    max_rr: float = 6.0
    #: 유동성이 아무리 멀리 있어도 이보다 먼 목표는 잡지 않는다.
    #: ICT 는 반대편 유동성을 노리지만, 1:20 짜리 목표는 도달 전에
    #: 되돌림에 먼저 잡힌다. 백테스트 숫자만 부풀리는 값이다.
    max_pool_distance_atr: float = 25.0
    #: 현재가에서 이보다 먼 풀은 이번 셋업의 습격 대상도, 목표도 아니다.
    #: 이걸 안 걸면 며칠 전 레벨이 근거로 붙는다.
    # 분석 구간
    htf_bars: int = 400
    ltf_bars: int = 600
