# Stock Investment Rev1 — 1인 퀀트 투자자 GUI 감사

## 판정: FAIL

GUI는 10개 화면의 로컬 읽기·분석 기능을 안정적으로 실행하며 버튼 회귀나 Qt 런타임 오류는 발견되지 않았다. 그러나 현재 상태에서는 **GUI만 보고 투자 판단을 완결할 수 없다.** 시장, 후보 종목, 포트폴리오 위험, 검증 결과가 서로 다른 탭에 흩어져 있고, 이를 하나의 행동 결론과 신뢰 수준으로 결합하는 표면이 없다. 더구나 현재 관리 데이터 39개 중 3개가 stale인데 그 영향이 판단 화면의 차단 조건으로 전파되지 않는다.

### Hard gates

| Gate | 결과 | 근거 |
|---|---|---|
| 실제 상호작용 | PASS | 10/10 화면, 117/117 컨트롤 대조, 활성 104/104 실행, 미확인 0 |
| Qt 경고/오류 | PASS | `ledger.json` 0건, 정상 종료 |
| 1280×720 레이아웃 | FAIL | Research 출처 패널 내용 손실, 순자산 헤더 작업이 우측 화면 밖으로 이탈 |
| 데이터 의사결정 적합성 | FAIL | 자동 관리 36/39 정상; KOSPI200 구성·breadth 계열 3건 STALE |
| GUI 단독 투자 판단 | FAIL | 후보→근거→포트폴리오 위험→검증→행동을 묶는 판단 표면 없음 |
| 안전 경계 | PASS | 주문/이체/실공급자/스케줄러 미실행, 변경 경로는 임시 저장소에서만 실행 |

### Severity

- Critical: 1
- High: 6
- Medium: 1
- Total: 8

## TOP 5 — 영향도 × 구현 효율 순

1. **QI-01 투자 판단 Cockpit 부재** — 흩어진 기존 정보를 읽기 전용 결론표로 묶는 한 번의 구조 변경이 사용자의 최종 목표 전체를 직접 해결한다.
2. **QI-02 stale 데이터가 판단을 차단하지 않음** — 현재 실제 회귀검사 실패이자 잘못된 확신 위험이므로 새로운 시각적 장식보다 먼저 고쳐야 한다.
3. **QI-03 종목/ETF 첫 검색이 막다른 길** — 사용자가 분석할 대상을 선택하지 못하면 이후의 Research·Backtest·포트폴리오 기능이 모두 무용해진다.
4. **QI-05 포트폴리오 위험·포지션 크기 부재** — 종목 신호가 있어도 현재 보유와 합친 손실 가능성을 모르면 실행 가능한 판단이 아니다.
5. **QI-06 Backtest가 고정 개발 화면에 머묾** — 신호를 실제 후보·비용·벤치마크·홀드아웃과 연결해야 GUI의 결론을 검증할 수 있다.

## Findings

### QI-01 — 투자 판단 Cockpit이 없어 GUI 단독 판단을 완결할 수 없음

- **Layer:** 정보 구조 / 핵심 작업
- **Severity:** Critical
- **Persona impact:** 시장→후보→보유 위험→검증을 탭 사이에서 기억하고 수기로 결합해야 하므로 “오늘 무엇을 왜 얼마나 할지”를 GUI가 답하지 못한다.
- **Reproduce:** 1) Dashboard에서 시장 상태를 본다. 2) Research 또는 관심종목에서 후보를 찾는다. 3) 계좌와 Backtest로 이동한다. 4) 같은 후보에 대한 행동·확신·반증·포지션 크기를 한곳에서 찾는다.
- **Observed:** 각각의 화면은 부분 정보를 제공하지만 종목별 `관찰/매수 검토/보유/축소/관망`, 신뢰도, 근거 최신성, 현재 보유, 목표 위험, 반증 조건을 합친 표면이 없다.
- **Expected:** 첫 화면에서 시장 regime와 후보 순위, 근거/반증, 데이터 신뢰, 보유 영향, 다음 행동을 한 행 단위로 확인하고 원자료로 drill-down할 수 있다.
- **Evidence:** `evidence/Dashboard_2560x1440_before.png`, `evidence/Research_Workspace_1600x900.png`, `evidence/Account_2560x1440_before.png`, `evidence/Backtest_2560x1440_before.png`.
- **Suspected location:** `src/stock_data/gui/main_window.py:13088`–`13317`, `src/stock_data/gui/main_window.py:5478`.
- **Smallest possible patch:** 기존 읽기 전용 view model만 조합하는 `투자 판단` 첫 탭을 추가한다. 열은 종목, 행동 상태, 기술/실적/가치 점수, 데이터 기준시각, 신뢰도, 현재 비중, 위험예산 대비 목표 비중, 핵심 근거, 반증 조건이며 각 셀은 기존 탭으로 이동한다.

### QI-02 — 실제 stale 데이터가 투자 표면의 hard stop으로 전파되지 않음

- **Layer:** 데이터 신뢰 / 안전 피드백
- **Severity:** High
- **Reproduce:** 1) provider-free release-readiness 검사를 실행한다. 2) Data Status와 Dashboard를 연다. 3) KOSPI200 breadth 또는 구성종목 기반 판단이 가능한지 확인한다.
- **Observed:** 관리 데이터는 36/39만 적합하다. `kr_index_constituent_daily`, `kr_kospi200_breadth_daily`, `kr_kospi200_constituent_price_daily`가 기대 2026-08-28보다 하루 전인 2026-08-27에 멈췄다. GUI는 “확인 필요”를 보여 주지만 어떤 신호와 결론이 무효인지 한눈에 연결하지 않는다.
- **Expected:** stale 데이터가 의존하는 시장 폭·구성종목 신호는 `판단 보류`로 명시되고, 영향받는 카드/후보와 복구 작업이 연결돼야 한다.
- **Evidence:** 회귀검사 `1 failed, 268 passed, 1 skipped`; `INTERACTION_MANIFEST.md`; `evidence/Data_Status_1600x900.png`.
- **Suspected location:** `src/stock_data/gui/health_service.py:43`–`105`, `src/stock_data/gui/refresh_status.py:261`–`276`, Dashboard/Research view-model 조립부.
- **Smallest possible patch:** 데이터 의존성→화면/신호 매핑을 추가하고, `STALE/UNKNOWN`이면 해당 결론을 숫자 대신 `판단 보류`로 렌더링하며 Data Status의 해당 행으로 바로 가는 버튼을 제공한다. 동시에 세 데이터 갱신 실패를 Data Lead가 복구한다.

### QI-03 — 기본 종목·ETF 검색 예시가 실패하고 다음 행동이 없음

- **Layer:** 첫 사용 / 후보 선택
- **Severity:** High
- **Reproduce:** 1) 종목 차트에서 내장 예시 `삼성전자 005930`을 실행한다. 2) 미국 ETF에서 `SOXX`를 실행한다. 3) 실패 후 가능한 복구를 찾는다.
- **Observed:** 국내는 식별정보를 읽거나 검증할 수 없고, SOXX는 승인된 13개 범위에서도 일치하지 않는다. 화면 대부분이 빈 공간이며 `차트 보기`와 `현재 종목 추가`가 비활성화된다.
- **Expected:** 예시는 현재 로컬 카탈로그에서 반드시 성공해야 한다. 카탈로그가 없으면 예시 대신 데이터 상태와 복구 버튼을 보여야 한다.
- **Evidence:** `evidence/Equity_1600x900.png`, `evidence/US_ETF_1600x900.png`; 비활성 컨트롤 4건은 `inventory.json`.
- **Suspected location:** `src/stock_data/gui/main_window.py:8990`–`9110`, `src/stock_data/gui/services.py:2818`.
- **Smallest possible patch:** 시작 예시를 현재 로컬 카탈로그의 첫 유효 항목에서 생성하고, 카탈로그 0건이면 `데이터 상태 열기`와 필요한 데이터명을 포함한 단일 복구 상태를 렌더링한다.

### QI-04 — Research가 내부 계약 문자열을 노출하고 복구 버튼이 없음

- **Layer:** 신뢰 설명 / 오류 복구
- **Severity:** High
- **Reproduce:** 1) Research Workspace를 연다. 2) 후보 입력이 없는 상태의 요약과 출처 패널을 읽는다. 3) 포인터로 복구를 시도한다.
- **Observed:** `LOCAL_CANDIDATE_INPUT_MISSING`, `recovery=Data`, 내부 데이터셋 ID, `exact typed view`가 그대로 노출된다. `현재 후보 새로고침`은 같은 실패를 반복하고, 정확한 종목 선택은 Ctrl+K 문장만 있다.
- **Expected:** 사용자 언어로 원인·영향·다음 행동을 설명하고 `종목 선택`, `데이터 상태 열기`, `복구 방법`이 실제 버튼이어야 한다.
- **Evidence:** `evidence/Research_Workspace_1600x900.png`, `evidence/Research_Workspace_1280x720.png`.
- **Suspected location:** `src/stock_data/gui/services.py:132`, `src/stock_data/gui/main_window.py:12468`–`12650`.
- **Smallest possible patch:** 공용 provenance/error formatter를 두고 기술 토큰은 `기술 세부정보`에 접는다. 빈 차트와 후보 오류에 기존 전역 종목 선택기 및 Data Status로 연결하는 두 버튼을 추가한다.

### QI-05 — 보유 화면에 포트폴리오 위험과 포지션 크기 판단이 없음

- **Layer:** 포트폴리오 의사결정
- **Severity:** High
- **Reproduce:** 1) 합성 보유 데이터가 채워진 계좌 화면을 연다. 2) 신규 후보를 추가할 수 있는 최대 비중과 현재 위험 기여도를 찾는다.
- **Observed:** 자산 배분·평가금액·가치 이력은 보이지만 섹터/통화/팩터 노출, 상관, 변동성, 최대낙폭, VaR/ES 또는 간단한 위험예산, 종목별 위험 기여도와 목표 포지션이 없다.
- **Expected:** 현재 보유와 후보를 합쳤을 때 위험예산 초과 여부와 권장 가능한 최대 비중을 읽을 수 있어야 한다.
- **Evidence:** `evidence/Account_2560x1440_before.png`.
- **Suspected location:** `src/stock_data/gui/main_window.py:3286` 이후 Account 렌더링과 계좌 presentation view model.
- **Smallest possible patch:** 우선 기존 가격/비중으로 계산 가능한 `집중도`, `통화 노출`, `최근 변동성`, `최대낙폭`, `위험예산 대비 비중` 5개 읽기 전용 카드를 추가하고, 계산 불가 항목은 근거 누락을 명시한다.

### QI-06 — Backtest가 고정 개발 시나리오라 판단 근거로 쓸 수 없음

- **Layer:** 검증 / 결과 해석
- **Severity:** High
- **Reproduce:** 1) Backtest를 연다. 2) 오프라인 실행과 번들 재읽기를 실행한다. 3) 현재 선택 종목·신호의 성과, 비용, 벤치마크, holdout을 찾는다.
- **Observed:** 고정 RSI14 30/70, `DEVELOPMENT ONLY`, `NOT_EXECUTABLE_INSTRUMENT`, 결과 없음과 검은 빈 차트가 중심이다. 현재 후보나 포트폴리오와 연결되지 않는다.
- **Expected:** 선택 종목/신호, 기간, 거래비용, 벤치마크, walk-forward/holdout 상태와 결과의 신뢰 한계를 한 흐름으로 보여야 한다.
- **Evidence:** `evidence/Backtest_1600x900.png`, `evidence/Backtest_2560x1440_before.png`.
- **Suspected location:** `src/stock_data/gui/main_window.py:10243`–`10880`.
- **Smallest possible patch:** 빈 검은 플롯을 결과 안내 카드로 대체하고, 기존 검증 번들에서 선택 종목·기간·비용·벤치마크·holdout 상태를 읽어 상단 요약에 표시한다. 실행 파라미터 확장은 별도 후속 작업으로 둔다.

### QI-07 — 1280×720에서 Research와 순자산 핵심 내용이 화면 밖으로 이탈

- **Layer:** 반응형 레이아웃
- **Severity:** High
- **Reproduce:** 1) 창을 1280×720으로 맞춘다. 2) Research 기본/all-open을 연다. 3) 순자산·증감 헤더를 연다.
- **Observed:** Research `출처·상태` 문장이 아래로 잘리고 세로 복구가 없다. 순자산은 `이 날짜 스냅샷 삭제`와 `로컬 새로 읽기`가 우측 화면 밖으로 나가며 하단 가로 스크롤이 유일한 발견 수단이다.
- **Expected:** 신뢰 정보와 안전한 재읽기 작업은 노트북 최소 크기에서도 스크롤 없이 보이거나 명확한 세로 흐름 안에서 접근 가능해야 한다.
- **Evidence:** `evidence/Research_Workspace_1280x720.png`, `evidence/Net_Worth_1280x720.png`.
- **Suspected location:** `src/stock_data/gui/main_window.py:4625`–`4685`, `src/stock_data/gui/main_window.py:12468`–`12650`.
- **Smallest possible patch:** 순자산 헤더를 제목행+작업행으로 나누고 위험 작업은 관리 메뉴로 이동한다. Research는 1280 이하에서 compact preset을 적용하고 전체 페이지 세로 스크롤을 제공한다.

### QI-08 — 밝은 청색 화면과 한영 혼용이 작업공간의 상태 언어를 약화함

- **Layer:** 시각 체계 / 지역화
- **Severity:** Medium
- **Reproduce:** 모든 최상위 탭을 연속해서 본다.
- **Observed:** 전체 stock GUI는 밝은 청백색이지만 관제 화면의 어두운 주황 체계와 다르다. 탭과 제목에 `Dashboard`, `Index Graph`, `Research Workspace`, `Data Status`, `Backtest`, 한국어가 혼재한다. 주황은 주로 경고에만 등장해 우선 행동과 단순 주의의 차이가 약하다.
- **Expected:** 장시간 관찰에 적합한 어두운 중립 바탕, 제한된 주황 강조, 상승/하락/주의/불확실성의 일관된 의미, 한국어 기본과 기술 세부정보 분리가 필요하다.
- **Evidence:** 모든 `*_1600x900.png`, 특히 `Dashboard_1600x900.png`와 `Backtest_1600x900.png`.
- **Suspected location:** `src/stock_data/gui/main_window.py:13301`–`13317`, `src/stock_data/gui/main_window.py:13469` 이후 QSS.
- **Smallest possible patch:** 현재 QSS 값을 `surface/text/border/accent/warning/positive/negative` 토큰으로 추출하고 dark 기본 팔레트+주황 accent를 적용한다. 최상위 탭은 `시장 요약/지수 차트/종목 차트/미국 ETF/종목 연구/관심종목/데이터 상태/계좌·순자산/전략 검증`으로 통일한다.

## 유지할 점

- 주문·이체 기능이 없고 로컬 읽기 경계가 명확하다.
- 잘못된 숫자를 추정해 채우지 않고 unavailable 상태를 표시한다.
- 실제 104개 활성 컨트롤 실행에서 Qt 경고/오류가 없고 종료가 깨끗하다.
- Index Graph는 가격·지표·출처 상세를 한 화면에서 비교적 명확하게 제공한다.
- 1280 Dashboard의 지표 도구줄은 이전 감사보다 훨씬 잘 줄바꿈된다.

## 구현 묶음 제안

1. **Decision UX Lead:** QI-01, QI-03, QI-04, QI-07, QI-08 — 판단 Cockpit과 후보/복구 흐름, 반응형·테마·문구.
2. **Data Truth Lead:** QI-02 및 QI-03의 카탈로그 전제 — stale 3건 복구, 데이터 의존성/신뢰도 전파.
3. **Quant Validation Lead:** QI-05, QI-06 — 포트폴리오 위험요약과 검증 번들 해석.

각 Lead는 Worker를 병렬로 깨우고, Worker가 독립 Reviewer를 호출한다. Reviewer PASS 뒤 Lead가 통합 판단하고 PM에 상신한다. PM만 Queue lifecycle을 변경한다.

## Self-critique

PM이 깨운 독립 Reviewer의 KEEP / GENERIC / DUPLICATE 판정을 반영한 뒤 이 초안을 `REPORT.md`로 승격한다.

## HOLD THIS IN YOUR HANDS

지금의 앱은 숫자를 함부로 꾸며내지 않는 성실한 연구용 바인더에 가깝다. 페이지를 넘기면 시장, 종목, 계좌, 검증 기록이 각각 꽤 진지하게 정리돼 있지만, 책상 위에서 실제 돈을 움직일 순간에는 내가 다시 종이를 펼쳐 결론을 조립해야 한다. 데이터가 낡았을 때 멈추는 태도와 로컬 읽기 안전성은 꼭 보존하고 싶다. 그 위에 오늘의 후보와 반증, 현재 보유가 감당할 위험, 검증의 한계를 한 장에 묶고 어두운 화면에서 우선순위만 주황으로 살아나게 하면 매일 손이 가는 개인 운용 콘솔이 될 수 있다. 지금은 보유하고 참고할 도구지만, 단독으로 투자 결정을 맡길 물건은 아니다.

