"""MT5 실행 계층.

`crowcode` 의 규칙 엔진(시그널 산출)과 MetaTrader 5 단말(주문 실행)을 잇는다.

  Broker      실행 인터페이스 (프로토콜)
  PaperBroker 단말 없이 돌리는 시뮬레이션 구현 — 테스트/드라이런용
  Mt5Broker   실제 MetaTrader5 파이썬 패키지 구현 (Windows 단말 필요)
  LiveRunner  새 봉마다 평가 → 주문 → 포지션 관리하는 루프
"""

from crowcode.mt5.broker import (
    AccountInfo, Broker, DealInfo, OrderInfo, OrderResult, PositionInfo, SymbolInfo, Tick,
)
from crowcode.mt5.journal import Journal
from crowcode.mt5.paper import PaperBroker
from crowcode.mt5.runner import LiveConfig, LiveRunner

__all__ = [
    "AccountInfo", "Broker", "DealInfo", "OrderInfo", "OrderResult",
    "PositionInfo", "SymbolInfo", "Tick",
    "Journal", "PaperBroker", "LiveConfig", "LiveRunner",
]
