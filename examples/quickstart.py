"""crowcode 사용 예제 — 데이터 없이 바로 돌려볼 수 있다.

    python3 examples/quickstart.py
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crowcode.backtest import Backtester
from crowcode.config import preset
from crowcode.data import synthetic
from crowcode.risk import RiskState, split_capital
from crowcode.strategy import CrowStrategy

CAPITAL = 5000.0

# 1) 채널 방식대로 자본을 세 계좌로 나눈다.
cfg = preset("scalp")
sp = split_capital(CAPITAL, cfg)
print("계좌 분리:", sp.as_dict(), "\n")

# 2) 스캘핑 계좌 규칙으로 시그널을 훑는다.
series = synthetic(12000)              # M1 합성 데이터 (약 8일)
strat = CrowStrategy(cfg, "XAUUSD")
view = strat.view(series)
risk = RiskState(balance=sp.scalp)

found = 0
for i in range(800, len(series), 10):
    sig = strat.evaluate(view, risk.balance, None, now_ts=series[i].ts)
    if sig:
        print(sig.pretty(), "\n")
        found += 1
        if found >= 3:
            break

print("필터별 기각 횟수:", strat.rejection_summary(), "\n")

# 3) 같은 규칙으로 백테스트.
res = Backtester(cfg, balance=sp.scalp, spread=0.20, warmup=800, eval_every=10).run(series)
print(res.report())
print("\n※ 합성 데이터는 무작위 보행이라 우위가 없다. 규칙 작동 확인용 숫자다.")
