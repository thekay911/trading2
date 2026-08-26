"""crowcode - Crow Concept 트레이딩 방식을 코드로 정리한 패키지.

t.me/crowconcept 채널에서 반복적으로 언급되는 매매 규칙
(Wyckoff / 구조(CHOCH·BOS) / 유동성 / 엘리엇 파동 / 자금관리)을
실행 가능한 규칙 엔진으로 옮긴 것이다.

전체 규칙 대응표는 docs/RULEBOOK.md 참고.
"""

from crowcode.config import CrowConfig, DEFAULT
from crowcode.data import Candle, Series, load_csv, resample
from crowcode.signals import Signal, Side
from crowcode.strategy import CrowStrategy
from crowcode.backtest import Backtester, BacktestResult

__all__ = [
    "CrowConfig",
    "DEFAULT",
    "Candle",
    "Series",
    "load_csv",
    "resample",
    "Signal",
    "Side",
    "CrowStrategy",
    "Backtester",
    "BacktestResult",
]

__version__ = "1.0.0"
