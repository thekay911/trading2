# trading2 — Crow Concept 트레이드 코드

Telegram 채널 [`t.me/crowconcept`](https://t.me/crowconcept) (Crow Concept 3.0) 에 공개된
매매 방식을 **하나의 실행 가능한 규칙 엔진**으로 정리한 것이다.
대상은 **XAUUSD(금) 전용** — 프리셋의 손절 폭, 스프레드 필터, 필요 자본이 전부 금 기준이다.

채널 글은 대부분 차트 이미지 + 짧은 코멘트라서, 흩어져 있는 규칙을
탑다운 파이프라인 하나로 묶고 파라미터화했다.

- 규칙 원문 ↔ 코드 대응표: **[docs/RULEBOOK.md](docs/RULEBOOK.md)**
- MT5 에 붙이는 방법: **[docs/MT5_SETUP.md](docs/MT5_SETUP.md)**
- XAUUSD 전용 설정(단위·자본·스프레드·스왑): **[docs/GOLD.md](docs/GOLD.md)**
- 외부 의존성 없음 (Python 3.10+ 표준 라이브러리만)

---

## 한눈에 보는 매매 로직

```
0. 게이트     세션(유럽·미국) / 뉴스 블랙아웃 / 금요일 마감 / 일일 리스크 한도
      ↓
1. HTF 방향   시장구조(BOS·CHOCH) + Wyckoff 국면  →  "Buy only" 또는 "Sell only"
      ↓                                              (둘이 싸우면 관망)
2. MTF 구역   방향에 맞는 오더블록 / FVG 중 아직 무효화되지 않은 것
      ↓
3. LTF 트리거 반대편 유동성 스윕  →  그 다음 CHOCH  (순서가 틀리면 기각)
      ↓
4. 주문       POI 근접 경계에 지정가 (이미 존 안이면 시장가)
              SL = 스윕 극점 바깥 + ATR 버퍼
              TP = 반대편 유동성, 없으면 고정 R (기본 1:3)
      ↓
5. 관리       2R → 본절 이동 / 3R → 절반 청산 / SL 은 뒤로 절대 안 감
              손절 2연속 → 그날 매매 종료
```

각 단계에서 걸러진 이유는 전부 기록되어 `rejection_summary()` 로 확인할 수 있다.
"왜 시그널이 안 나왔는가" 를 추적할 수 있게 만든 것이 이 구조의 핵심이다.

---

## 설치 / 실행

```bash
git clone <repo> && cd trading2
python3 -m unittest discover -s tests -t .     # 244개 테스트
python3 examples/quickstart.py                 # 데이터 없이 바로 실행되는 예제
```

`data/sample_xauusd_m1.csv` 는 **합성 데이터**다 (CSV 경로 확인용, 실제 시세 아님).

### 금 기준 수치와 프리셋 확인

```bash
python3 -m crowcode gold                    # 단위, 프리셋별 손절 폭·필요 자본, 주요 지표
python3 -m crowcode rules --preset intraday # 파라미터 전체
```

```
프리셋별 손절 폭 가드와 최소 필요 자본
  swing     D1>H4>H1   리스크  1.0%  손절 $ 6.00~$60.00  필요 자본      600 ~     6,000
  intraday  H4>H1>M15  리스크  1.0%  손절 $ 2.50~$25.00  필요 자본      250 ~     2,500
  scalp     H1>M15>M5   리스크  0.5%  손절 $ 1.50~$10.00  필요 자본      300 ~     2,000
  highrisk  M15>M5>M1   리스크  6.0%  손절 $ 0.80~$4.00   필요 자본       13 ~        67
```

### 리스크 사다리 확인

```bash
python3 -m crowcode risk --preset intraday
```

거래당 2% / 1:3 에서 손익분기 승률, 승률별 최대낙폭·최장연패,
서킷이 실제로 걸릴 수 있는지를 계산한다.

```
 손실 사다리
   거래당            -2%
   연속 2회 손절     -4%   → 그날 매매 종료
   일일 한도         -6%   → 그날 매매 종료
   서킷브레이커      -10%  → 잠금 (복기 전까지 재개 불가)

 손익분기 승률  25%   ← 1:3 에서 본전이 되는 승률

   승률         기대값      연간 R      수익률      최대낙폭     최장연패     서킷     손실마감
   25%     +0.00R       -1R      -2%     46.0%      14회     0%      51%
   35%     +0.40R      +63R    +126%     22.0%      10회     0%       0%
   50%     +1.00R     +156R    +312%     14.0%       6회     0%       0%
```

### 사전 점검 — 계좌·브로커가 이 설정을 감당하는지

```bash
python3 -m crowcode preflight --paper --preset intraday --balance 3000
```

심볼 이름, 계약 크기, 브로커 최소 이격, 스프레드, 필요 자본, 설정 모순을 한 번에 본다.

### 계좌 3분할 (채널의 "스윙/스캘핑/고위험 계좌 분리")

```bash
python3 -m crowcode split --capital 5000
#   swing       3,000.00
#   scalp       1,500.00
#   high_risk     500.00
```

### 시그널 산출

```bash
# 자기 데이터로 (CSV: time,open,high,low,close[,volume])
python3 -m crowcode signal --csv data/xauusd_m1.csv --preset scalp --balance 5000

# 데이터가 없으면 합성 데이터로 동작 확인
python3 -m crowcode signal --preset scalp --balance 5000 --bars 12000
```

출력 예시:

```
▲ BUY  XAUUSD [H1>M15>M5] LIMIT
  진입 1954.780 / SL 1948.475 / TP 1973.692  (RR 1:3.0)
  랏 0.01  리스크 6.30  점수 4.50
  본절 이동가 1967.388 (2R)
  · 세션: NewYork
  · HTF(H1) 구조=bullish, Wyckoff=undefined/Phase B
  · 유동성 스윕: 1949.406 (below)
  · LTF(M5) CHOCH 1951.212 돌파 → 구조 전환
  · 진입 POI: fvg 1953.295~1954.780 (지정가)
```

### 백테스트

```bash
python3 -m crowcode backtest --csv data/xauusd_m1.csv --preset scalp \
        --balance 5000 --spread 0.20 --trades
python3 -m crowcode demo        # 세 프리셋 전부 합성 데이터로 실행
```

---

## MT5 에서 실제로 돌리기

같은 규칙을 두 가지 방식으로 실행할 수 있다. 자세한 절차는
[docs/MT5_SETUP.md](docs/MT5_SETUP.md).

### A. MQL5 EA — Python 없이 차트에 부착

`mql5/Experts/CrowConcept.mq5` 를 데이터 폴더의 `MQL5/Experts/` 에 넣고
MetaEditor 에서 F7 로 컴파일한 뒤 **금 차트**에 드래그한다.
EA 속성창에서 `mql5/Presets/CrowConcept-intraday.set` 을 불러오면 설정이 끝난다.
전부 `InpDryRun=true` 로 저장되어 있어 주문 없이 로그만 남긴다 — 먼저 이걸로 검증한다.

```
CrowConcept SIGNAL BUY LIMIT | entry=1948.58 sl=1947.83 tp=1950.81 rr=1:3.0 lots=0.06 zone=fvg
CrowConcept: 1234567 moved to breakeven at 1950.07
CrowConcept: 1234567 partial close 0.03 at 3.0R
```

### B. Python 브릿지 — 기존 엔진을 그대로 사용

```powershell
pip install MetaTrader5                                    # Windows 단말 필요

python -m crowcode live --symbol XAUUSD --preset scalp      # 드라이런(기본)
python -m crowcode live --symbol XAUUSD --preset scalp --live   # 실주문
python -m crowcode live --paper --bars 25000                # 단말 없이 시뮬레이션
```

러너가 매 스텝 하는 일:

```
1. 포지션 관리   2R → 본절, 3R → 분할 (신규 진입보다 먼저)
2. 대기 주문     만료 / 전제 붕괴(종가가 SL 밖) 시 취소
3. 리스크 게이트 브로커 체결 내역으로 당일 손익·연속 손절 재구성
4. 신규 평가     새 봉이 마감됐고, 포지션·대기주문이 없을 때만
5. 주문 전송     최소 이격·랏 단위·스프레드 재검증 후
```

브로커 상태를 매번 다시 읽으므로 프로세스를 재시작해도 이어서 동작한다.
`PaperBroker` 덕분에 단말 없는 환경(리눅스/CI)에서도 실행 경로 전체를 테스트한다.

---

## 파이썬에서 쓰기

```python
from crowcode import CrowStrategy, Backtester
from crowcode.config import preset
from crowcode.data import load_csv
from crowcode.risk import RiskState
from crowcode.sessions import NewsEvent

series = load_csv("data/xauusd_m1.csv", "XAUUSD", "M1")
cfg = preset("scalp").with_(risk_pct=0.75, target_rr=4.0)

# 1) 최신 봉 기준 시그널
strat = CrowStrategy(cfg, "XAUUSD", news=[NewsEvent(nfp_time, "NFP")])
sig = strat.evaluate(series, balance=5000, risk=RiskState(balance=5000))
print(sig.pretty() if sig else strat.rejection_summary())

# 2) 같은 규칙 그대로 백테스트
res = Backtester(cfg, balance=5000, spread=0.20).run(series)
print(res.report())
```

---

## 모듈 구성

| 파일 | 역할 |
|---|---|
| `config.py` | 모든 규칙 파라미터 + 프리셋 3종 (swing / scalp / highrisk) |
| `data.py` | 캔들·시계열·리샘플·ATR, 다중 타임프레임 뷰(`MTFView`) |
| `structure.py` | 스윙 포인트, BOS, CHOCH (전부 인과적 계산) |
| `liquidity.py` | 유동성 풀, 스윕, 오더블록, FVG, POI 선정 |
| `wyckoff.py` | 레인지 탐지, Spring/Upthrust, Phase A~E |
| `waves.py` | 지그재그 레그, 엘리엇 3규칙 검증, ABC 조정 완료 판정 |
| `sessions.py` | 세션 창, 뉴스 블랙아웃, 금요일 마감 |
| `risk.py` | 사이징, 레버리지 상한, 본절/분할, 일일 한도, 계좌 3분할 |
| `gold.py` | XAUUSD 전용 — 심볼 이름 해석, 필요 자본, 사전 점검, 지표 목록 |
| `riskmath.py` | 손익분기 승률, 연패 분포, 낙폭 시뮬레이션 |
| `strategy.py` | 위 전부를 묶는 탑다운 파이프라인 |
| `backtest.py` | 이벤트 기반 백테스터 (SL 우선 체결, 스프레드 반영) |
| `mt5/broker.py` | 브로커 인터페이스 + 심볼 사양(랏 단위, 최소 이격, 틱 가치) |
| `mt5/paper.py` | 단말 없이 도는 시뮬레이션 브로커 |
| `mt5/terminal.py` | 실제 MetaTrader5 연결 (서버시간 보정, 체결방식 선택) |
| `mt5/runner.py` | 실전 루프 — 관리 → 대기주문 → 리스크 → 평가 → 전송 |
| `mt5/journal.py` | JSONL 기록 (기각 사유 집계 포함) |
| `mql5/Experts/CrowConcept.mq5` | 같은 규칙의 네이티브 EA |
| `mql5/Presets/*.set` | EA 프리셋 (config.py 에서 자동 생성) |

---

## 설계상 지킨 것

- **룩어헤드 없음** — 스윙은 확정 지연(`confirmed_at`)을 두고, 상위 타임프레임은
  마감된 봉만 쓴다. 시계열을 잘라서 평가해도 같은 시그널이 나오는지 테스트로 검증한다
  (`tests/test_strategy.py::TestNoLookahead`).
- **낙관 편향 없음** — 한 봉 안에서 SL·TP 가 모두 닿으면 항상 SL 체결로 처리한다.
- **기각 사유 보존** — 어떤 필터가 몇 번 걸렀는지 전부 집계된다.

## 주의

MQL5 EA 는 이 저장소에서 **컴파일·실행 검증을 하지 못했다** (MetaEditor 없는 환경).
Python 브릿지의 `Mt5Broker` 도 실제 단말 없이 작성됐다 — 러너·전략·리스크 경로는
`PaperBroker` 로 전부 테스트했지만 MT5 API 호출 자체는 Windows 에서 처음 돌려 봐야 한다.
반드시 데모 계좌에서 검증한 뒤 실계좌로 옮길 것.

합성 데이터는 무작위 보행이라 우위가 없다. `demo` 의 숫자는 규칙 작동 확인용이지
성과 근거가 아니다. 채널의 "월 30-50%", "승률 76%" 같은 주장은 검증할 수 없어
코드에 반영하지 않았다 — 자세한 제외 목록은 [docs/RULEBOOK.md](docs/RULEBOOK.md) 4장 참고.
실거래 전에는 본인 브로커의 실제 스프레드·스왑으로 반드시 재검증할 것.
