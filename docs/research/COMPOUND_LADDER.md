# 복리 사다리 백테스트 방법

## 목적과 경계

`compound-ladder/v2`는 여러 drawdown cycle을 하나의 계좌로 이어 붙이는 개발용 시뮬레이션이다. 계좌는 평상시에도 기초지수 1x core를 항상 보유하고 drawdown level이 올라갈 때 core 일부를 일일 재설정 k배 상품으로 옮긴다. 모든 판단 기준은 기초지수 1x를 첫날 100% 매수해 끝까지 보유한 baseline 대비 **FINAL WEALTH**다. 투자 추천, 실현 가능한 체결, 사용자 적합성 또는 실계좌 성과를 뜻하지 않는다.

v2 변경점은 v1의 기본 현금비중 100% 모델을 1x core 상시 보유 모델로 바로잡은 것이다. v1 방식은 `base_exposure=0.0`인 **현금 대기형** 보조 실험으로만 남기고, 헤드라인·탐색 winner·plateau·exit 순위·분할×multiple 표는 모두 `base_exposure=1.0`만 사용한다. 엔진 자체는 `base_exposure=b`를 `[0, 3]`에서 받되 `b≤k`를 강제한다. 별도 base-exposure sweep은 기존 grid 기본값을 바꾸지 않고 `b>1`인 항상-on 레버리지 위에 같은 사다리를 얹는다.

실행은 retained Parquet만 읽으며 API 호출, provider import, 계좌 접근, 주문 경로가 없다. 결과는 입력 Parquet의 경로·크기·SHA-256을 묶은 manifest digest와 함께 저장한다.

## 신호와 시계

기초지수 종가로 기존 `compute_signals`의 `drawdown252`, `disp60`, `rsi14` 등을 계산한다. 현재 규칙은 `kr_dd_ladder_2`를 그대로 옮긴 다음 두 조건이다.

- `drawdown252 <= -0.20`
- `disp60 <= -0.10`

각 행의 두 조건 점수는 0, 1, 2다. 민감도 분석의 분할 수 `n=1..4`는 `ceil(score / 2 × n)`개의 동일 크기 level로 비례 변환한다. `b=1` Core형의 목표 노출은 `e(L) = 1 + (k-1) × L/n`이고, 계좌의 `f(L)=L/n`을 1x core에서 k배 상품으로 옮긴다. `b>1`이면 level 0부터 k배 상품 비중 `f0=(b-1)/(k-1)`을 영구 보유하고 `f(L)=f0+(1-f0)×L/n`, 즉 유효 노출 `e(L)=b+(k-b)×L/n`으로 올린다. 최대 level에서는 k배 상품 100%, level 0과 exit 복귀 때는 `f0`다. `b=k`이면 이미 k배 상품 100%라 사다리 on/off가 동일하다. `k=1, b=1`은 모든 level과 exit에서 1x baseline과 정확히 같다. `0≤b<1`은 level 0에서 1x core를 자본의 `b`만큼 보유하고 남은 현금까지 단계적으로 k배 상품으로 옮겨 기존 `b=0` 현금 대기형과 `b=1` Core형을 연속적으로 잇는다.

신호는 종가 T까지의 값만 사용한다. T에서 관측한 level은 다음 retained session의 종가에서 실행하며 새 비중은 그 다음 close-to-close 수익부터 얻는다. 결측 신호는 거짓으로 바꾸거나 새 주문을 만들지 않고 직전 실행 상태를 유지한다. 이 계산 시계는 미래 가격을 신호에 넣지 않지만, retained 지수 종가의 원천 빈티지·당시 공개시각 자체가 역사적으로 PIT-safe였다고 주장하지 않는다.

## 연속 계좌와 exit

계좌는 처음부터 끝까지 하나이며 외부 현금 흐름이 없다. 현금 수익률 기본값은 0%다. 목표 비중 변경 때 매수·매도 각 leg의 notional에 편도 0.10% 비용을 적용한다. 따라서 core를 팔아 overlay를 사거나 overlay를 팔아 core를 사는 rebalance는 양쪽 leg 모두 비용을 낸다. 목표 비중과 거래비용을 동시에 만족하도록 self-financing 잔액을 계산한다.

- `a 점수 역주행`: 노출 `e`가 실행 level을 따라 오르내린다.
- `b60`, `b120 고정 기간`: episode 진입 때 즉시 최대 노출 `e(level_max)=k`로 올리고 60 또는 120 retained sessions를 유지한 뒤 core형은 1.0, 현금 대기형은 0으로 한 번에 복귀한다.
- `c 목표 수익 분할`: 각 overlay tranche 진입가격 대비 +30%, +60%, +90%에서 최초 overlay 수량의 1/3씩 팔아 core형은 1x로, 현금 대기형은 현금으로 옮긴다. level 하락만으로는 팔지 않는다.
- `d 안 팔고 보유`: 첫 `level>=1`에서 최대 노출로 올린 뒤 끝까지 de-lever하지 않는다.

`b>1`의 de-lever는 현금이나 1x 100%가 아니라 영구 기본 비중 `f0`로 돌아가는 것을 뜻한다. `d`는 첫 진입 뒤 최대 k배 비중을 유지하므로 복귀가 없다.

Baseline은 기초지수 1x를 첫 retained close에 100% 매수하고 끝까지 보유한다. Core형 전략과 baseline 모두 비용 on일 때 최초 1x 매수비용을 낸다. Cycle 표는 실행 level이 0에서 양수로 바뀐 구간을 episode로 정의하고 entry, 최대 level, 신호 종료, overlay가 0으로 돌아온 실제 de-lever일(없으면 null), 계좌 wealth 기여와 같은 날짜의 baseline 기여를 기록한다.

## 상품 수익률

기초지수 자체는 비교 원천이다. Synthetic 상품은 일일 재설정 수익률로 계산한다.

`r_product,t = k × r_index,t − expense/252 − (k−1) × short_rate_t/252 − tracking_drag/252`

`k`는 1, 2, 3이다. 연 비용은 1x 0.35%, 2x·3x 0.90%다. retained `fred_*`에 명시적인 3개월물 열이 있으면 과거 값만 forward-fill하고, 없으면 연 2.5% 상수를 쓴다. 거래비용은 상품 수익률에서 빼지 않고 계좌 거래 때 한 번만 적용한다.

Real ETF와 기초지수의 공통 날짜에서 `real log return − synthetic log return`을 연환산한다. 음의 gap만 `calibrated_extra_drag`로 바꾸어 전체 synthetic 역사에 일정하게 적용한 것이 “실제 상품 기준” variant다. 사용 mapping은 `123320→KOSPI/KOSPI200 2x`, `243880→KOSPI200_IT 2x`, `QLD/TQQQ→NASDAQ100 2x/3x`, `SOXL→SOX 3x`다. 짧은 겹침을 과거 전체에 일정 적용하는 강한 가정이다.

변동성 drag는 일일재설정 synthetic 최종배수와 `1 + k × (index final multiple − 1)`을 따로 보여 준다. 후자는 실제 투자경로가 아니라 단순 산술 비교다.

## 기간, grid, plateau

한 연속 equity curve에서 fit은 2015-12-31까지, hold-out은 2016-01-01부터, full은 전 기간이다. 이 `hold-out`은 compound-ladder가 미리 고정한 설명용 시간 분할이며 프로젝트 Phase-1의 2021-08-17 시작 sealed final holdout을 열거나 재사용한 것이 아니다. 기간 배수는 해당 구간 첫 wealth 대비 마지막 wealth이므로 split 경계의 보유 상태를 그대로 이어받는다.

Full grid는 아래 Cartesian product다.

- drawdown: `-0.10, -0.15, -0.20, -0.25, -0.30, -0.35`
- disp60: `-0.05, -0.10, -0.15`
- 분할 수: `1, 2, 3, 4`
- multiple: `1, 2, 3`
- base exposure: `0.0`(현금 대기형), `1.0`(1x core형)
- exit: `a, b60, b120, c, d`
- cost: off/on

각 row는 fit/hold-out/full의 최종배수, CAGR, MDD, 거래 수·회전·비용, 1x baseline, baseline 대비 배수와 최종부 차이를 가진다. Weekly equity curve는 core형 current rule, core형 fit-grid winner(명시적으로 exploratory), 1x baseline에만 넣는다.

Plateau는 `base_exposure=1.0`에서 `threshold×levels`, `levels×multiple`, `threshold×multiple` 세 surface의 나머지를 current 값과 `exit=a`, cost on으로 고정한다. Fit 최적 셀의 인접 grid index 최대 8개 평균을 계산한다. 최적 셀이 이 평균을 `0.25 × (최적 셀의 baseline 초과 edge)`보다 더 크게 이길 때만 `sharp peak`다.

## 산출물과 재현

```text
.venv\Scripts\python.exe scripts\research\run_compound_backtest.py --project-root . --baskets KR,US_TECH,SEMIS
.venv\Scripts\python.exe scripts\research\run_compound_backtest.py --project-root . --baskets KR,US_TECH,SEMIS --quick
.venv\Scripts\python.exe scripts\research\run_compound_backtest.py --project-root . --baskets KR,US_TECH,SEMIS --base-exposures 1.0,1.3,1.5,1.7,2.0
```

`artifacts/research/compound_ladder/grid_<basket>_<product>.json`은 UI용 row list이고 `summary.json`은 basket별 headline, 탐색 winner, plateau, 추적 gap과 입력 manifest를 모은다. FOREIGN 숫자는 이 실험에서 산출하지 않고 별도 `foreign_transfer` 시험으로 이관한다. 결과 문서는 `RESULTS_20260905_compound_ladder.md`다.

`--base-exposures`를 주면 기존 grid·summary를 쓰지 않고 현재 규칙, `k∈{2,3}`, exit `a,d`, 비용 on, 가능한 실제 상품 gap 보정만 실행한다. 지수별 `artifacts/research/compound_ladder/sweep_base_<basket>_<index>.json`과 통합 문서 `RESULTS_20260905_base_exposure_sweep.md`를 만든다. 각 파일은 사다리-on-base, 같은 `b`를 규칙 없이 영구 보유한 참조 행, 1x baseline의 fit/hold-out/full 최종부·MDD와 두 상대배수, 독립 cycle 수를 함께 보존한다. 독립 cycle은 양수 level 신호일을 모아 연속 신호일 사이 공백이 90 calendar day를 넘을 때 새 episode로 세는 기존 research event 규약이다.
