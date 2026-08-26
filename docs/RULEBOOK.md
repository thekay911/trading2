# Crow Concept 룰북 — 채널 원문 → 코드 대응표

출처: Telegram 채널 `t.me/crowconcept` (Crow Concept 3.0), 공개 게시글 기준.
게시글은 베트남어/영어가 섞여 있고, 대부분 **차트 이미지 + 짧은 코멘트** 형태다.
아래는 반복적으로 등장하는 규칙만 추려서 코드로 옮긴 것이다.
1회성 코멘트, 특정 가격 콜(예: "금 1962-1963 매수"), 홍보성 문구는 규칙으로 취급하지 않았다.

---

## 1. 방식 요약

한 문장으로 요약하면 **"상위 프레임 방향 → 유동성 스윕 → CHOCH 확인 → 되돌림 구간 지정가"** 이고,
거기에 **아주 엄격한 자금관리**가 붙어 있다. 세부적으로는 세 갈래가 섞여 있다.

| 갈래 | 채널에서의 표현 | 쓰임새 |
|---|---|---|
| Wyckoff 수급 | "Spring 나옴", "재축적", "Phase A" | 방향(Buy only / Sell only) 결정 |
| 구조 매매(SMC) | "CHOCH", "구조 깨짐", "백업 존" | 진입 트리거와 손절 자리 |
| 엘리엇 파동 | "4파 끝, 5파 간다", "ABC 조정" | 목표가와 대기 여부 판단 |

---

## 2. 규칙 → 코드 대응

### 2.1 타임프레임 계층

| 채널 원문 취지 | 코드 |
|---|---|
| "M1/M5 는 그 세션만, M15 는 그날만, H4/D1 은 중장기" | `CrowConfig.htf / mtf / ltf` |
| "H1 은 확인용" | `SCALP` 프리셋의 `htf="H1"` |
| 상위 → 하위 순서로 본다 | `CrowStrategy.evaluate()` 의 1→2→3 단계 |

### 2.2 방향 결정 (HTF)

| 원문 취지 | 코드 |
|---|---|
| "Wyckoff 로 보면 지금은 매수만" | `wyckoff.analyze()` → `WyckoffView.bias` |
| "구조가 깨졌으니 방향 바뀜" | `structure.analyze_structure()` → `StructureState.bias` |
| 둘이 싸우면 안 들어감 | `CrowStrategy._decide_side()` 가 `None` 반환 |

Wyckoff 국면 판정은 레인지 경계를 **분위수(95%/5%)** 로 잡는다.
절대 고·저를 쓰면 스프링의 꼬리가 곧 경계가 되어 정작 스프링을 못 잡기 때문이다.
→ `wyckoff.detect_range()`

### 2.3 진입 구역 (MTF)

| 원문 취지 | 코드 |
|---|---|
| "백업(되돌림) 존에서 잡는다" | `liquidity.collect_pois()` (오더블록 + FVG) |
| "구 지지 구간" | `liquidity.order_blocks()` — 임펄스 직전 반대 색 캔들 |
| 한 번 뚫린 존은 안 쓴다 | `liquidity.invalidated()` — **종가로 관통**해야 무효, 단순 터치는 유효 |
| "너무 멀면 기다린다" | `CrowConfig.max_entry_distance_atr` |

### 2.4 트리거 (LTF)

| 원문 취지 | 코드 |
|---|---|
| "유동성 죽이고 나서 간다" | `liquidity.find_sweeps()` + `last_sweep()` |
| "CHOCH 로 단기 구조가 깨져서 진입" | `structure.StructureState.last_choch()` |
| 순서가 중요하다 (스윕 → CHOCH) | `evaluate()` 에서 `ch.index < sweep.index` 면 기각 |
| "M5 종가가 1953 아래로 마감해야 확인" | `CrowConfig.structure_break_on_close = True` |

### 2.5 주문

| 원문 취지 | 코드 |
|---|---|
| "지정가 걸어두고 잔다" | `entry_style="limit"`, `limit_expiry_bars` |
| 즉시 진입 스캘프도 함 | `market_if_at_poi=True` — 가격이 이미 존 안이면 시장가 |
| "0.03 을 3분할로 나눠서" | `POI.split_entries()`, `max_entries_per_setup=2` |
| "직전 저점 아래 SL" | `CrowStrategy._stop()` — 스윕 극점 + ATR 버퍼 |
| "1:3 이 기본" | `CrowStrategy._target()` — 반대편 유동성 우선, 없으면 고정 `target_rr` |

### 2.6 자금관리 (가장 강조되는 부분)

> "quản lý vốn là thứ quan trọng bậc nhất" — 자금관리가 제일 중요하다

| 원문 취지 | 코드 |
|---|---|
| "1틱당 계좌 1% 이하" | `CrowConfig.risk_pct`, `risk.position_size()` |
| "최소 1:2, 보통 1:3" | `min_rr`, `validate_rr()` |
| "2R 가면 본절로 옮긴다" | `breakeven_at_r`, `ManagedPosition.update()` |
| "1:3 닿으면 일부 뺀다" | `partial_at_r`, `partial_fraction` |
| "SL 은 절대 밀지 않는다" | `move_sl_only_forward`, `ManagedPosition._sl_is_forward()` |
| "물타기 금지" | `allow_averaging_down=False` |
| "2번 손절 나면 그날은 끝" | `max_consecutive_losses`, `RiskState.can_trade()` |
| "레버리지 1:50 또는 1:20 으로 낮춰라" | `max_leverage`, `max_lots_by_leverage()` |
| "여유 자금이면 계좌 3개로 나눠라" | `account_split`, `risk.split_capital()` |

### 2.7 세션 / 뉴스

| 원문 취지 | 코드 |
|---|---|
| "유럽·미국 세션에 매매" | `CrowConfig.sessions = (LONDON, NEWYORK)` |
| "NFP(19:30) 앞뒤로 물량 줄인다" | `sessions.news_blackout()` |
| 금요일 마감 갭 회피 | `sessions.friday_close_block()` |

---

## 3. 프리셋 = 채널의 계좌 3분할

| 프리셋 | 대응 | 리스크 | 타임프레임 | 근거 |
|---|---|---|---|---|
| `swing` | 스윙 계좌 | 1% | D1 → H4 → H1 | "H1 스윙은 15-30일 유효" |
| `scalp` | 스캘핑 계좌 | 0.5% | H1 → M15 → M5 | "M15 로 하루 5-8 시그널" |
| `highrisk` | 소액 고위험 계좌 | 6% | M15 → M5 → M1 | "M1 은 SL 이 2-3핍이라 스프레드에 죽는다 → 작은 계좌로만" |

```
crowcode split --capital 5000
  swing       3,000.00
  scalp       1,500.00
  high_risk     500.00
```

---

## 4. 코드로 옮기지 않은 것 (의도적으로 제외)

| 항목 | 이유 |
|---|---|
| "월 30-50% 수익" | 검증 불가능한 수익률 주장. 파라미터가 아니라 마케팅 문구다. |
| "승률 76%, 1:3 RR" | 표본·기간·계좌가 공개되지 않음. 목표값으로도 넣지 않았다. |
| 개별 가격 콜(1962-1963 등) | 특정 시점 데이터. 규칙이 아니라 결과물이다. |
| 시그널 구독/패스뷰 안내 | 매매 방식과 무관. |
| 손실 후 회복 서사("-30% → 2일 뒤 +50%") | 규칙이 아니라 사후 서술. 오히려 `max_daily_loss_pct` 로 반대 방향의 제약을 걸었다. |

---

## 5. 주의

- 이 코드는 **채널에 공개된 서술을 규칙으로 재구성한 것**이며, 채널 운영자가 실제로 쓰는
  시스템과 동일하다는 보장은 없다. 게시글 상당수가 차트 이미지라 텍스트만으로는
  파라미터를 정확히 복원할 수 없다.
- 동봉된 합성 데이터는 무작위 보행이라 **우위(edge)가 없다**. 백테스트 숫자는
  "규칙이 의도대로 작동하는지" 확인용이지 성과 근거가 아니다.
- 실거래 전에는 반드시 본인 브로커의 실제 틱 데이터·스프레드·스왑으로 검증할 것.
  채널 스스로도 "M1 은 스프레드 때문에 못 먹는다" 고 여러 번 적고 있다.
