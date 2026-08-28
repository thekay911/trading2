"""자료(TPO Yugi 2)의 주장을 데이터로 재기.

각 항목은 '자료가 이렇게 주장한다' 와 '실제로 그런가' 를 나란히 둔다.
우연 수준(50% 근처)이면 그 개념은 이 시장에서 근거가 없다는 뜻이다.
"""

from __future__ import annotations

import statistics
from typing import Sequence

from crowcode.data import Candle
from tpo.profile import Profile, build
from tpo.sessions import Session, chain, split


def _pct(a: int, b: int) -> str:
    return f"{a / b:.1%}" if b else "-"


def profiles(candles: Sequence[Candle], tick: float) -> list[tuple[Session, Profile]]:
    out = []
    for s in split(candles):
        try:
            out.append((s, build(s.bars, tick)))
        except ValueError:
            continue
    return out


def run(candles: Sequence[Candle], tick: float = 1.0) -> str:
    ses = split(candles)
    prof = {id(s): build(s.bars, tick) for s in ses if s.bars}
    pairs = [(a, b) for a, b in chain(ses) if id(a) in prof and id(b) in prof]
    lines = ["=" * 74,
             f" TPO 주장 검증 — 세션 {len(ses):,}개, 연결쌍 {len(pairs):,}개, "
             f"버킷 {tick:g}",
             "=" * 74]

    # 1. IB 가 좁으면 다음 세션이 돌파한다
    #
    # 자료는 '1%' 라는 절대 기준을 쓰는데, 금 세션은 99% 가 그 아래다.
    # (그 기준으로는 10,908 대 94 로 쏠려 비교가 성립하지 않는다.)
    # 그래서 같은 세션 이름끼리 모아 사분위로 나눈다 — '이 세션치고 좁은가'.
    rel: dict[str, list[float]] = {}
    for a, b in pairs:
        pa = prof[id(a)]
        if pa.ib_range > 0 and pa.close > 0:
            rel.setdefault(a.name, []).append(pa.ib_range / pa.close)
    cut = {}
    for k, v in rel.items():
        v = sorted(v)
        cut[k] = (v[len(v) // 4], v[3 * len(v) // 4])

    tight = wide = tight_bo = wide_bo = 0
    for a, b in pairs:
        pa, pb = prof[id(a)], prof[id(b)]
        if pa.ib_range <= 0 or pa.close <= 0 or a.name not in cut:
            continue
        r = pa.ib_range / pa.close
        broke = pb.high > pa.high or pb.low < pa.low
        lo, hi = cut[a.name]
        if r <= lo:
            tight += 1
            tight_bo += broke
        elif r >= hi:
            wide += 1
            wide_bo += broke
    lines += ["-" * 74,
              " 1. IB 가 좁으면 다음 세션이 돌파한다",
              "    (자료의 '1%' 기준은 금에서 99% 가 해당돼 쓸 수 없다.",
              "     세션별 하위 25% 를 좁음, 상위 25% 를 넓음으로 본다)",
              f"   IB 좁음  {_pct(tight_bo, tight):>7}  ({tight:,}건)",
              f"   IB 넓음  {_pct(wide_bo, wide):>7}  ({wide:,}건)",
              "   → 두 값이 비슷하면 IB 폭은 돌파를 예측하지 못한다"]

    # 2. VA 가 IB 안에 있으면 다음 세션에 추세가 난다
    ins = outs = 0
    ins_r: list[float] = []
    outs_r: list[float] = []
    for a, b in pairs:
        pa, pb = prof[id(a)], prof[id(b)]
        if pa.ib_range <= 0 or pb.close <= 0:
            continue
        rel = pb.range / pb.close
        if pa.va_inside_ib():
            ins += 1
            ins_r.append(rel)
        else:
            outs += 1
            outs_r.append(rel)
    lines += ["-" * 74,
              " 2. VA 가 IB 안에 들어가면 다음 세션은 추세장",
              f"   VA in IB   다음 세션 변동폭 중앙값 "
              f"{statistics.median(ins_r) * 100:.3f}%  ({ins:,}건)"
              if ins_r else "   VA in IB   표본 없음",
              f"   그 외       다음 세션 변동폭 중앙값 "
              f"{statistics.median(outs_r) * 100:.3f}%  ({outs:,}건)"
              if outs_r else "   그 외       표본 없음",
              "   → 앞쪽이 뚜렷하게 커야 주장이 성립한다"]

    # 3. poor high / poor low 는 뚫린다
    ph = pl = ph_broken = pl_broken = 0
    clean_h = clean_h_broken = 0
    for a, b in pairs:
        pa, pb = prof[id(a)], prof[id(b)]
        if pa.poor_high():
            ph += 1
            ph_broken += pb.high > pa.high
        else:
            clean_h += 1
            clean_h_broken += pb.high > pa.high
        if pa.poor_low():
            pl += 1
            pl_broken += pb.low < pa.low
    lines += ["-" * 74,
              " 3. 극점에 TPO 가 2개 이상(poor high/low)이면 뚫리기 쉽다",
              f"   poor high  다음 세션에 뚫림 {_pct(ph_broken, ph):>7}  ({ph:,}건)",
              f"   clean high 다음 세션에 뚫림 {_pct(clean_h_broken, clean_h):>7}  ({clean_h:,}건)",
              f"   poor low   다음 세션에 뚫림 {_pct(pl_broken, pl):>7}  ({pl:,}건)",
              "   → poor 쪽이 뚜렷하게 높아야 한다"]

    # 4. 돌파 후 앞 세션 POC 로 돌아온다
    #
    # 대조군이 없으면 이 숫자는 의미가 없다. 다음 세션이 앞 세션 레인지의
    # 아무 가격이나 스치는 비율이 이미 높다면, POC 라서 간 게 아니다.
    back = tot = 0
    ctrl_hits = ctrl_tot = 0
    for a, b in pairs:
        pa, pb = prof[id(a)], prof[id(b)]
        if not (pb.high > pa.high or pb.low < pa.low):
            continue
        tot += 1
        back += pb.low <= pa.poc <= pb.high
        # 대조: 앞 세션 레인지를 5등분한 지점들
        for f in (0.1, 0.3, 0.5, 0.7, 0.9):
            lvl = pa.low + pa.range * f
            ctrl_tot += 1
            ctrl_hits += pb.low <= lvl <= pb.high
    lines += ["-" * 74,
              " 4. 돌파 뒤 가격은 앞 세션의 POC 로 돌아온다",
              f"   POC 도달        {_pct(back, tot):>7}  ({tot:,}건)",
              f"   대조: 아무 가격  {_pct(ctrl_hits, ctrl_tot):>7}  "
              f"(앞 세션 레인지 5등분 지점)",
              "   → POC 가 대조군보다 뚜렷하게 높아야 POC 라서 간 것이다"]

    # 5. 종가가 VA 밖이면 그 VA 는 가치구간이 아니다
    #    -> 다음 세션이 그 VA 를 다시 존중하지 않아야 한다
    out_ok = out_tot = in_ok = in_tot = 0
    for a, b in pairs:
        pa, pb = prof[id(a)], prof[id(b)]
        overlap = min(pa.vah, pb.vah) - max(pa.val, pb.val)
        respected = overlap > 0
        if pa.close_outside_va():
            out_tot += 1
            out_ok += respected
        else:
            in_tot += 1
            in_ok += respected
    lines += ["-" * 74,
              " 5. 종가가 VA 밖이면 그 VA 는 제대로 된 가치구간이 아니다",
              f"   종가 VA 밖  다음 VA 가 겹침 {_pct(out_ok, out_tot):>7}  ({out_tot:,}건)",
              f"   종가 VA 안  다음 VA 가 겹침 {_pct(in_ok, in_tot):>7}  ({in_tot:,}건)",
              "   → 뒤쪽이 뚜렷하게 높아야 주장이 성립한다"]

    # 6. 세션 연결: 앞 세션이 뒤 세션의 레인지를 만드는가
    #
    # '레인지를 만든다' 를 두 가지로 본다.
    #   (a) 뒤 세션의 극점이 앞 VA 경계 근처에서 만들어지는가
    #   (b) 뒤 세션이 앞 VA 밖으로 나가면 그쪽으로 확장되는가
    edge: dict[str, list[int]] = {}
    follow: dict[str, list[int]] = {}
    nextof = {id(a): b for a, b in pairs}
    for a, b in pairs:
        pa, pb = prof[id(a)], prof[id(b)]
        key = f"{a.name}->{b.name}"
        tol = max(tick, pa.va_width * 0.10)
        at_edge = (abs(pb.high - pa.vah) <= tol) or (abs(pb.low - pa.val) <= tol)
        edge.setdefault(key, []).append(1 if at_edge else 0)
        # (b) 는 순환 논증이 되기 쉽다. "앞 VA 위에서 마감했으면 그 세션이
        #     상승 세션이었나" 는 거의 정의상 참이다(VA 는 앞 세션 가운데
        #     근처에 있으므로). 그래서 뒤 세션이 아니라 **그 다음 세션까지**
        #     이어졌는지를 본다.
        nxt = nextof.get(id(b))
        if nxt is None:
            continue
        pn = prof[id(nxt)]
        if pb.close > pa.vah:
            follow.setdefault(key, []).append(1 if pn.close > pb.close else 0)
        elif pb.close < pa.val:
            follow.setdefault(key, []).append(1 if pn.close < pb.close else 0)
    lines += ["-" * 74,
              " 6. 아시아가 유럽의, 유럽이 CME 의 레인지를 만든다",
              "    (a) 뒤 세션의 고·저가 앞 VA 경계에서 멈추는 비율"]
    for k, v in sorted(edge.items()):
        if len(v) >= 50:
            lines.append(f"   {k:<16}{_pct(sum(v), len(v)):>7}  ({len(v):,}건)")
    lines.append("    (b) 앞 VA 를 벗어나 마감하면 그 다음 세션까지 이어지는가")
    for k, v in sorted(follow.items()):
        if len(v) >= 50:
            lines.append(f"   {k:<16}{_pct(sum(v), len(v)):>7}  ({len(v):,}건)")

    lines += ["=" * 74,
              " 우연 수준(50% 근처)이거나 두 집단이 비슷하면 그 주장은",
              " 이 시장·이 표본에서 근거가 없다는 뜻이다.",
              "=" * 74]
    return "\n".join(lines)
