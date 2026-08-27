"""ICT (Inner Circle Trader) 개념 엔진 — XAUUSD.

crowcode 와 별개의 패키지다. ICT 는 전제가 다르다.

  · 모든 시각은 **뉴욕 시간** 기준이다 (킬존, 마카오, 자정 오픈).
  · 가격은 '유동성' 을 향해 이동한다. 지지·저항이 아니라 매수/매도 스톱이 목표다.
  · 진입은 PD Array (FVG, 오더블록, 브레이커 ...) 에서만 한다.
  · 구조 전환(MSS)은 **변위(displacement)** 를 동반해야 유효하다.
    갭 하나 남기지 못한 돌파는 힘이 없다.

모듈
----
  timeops     뉴욕 시간, 킬존, 매크로, 자정 오픈
  structure   스윙, BOS, MSS, 변위
  liquidity   BSL/SSL, 이전일·주 고저, 유동성 습격
  pdarrays    FVG, 오더블록, 브레이커, BPR, 인버전 FVG
  ranges      딜링 레인지, 프리미엄/디스카운트, OTE
  bias        DOL(Draw On Liquidity) — 가격이 끌려갈 곳
  models      실제 진입 모델 (2022 모델, 실버불릿, 터틀수프, OTE)
  study       2018~현재 데이터로 어떤 개념이 실제로 통했는지 측정
"""

__version__ = "0.1.0"
