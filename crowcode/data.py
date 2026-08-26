"""OHLCV 데이터 구조와 유틸리티 (외부 의존성 없음)."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Iterator, Sequence


@dataclass(frozen=True)
class Candle:
    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @property
    def bullish(self) -> bool:
        return self.close >= self.open

    @property
    def bearish(self) -> bool:
        return self.close < self.open

    @property
    def body_top(self) -> float:
        return max(self.open, self.close)

    @property
    def body_bottom(self) -> float:
        return min(self.open, self.close)

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def body(self) -> float:
        return abs(self.close - self.open)


class Series(Sequence[Candle]):
    """시간 오름차순 캔들 시퀀스."""

    def __init__(self, candles: Iterable[Candle], symbol: str = "", timeframe: str = ""):
        self._c = list(candles)
        self._c.sort(key=lambda x: x.ts)
        self.symbol = symbol
        self.timeframe = timeframe

    def __len__(self) -> int:
        return len(self._c)

    def __iter__(self) -> Iterator[Candle]:
        return iter(self._c)

    def __getitem__(self, i):  # type: ignore[override]
        if isinstance(i, slice):
            return Series(self._c[i], self.symbol, self.timeframe)
        return self._c[i]

    def __repr__(self) -> str:
        return f"<Series {self.symbol} {self.timeframe} n={len(self._c)}>"

    # --- 접근자 ---------------------------------------------------------
    def highs(self) -> list[float]:
        return [c.high for c in self._c]

    def lows(self) -> list[float]:
        return [c.low for c in self._c]

    def closes(self) -> list[float]:
        return [c.close for c in self._c]

    def upto(self, ts: datetime) -> "Series":
        """ts 시점까지 '이미 마감된' 캔들만 반환 (룩어헤드 방지)."""
        return Series([c for c in self._c if c.ts <= ts], self.symbol, self.timeframe)

    def window(self, n: int) -> "Series":
        return Series(self._c[-n:], self.symbol, self.timeframe)


def atr(candles: Sequence[Candle], period: int = 14) -> float:
    """Wilder ATR. 캔들이 부족하면 가용 구간으로 계산."""
    if len(candles) < 2:
        return 0.0
    trs: list[float] = []
    for i in range(1, len(candles)):
        c, p = candles[i], candles[i - 1]
        trs.append(max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close)))
    period = min(period, len(trs))
    if period == 0:
        return 0.0
    val = sum(trs[:period]) / period
    for tr in trs[period:]:
        val = (val * (period - 1) + tr) / period
    return val


_TF_MINUTES = {
    "M1": 1, "M5": 5, "M15": 15, "M30": 30,
    "H1": 60, "H4": 240, "D1": 1440,
}


def tf_minutes(tf: str) -> int:
    key = tf.upper()
    if key not in _TF_MINUTES:
        raise ValueError(f"지원하지 않는 타임프레임: {tf}")
    return _TF_MINUTES[key]


def resample(series: Series, timeframe: str) -> Series:
    """하위 타임프레임 → 상위 타임프레임 집계."""
    minutes = tf_minutes(timeframe)
    bucket_ms = minutes * 60
    out: list[Candle] = []
    cur: Candle | None = None
    cur_key: int | None = None
    for c in series:
        key = int(c.ts.timestamp()) // bucket_ms
        if cur_key != key:
            if cur is not None:
                out.append(cur)
            cur_key = key
            cur = Candle(
                ts=datetime.fromtimestamp(key * bucket_ms, tz=c.ts.tzinfo or timezone.utc),
                open=c.open, high=c.high, low=c.low, close=c.close, volume=c.volume,
            )
        else:
            assert cur is not None
            cur = Candle(
                ts=cur.ts,
                open=cur.open,
                high=max(cur.high, c.high),
                low=min(cur.low, c.low),
                close=c.close,
                volume=cur.volume + c.volume,
            )
    if cur is not None:
        out.append(cur)
    return Series(out, series.symbol, timeframe.upper())


def load_csv(path: str, symbol: str = "", timeframe: str = "") -> Series:
    """CSV 로더. 헤더: time,open,high,low,close[,volume]

    time 은 ISO8601 또는 유닉스 초.
    """
    candles: list[Candle] = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            raw = (row.get("time") or row.get("timestamp") or row.get("date") or "").strip()
            if raw.isdigit():
                ts = datetime.fromtimestamp(int(raw), tz=timezone.utc)
            else:
                ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
            candles.append(Candle(
                ts=ts,
                open=float(row["open"]), high=float(row["high"]),
                low=float(row["low"]), close=float(row["close"]),
                volume=float(row.get("volume") or 0.0),
            ))
    return Series(candles, symbol, timeframe)


def synthetic(
    n: int = 3000,
    start: datetime | None = None,
    minutes: int = 1,
    price: float = 1960.0,
    seed: int = 7,
) -> Series:
    """의존성 없이 데모/테스트용 M1 시세를 만든다 (추세 + 되돌림 + 유동성 스윕)."""
    import math
    import random

    rnd = random.Random(seed)
    ts = start or datetime(2024, 1, 8, 0, 0, tzinfo=timezone.utc)
    out: list[Candle] = []
    p = price
    for i in range(n):
        drift = 0.06 * math.sin(i / 180.0) + 0.02 * math.sin(i / 47.0)
        step = rnd.gauss(drift, 0.35)
        o = p
        c = p + step
        wick = abs(rnd.gauss(0, 0.25)) + 0.05
        h = max(o, c) + wick
        l = min(o, c) - wick
        out.append(Candle(ts, o, h, l, c, volume=abs(rnd.gauss(120, 30))))
        p = c
        ts = ts + timedelta(minutes=minutes)
    return Series(out, "XAUUSD", f"M{minutes}")


class MTFView:
    """다중 타임프레임 뷰.

    베이스 시계열을 한 번만 리샘플해 두고, 특정 시각 기준으로
    '이미 마감된' 상위/하위 타임프레임 캔들만 잘라 준다.
    (백테스트에서 매 봉마다 리샘플하는 비용을 없애고 룩어헤드도 막는다)
    """

    def __init__(self, base: Series, timeframes: Iterable[str]):
        self.base = base
        self.base_min = tf_minutes(base.timeframe) if base.timeframe else 1
        self.frames: dict[str, Series] = {}
        for tf in {t.upper() for t in timeframes}:
            self.frames[tf] = base if tf_minutes(tf) == self.base_min else resample(base, tf)
        self._ts: dict[str, list[float]] = {
            tf: [c.ts.timestamp() for c in s] for tf, s in self.frames.items()
        }

    def slice(self, tf: str, now_ts: datetime, max_bars: int = 400) -> list[Candle]:
        """now_ts 시점에 마감이 끝난 캔들 최대 max_bars 개."""
        import bisect

        key = tf.upper()
        s, tss = self.frames[key], self._ts[key]
        dur = tf_minutes(key) * 60
        now_epoch = now_ts.timestamp() + self.base_min * 60  # 현재 베이스 봉의 마감 시각
        # 마감 시각(ts + dur) <= now_epoch 인 마지막 캔들까지
        cut = bisect.bisect_right(tss, now_epoch - dur)
        lo = max(0, cut - max_bars)
        return list(s[lo:cut])

    def last_base(self, now_ts: datetime) -> Candle | None:
        import bisect

        tss = [c.ts.timestamp() for c in self.base]
        i = bisect.bisect_right(tss, now_ts.timestamp()) - 1
        return self.base[i] if i >= 0 else None
