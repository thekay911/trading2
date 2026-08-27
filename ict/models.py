"""ICT 진입 모델.

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
from typing import Literal, Sequence

from crowcode.data import Candle, atr
from ict import liquidity as liq
from ict import pdarrays as pda
from ict.ranges import DealingRange, leg_range, swing_range
from ict.structure import BEAR, BULL, Dir, StructureEvent, analyze, swings
from ict.timeops import active_windows, in_killzone, session_of

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
    max_pool_distance_atr: float = 25.0
    #: 현재가에서 이보다 먼 풀은 이번 셋업의 습격 대상도, 목표도 아니다.
    #: 이걸 안 걸면 며칠 전 레벨이 근거로 붙는다.
    # 분석 구간
    htf_bars: int = 400
    ltf_bars: int = 600


def find_setup(candles: Sequence[Candle], now: int, cfg: Config = Config()) -> Setup | None:
    """`now` 시점에서 ICT 2022 모델 셋업을 찾는다. 없으면 None.

    모든 판단은 `now` 이전 데이터만 쓴다.
    """
    if now < 120 or now >= len(candles):
        return None
    bars = list(candles)
    view = bars[:now + 1]
    bar = bars[now]

    # --- 1) 시간 ------------------------------------------------------
    kz = in_killzone(bar.ts)
    windows = active_windows(bar.ts)
    if cfg.require_killzone:
        if not any(w in cfg.allowed_windows for w in windows):
            return None
    kz_name = kz.name if kz else session_of(bar.ts)

    lo = max(0, now - cfg.ltf_bars)
    seg = view[lo:]
    off = lo                                   # seg 인덱스 → 전체 인덱스 보정

    # --- 2) MSS + 변위 -----------------------------------------------
    st = analyze(seg, cfg.swing_left, cfg.swing_right,
                 require_displacement=True,
                 min_displacement_atr=cfg.min_displacement_atr)
    mss = st.last_mss(valid_only=True)
    if mss is None or (len(seg) - 1 - mss.index) > cfg.mss_lookback:
        return None
    side: Side = "buy" if mss.direction == BULL else "sell"

    # --- 3) 유동성 습격이 MSS 보다 먼저 -------------------------------
    pools = liq.reference_pools(view, now)
    a = atr(seg[-120:] if len(seg) > 120 else seg)
    sw = swings(seg, cfg.swing_left, cfg.swing_right)
    pools += liq.equal_levels(sw, tol=a * 0.2)
    # equal_levels 는 seg 인덱스라 보정
    pools = [p if p.label not in ("EQH", "EQL")
             else liq.Pool(p.kind, p.price, p.label, p.index + off, p.strength, p.taken_at)
             for p in pools]

    # 현재가에서 먼 풀은 버린다
    if cfg.max_pool_distance_atr > 0 and a > 0:
        span = a * cfg.max_pool_distance_atr
        pools = [p for p in pools if abs(p.price - bar.close) <= span]

    raid = None
    if cfg.require_raid:
        seg_pools = [liq.Pool(p.kind, p.price, p.label, max(0, p.index - off),
                              p.strength, None) for p in pools]
        raids = liq.find_raids(seg, seg_pools, start=max(0, mss.index - cfg.raid_lookback),
                               require_close_back=False)
        raid = liq.last_raid(raids, mss.direction, mss.index, cfg.raid_lookback)
        if raid is None:
            return None
        # 습격 극점이 MSS 가 깨뜨린 레벨과 동떨어져 있으면 다른 사건이다
        if a > 0 and abs(raid.extreme - mss.level) > a * cfg.max_pool_distance_atr:
            return None
        # 매수라면 습격은 아래쪽(SSL)에서, 매도라면 위쪽(BSL)에서 일어나야 한다
        if side == "buy" and raid.extreme > bar.close:
            return None
        if side == "sell" and raid.extreme < bar.close:
            return None

    # --- 4) 변위가 남긴 PD Array 로 되돌아오는가 ----------------------
    arrays = pda.collect(seg, mss, len(seg) - 1, cfg.array_kinds)
    if not arrays:
        return None
    array = arrays[0]
    entry = array.entry(cfg.entry_style)

    price = bar.close
    if side == "buy" and entry > price:
        return None                            # 아직 되돌아오지 않았다
    if side == "sell" and entry < price:
        return None

    # --- 5) 프리미엄 / 디스카운트 -------------------------------------
    notes: list[str] = []
    if cfg.require_discount_premium:
        d = mss.displacement
        dr = leg_range(seg, d.start, d.end) if d else swing_range(sw, len(seg) - 1)
        if dr is None:
            return None
        if side == "buy" and not dr.is_discount(entry):
            return None
        if side == "sell" and not dr.is_premium(entry):
            return None
        notes.append(f"레인지 위치 {dr.position(entry):.0%} "
                     f"({'디스카운트' if side == 'buy' else '프리미엄'})")
        if dr.in_ote(entry, side):
            notes.append("OTE 구간")

    # --- 6) 손절과 목표 ----------------------------------------------
    buf = a * cfg.stop_buffer_atr
    if side == "buy":
        base = raid.extreme if raid else array.bottom
        stop = min(base, array.bottom) - buf
    else:
        base = raid.extreme if raid else array.top
        stop = max(base, array.top) + buf

    risk = abs(entry - stop)
    if risk <= 0:
        return None

    targets = liq.draw_targets(pools, side, entry)
    target_pool = None
    for p in targets:
        rr = abs(p.price - entry) / risk
        if rr >= cfg.min_rr:
            target_pool = p
            break
    if target_pool is not None:
        target = target_pool.price
    else:
        target = entry + risk * cfg.default_rr if side == "buy" \
            else entry - risk * cfg.default_rr

    setup = Setup(
        ts=bar.ts, index=now, model="ICT2022", side=side,
        entry=entry, stop=stop, target=target, array=array, raid=raid,
        mss=mss, target_pool=target_pool, killzone=kz_name,
        mss_index=mss.index + off, notes=notes,
    )
    if setup.rr < cfg.min_rr:
        return None
    if mss.displacement:
        setup.notes.append(f"변위 {mss.displacement.atr_multiple:.1f}×ATR")
    return setup


def scan(candles: Sequence[Candle], cfg: Config = Config(),
         start: int = 200, step: int = 1) -> list[Setup]:
    """전 구간을 훑어 셋업을 모은다. 같은 MSS 에서는 첫 진입만 취한다."""
    out: list[Setup] = []
    seen: set[tuple[int, str]] = set()
    bars = list(candles)
    for i in range(start, len(bars), step):
        s = find_setup(bars, i, cfg)
        if s is None:
            continue
        key = (s.mss_index, s.side)
        if key in seen:
            continue
        seen.add(key)
        out.append(s)
    return out
