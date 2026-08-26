# MT5 에 붙이는 방법

두 가지 경로가 있다. **둘 다 같은 규칙**이지만 운영 방식이 다르다.

| | A. MQL5 EA | B. Python 브릿지 |
|---|---|---|
| 설치 | `.mq5` 하나를 컴파일해 차트에 붙임 | Windows + `pip install MetaTrader5` |
| 실행 | MT5 단말 안에서 자체 실행 | 별도 파이썬 프로세스가 단말에 붙음 |
| VPS | 단말만 켜 두면 됨 | 단말 + 파이썬 둘 다 |
| 전략 백테스트 | MT5 전략 테스터(실 틱) | `crowcode backtest` |
| 규칙 수정 | MQL5 재컴파일 | 파이썬 편집 후 재시작 |
| 추천 | **실전 운용** | 연구·검증·로그 분석 |

> 처음이라면 **A 를 데모 계좌 + `InpDryRun=true`** 로 최소 2주 돌려서
> 로그의 시그널이 납득되는지 먼저 확인할 것.

---

## A. MQL5 EA

### 1) 파일 배치

MT5 단말에서 `파일 → 데이터 폴더 열기` → `MQL5/Experts/` 안에
`mql5/Experts/CrowConcept.mq5` 를 복사한다.

### 2) 컴파일

MetaEditor(F4)에서 `CrowConcept.mq5` 를 열고 F7. 오류 0 이어야 한다.

### 3) 차트에 부착

- 매매할 심볼 차트를 연다 (예: XAUUSD).
- **차트 주기는 아무거나 상관없다.** EA 는 입력값의 HTF/MTF/LTF 를 직접 읽는다.
- 내비게이터에서 `CrowConcept` 를 차트로 드래그.
- `공용` 탭에서 **알고리즘 트레이딩 허용** 체크.

### 4) 프리셋 불러오기

일일이 입력할 필요 없다. `mql5/Presets/` 의 `.set` 파일을 EA 속성창에서
`불러오기(Load)` 하면 된다. 전부 `InpDryRun=true` 로 저장되어 있다.

| 파일 | 프레임 | 리스크 | 손절 폭 |
|---|---|---|---|
| `CrowConcept-swing.set` | D1 > H4 > H1 | 1.0% | $6 ~ $60 |
| `CrowConcept-intraday.set` **(권장)** | H4 > H1 > M15 | 1.0% | $2.5 ~ $25 |
| `CrowConcept-scalp.set` | H1 > M15 > M5 | 0.5% | $1.5 ~ $10 |
| `CrowConcept-highrisk.set` | M15 > M5 > M1 | 6.0% | $0.8 ~ $4 |

계좌 크기에 따른 선택 기준은 [docs/GOLD.md](GOLD.md) 2장에 있다.
이 파일들은 `python3 tools/gen_set_files.py` 가 `crowcode/config.py` 에서 생성하므로
파이썬 프리셋과 항상 같은 값이다 (테스트로 검증한다).

`InpMagic` 은 다른 EA 와 겹치지 않게 바꿀 것. 나머지는 그대로 두면 된다.

세션은 **GMT 기준**이다 (`TimeGMT()` 을 쓰므로 브로커 서버 시간 오프셋과 무관하다).
기본값 7-12 / 12-17 은 런던·뉴욕 세션이다.

뉴스 차단은 `InpNewsTimes` 에 GMT 시각을 세미콜론으로 나열한다:

```
2026.09.04 12:30;2026.09.11 12:30
```

### 5) 검증 순서

1. `InpDryRun=true` 로 두고 로그(`전문가` 탭)를 본다.
   `CrowConcept SIGNAL BUY LIMIT | entry=... rr=1:3.0` 같은 줄이 나온다.
2. 전략 테스터에서 **"모든 틱"** 또는 **"실제 틱"** 모드로 3~6개월 돌린다.
   ("Open prices only" 는 이 EA 에 무의미하다 — 봉 안의 SL/TP 순서가 결과를 바꾼다.)
3. 데모 계좌에서 `InpDryRun=false` 로 2주 이상.
4. 그다음에야 실계좌, 그것도 최소 랏으로.

---

## B. Python 브릿지

### 1) 사전 준비 (Windows 필수)

MetaTrader5 파이썬 패키지는 Windows 단말에만 붙는다.

```powershell
pip install MetaTrader5
git clone <repo> && cd trading2
```

MT5 단말에서 `도구 → 옵션 → 전문가 조언자` → **알고리즘 트레이딩 허용** 체크.

### 2) 사전 점검 — 먼저 이것부터

계좌·브로커·프리셋 조합이 실제로 돌아갈 수 있는지 확인한다.
"왜 한 번도 진입을 안 하지?" 의 원인은 대부분 여기서 잡힌다.

```powershell
python -m crowcode preflight --preset intraday          # 단말에 연결
python -m crowcode preflight --paper --balance 3000     # 표준 사양으로 미리
```

심볼 이름, 계약 크기, 최소 이격, 스프레드, 필요 자본, 설정 모순을 한 번에 본다.
`실패` 항목이 있으면 그대로는 매매가 안 된다.

### 3) 드라이런 (주문 전송 없음)

```powershell
python -m crowcode live --symbol XAUUSD --preset scalp --timeframe M1
```

기본이 드라이런이다. 주문을 만들되 **보내지 않고** 로그에 `dry:order` 로 남긴다.

### 4) 실주문

```powershell
python -m crowcode live --symbol XAUUSD --preset scalp --live `
    --magic 700911 --journal state/journal.jsonl
```

이미 로그인된 단말에 붙는다. 계정을 코드에서 지정하려면:

```powershell
python -m crowcode live --symbol XAUUSD --live `
    --login 12345678 --password "..." --server "Exness-MT5Real38"
```

> 비밀번호를 명령줄에 직접 넣으면 셸 히스토리에 남는다.
> 단말에 미리 로그인해 두고 `--login` 을 생략하는 쪽을 권한다.

### 5) 단말 없이 시뮬레이션

```powershell
python -m crowcode live --paper --bars 25000 --warmup 800 --preset scalp
```

`PaperBroker` 가 합성/CSV 데이터를 한 봉씩 흘리며 실전과 **같은 러너 코드**를 돌린다.
리눅스·맥에서도 동작하므로 규칙 변경 후 회귀 확인에 쓴다.

### 6) 주요 옵션

| 옵션 | 뜻 |
|---|---|
| `--live` | 실제 주문 전송 (없으면 드라이런) |
| `--magic` | 이 봇의 주문만 식별. **다른 EA 와 반드시 다르게** |
| `--max-spread` | 이보다 스프레드가 넓으면 진입 안 함 (포인트) |
| `--poll` | 폴링 간격(초). 평가는 새 봉이 마감될 때만 일어난다 |
| `--server-offset` | 서버시간 − UTC (시간). 생략하면 자동 추정 |
| `--symbol auto` | 마켓워치에서 금 심볼을 찾아낸다 (XAUUSD.m 등) |
| `--news` | `"CPI@2026-09-11 12:30, ..."` (GMT) 전후 진입 차단 |
| `--journal` | JSONL 기록 경로 |
| `--state` | 재시작 시 복구할 상태 파일 |
| `--once` | 1회만 평가하고 종료 (cron 용) |

---

## 공통 주의사항

### 서버 시간

MT5 가 주는 봉 시각은 **브로커 서버 시간**(대개 UTC+2/+3)이다.
- MQL5 EA 는 `TimeGMT()` 을 쓰므로 신경 쓸 필요 없다.
- Python 브릿지는 틱 시각과 실제 UTC 를 비교해 오프셋을 자동 추정한다.
  브로커가 특이하면 `--server-offset 3` 처럼 직접 지정한다.
  **세션 필터가 몇 시간 밀려 있으면 이 값을 의심할 것.**

### 심볼 접미사

브로커마다 `XAUUSD`, `XAUUSD.m`, `XAUUSDm`, `GOLD` 등으로 다르다.
Python 쪽은 `--symbol auto` 로 두면 마켓워치에서 찾아내고, 이름이 틀렸을 때도 보정해 준다.
MQL5 EA 는 **차트의 심볼을 그대로 쓰므로** 금 차트에 붙이기만 하면 된다.

### 최소 이격(stops level)

브로커가 요구하는 SL/TP 최소 거리보다 가까우면 주문이 거부된다(retcode 10016).
두 구현 모두 전송 전에 검사해서 미달이면 그 셋업을 버린다.
`preflight` 가 이 값과 프리셋의 최소 손절을 비교해 미리 알려 준다.

### 금 고유 사항

단위(랏·포인트·핍), 계좌 크기별 프리셋 선택, 스프레드 비율 필터, 스왑, 지표 차단은
전부 [docs/GOLD.md](GOLD.md) 에 정리했다. 실전 전에 한 번 읽을 것.

### 계좌 모드

헤징/네팅 모두 동작하지만, **한 번에 한 셋업**만 잡도록 되어 있으므로
같은 심볼에 다른 EA 를 같이 돌리면 매직 넘버를 반드시 분리할 것.

### 재시작

- MQL5: 최초 리스크를 터미널 전역변수(`CC_R_<ticket>`)에 저장한다. 단말을 껐다 켜도 유지된다.
- Python: `state/crowcode_state.json` 에 저장한다. 파일이 없으면 현재 SL 을 최초 리스크로
  간주하고 이어받되(`adopt` 로그), 그 포지션의 본절 시점은 원래와 달라질 수 있다.

### 검증되지 않은 것

- **MQL5 EA 는 이 저장소에서 컴파일·실행 검증을 하지 못했다** (MetaEditor 가 없는 환경).
  로직은 파이썬 구현과 1:1로 맞췄지만, 첫 컴파일 오류나 브로커별 주문 거부는
  직접 확인해야 한다.
- **Python 브릿지의 `Mt5Broker` 역시 실제 단말 없이 작성됐다.**
  `PaperBroker` 로 러너·전략·리스크 경로는 전부 테스트했지만(37개),
  MT5 API 호출 자체는 Windows 에서 처음 돌려 봐야 한다.
- 어느 쪽이든 **데모 계좌 검증을 건너뛰지 말 것.**
