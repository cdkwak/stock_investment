# 조건 시나리오 백테스트

## 목적과 해석 범위

이 연구는 관심종목 알림 조건인 RSI14 ≤ 30, 종가/60일 이동평균 - 1 ≤
-10%, 종가/최근 252세션 최고 종가 - 1 ≤ -30%가 과거에 처음 진입했을
때의 이후 분포를 측정한다. 세 조건을 점수로 합친 `낙폭 과대` 후보도
2015년 말까지의 적합 구간에서만 고르고 2016년 이후에 따로 평가한다.

결과는 종가 기반 조건부 기술통계다. 체결 시점, 비용, 세금, 환율, 배당,
추적오차와 상품 실행 가능성을 포함하지 않으므로 투자성과나 추천이 아니다.

## 입력과 바스켓

실행기는 네트워크를 사용하지 않고 다음 retained Parquet만 읽는다.

- `kr_index_daily`: KOSPI와, 실제 보유돼 있다면 KOSPI200/IT 계열
- `kr_kospi200_index_daily`: 통합 KR index 자료에 KOSPI200이 없을 때의
  좁은 retained fallback
- `global_index_price_daily`: SP500, NASDAQ100, NASDAQ_COMPOSITE, SOX,
  DOW_JONES
- `kr_etf_price_daily`(`partitioning=None`)와 `kr_etf_master`: 계약상 레버리지
  배수가 1보다 큰 국내 ETF의 기술통계
- `global_etf_price_daily`: QLD, TQQQ, SOXL의 기술통계

KR은 KOSPI/KOSPI200/보유 시 IT, US_TECH는 NASDAQ100, SEMIS는 SOX다.
POOLED에는 이들과 SP500/NASDAQ_COMPOSITE/DOW_JONES가 함께 들어간다.
개별 종목은 지수가 주 분석 대상이고 현재 필요하지 않아 표본 추출하지 않는다.
실행 전후 모든 사용 Parquet의 상대 경로, 바이트 수, SHA-256을 비교하며 달라지면
실패한다. 동일 manifest는 출력의 `input_manifest.csv`와 집계 digest로 남는다.

## 시점 안전 신호와 사건

각 시계열을 날짜순으로 독립 계산한다.

- RSI14: 최초 14개 변화의 단순평균을 seed로 쓰는 Wilder 재귀식
- `disp60`: `close / rolling_mean_60(close) - 1`, 완전한 60세션 필요
- `drawdown252`: `close / rolling_max_252(close) - 1`, 완전한 252세션 필요
- `volume_ratio20`: volume이 있을 때 `volume / rolling_mean_20(volume)`

사건은 전일에 영역 밖이었다가 T일에 영역 안으로 들어온 경우만 센다. 첫 유효
행부터 이미 영역 안이면 이전 상태가 없으므로 사건이 아니다. 세 조건 동시는
세 신호 모두가 유효하고, 전일에는 동시 조건이 아니었다가 T일에 모두 충족할
때다. 신호 프레임에는 미래 수익률이나 결과 열을 두지 않는다.

결과 라벨은 별도 프레임에서 5/20/60/120 세션 후 종가 수익률과, 사건 종가를
포함한 해당 미래 경로의 실제 peak-to-trough 최대낙폭을 계산한다. 끝까지 완전한
기간이 없는 행은 그 기간 결과에서 제외한다. 같은 시계열·기간의 모든 유효 날짜
분포를 무조건 기준선으로 사용한다.

기존 `market_backtest` helper는 읽고 계약 적합성을 검토했다. 그러나
`labels.build_forward_labels`는 5/20/60만 지원하고 최대낙폭을 사건 종가 대비 최저
수익으로 정의하며, `holdout.define_untouched_holdout`은 마지막 5개 달력연도를
봉인하고, `walk_forward.expanding_walk_forward`는 관측수 기반 fold다. 이 연구의
120세션·고정 2016 경계·5개 달력연도 refit과 맞지 않아 최소 로컬 구현을 사용한다.

## 낙폭 과대 점수와 분할

각 축은 세 임계값을 넘을 때마다 1점씩, 합계 0~9점이다. 작은 사전 선언 grid는
다음 세 임계값 묶음과 trigger score 3/4/5/6의 곱이다.

| 축 | 후보 세 단계 |
|---|---|
| 252세션 낙폭 | -10/-20/-30%, -15/-25/-35%, -20/-30/-40% |
| 60일선 대비 | -5/-10/-15%, -8/-12/-18%, -10/-15/-20% |
| RSI14 | 40/30/20, 35/30/25, 30/25/20 |

점수 사건도 trigger 미만에서 이상으로 넘어갈 때만 센다. 기본 `N=30` 이상인
후보 가운데 적합 구간 60세션 평균수익이 가장 큰 것을 고른다. 동률은 적합
중앙값, 상승확률, 사건 수, 안정적인 임계값 ID 순으로 푼다. 홀드아웃 수치는
순위에 전혀 쓰지 않는다.

- 적합: 관측일이 2015-12-31 이하이면서 60세션 결과 종료일도 2015-12-31 이하
- 홀드아웃: 관측일이 2016-01-01 이상이고 완전한 60세션 결과가 있는 경우
- 통합 방식: POOLED에서 한 임계값을 선택한 뒤 각 바스켓에 그대로 평가
- 바스켓별 방식: KR/US_TECH/SEMIS/POOLED에서 각각 선택
- 안정성: 최소 10년의 이전 자료 뒤 5개 달력연도마다 과거 전부로 재적합하고
  다음 5개 달력연도에서 평가; 직전 fold와 임계값 ID가 같은지 표시

사건 수 15 미만인 집계 셀은 `low_sample=true`와 ⚠로 표시한다. 적합 이전 이력이
없는 SEMIS처럼 N을 충족할 수 없는 바스켓은 임계값을 억지로 만들지 않는다.

## 레버리지 ETF 제한

레버리지 ETF는 보유 이력이 짧고 특정 시장 국면에 집중돼 있다. 따라서 동일한
신호·사건·이후 분포만 별도 CSV로 기술하며, score grid, winner selection,
walk-forward에는 어떤 ETF 행도 넣지 않는다. 2025-04 저점에서 2026-06 고점으로
이어진 한 사이클을 임계값 학습 근거로 일반화하지 않는다.

## 실행과 산출물

```powershell
$env:PYTHONIOENCODING='utf-8'
.venv\Scripts\python.exe scripts/research/run_condition_backtest.py --project-root .
```

실행기는 `artifacts/research/condition_backtest/<YYYYMMDD>/summary.md` 경로를
stdout에 출력한다. 같은 폴더에는 입력 manifest, 시계열 범위, 조건 사건 원자료와
집계, 전체 점수 grid, 선택 임계값, walk-forward, 레버리지 ETF 사건과 집계 CSV가
생긴다. randomness와 네트워크 호출은 없다.
