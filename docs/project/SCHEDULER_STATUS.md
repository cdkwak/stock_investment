# 스케줄러 상태

상태: `현재 / 실환경 정의 확인 / 영수증 저하 / 20:30 자연 실행 대기`

확인 시각: `2026-09-02 KST`

이 문서는 설치된 Windows 작업 스케줄러 정의, 실행 주기, 정의 불일치와
통합 방향만 관리하는 짧은 현재 상태 문서다.

- [데이터 상태](../data/DATA_STATUS.md)와 선택된 실행 문서가 데이터별 의미,
  최신화 가능 시점, 승격 경계와 다음 실행을 결정한다.
- [스케줄러 데이터 연결표](../data/SCHEDULER_DATA_MAP.md)가 Windows 작업,
  논리 레인, 80개 Dataset Universe와 자동화 비활성 분류를 연결한다.
- [GUI 최신화 상태 계약](../gui/GUI_REFRESH_STATUS_CONTRACT.md)이 화면별 기준
  시각, 최신성, 마지막 정상 반영과 다음 반영 가능 시점을 관리한다.
- 실제 정의는 `Get-ScheduledTask`/`Get-ScheduledTaskInfo`, 실제 결과는
  `artifacts/scheduler_logs/`, Health와 각 운영 체크포인트가 최종 근거다.
  `LastTaskResult=0`만으로 데이터가 최신화됐다고 판단하지 않는다.

## 현재 요약

관련 작업 정의는 **17개**다. **16개가 활성**, **1개가 비활성 과거 작업**이며,
활성 작업은 Data 13개, 프로젝트 상태 동기화 1개, Telegram 2개다. 정리 2단계에서
비활성 저장소 로컬 PM 구현을 제거했으며, 그 전용 Windows 작업은 설치돼 있지
않았으므로 이 수치와 현재 스케줄러 운영에는 변화가 없다.

80개 Dataset Universe 가운데 **39개 자동화 활성 데이터셋이 19개 논리 레인에
모두 연결**돼 있다. Windows Data 작업 수는 13개로 유지했고, 한국장 일별
20:30 묶음을 계약 v5로 확장했기 때문에 데이터셋마다 별도 Windows 작업을
추가하지 않았다. 공급자 호출이 없는 v5 20:30 사전 검증은 13개 레인으로
통과했다. 2026-09-02 KST에 측정한 관리 자동화 Health artifact는
**39/39 허용 상태**이고 런타임 검증 실패는 0개다. 18:24 KST 네이티브
재계산은 2 `CURRENT`, 37 `EXPECTED_LAG`, 0 managed `STALE`, 0 invalid였다. 비관리
연구·보존 행의 오래됨과 미확정까지 포함한 전체 Typed Health 표시는
`DEGRADED`이며, 관리 자동화 실패를 뜻하지 않는다.

운영 판정은 `DEGRADED`를 유지한다. 활성 정의 중 중지된 작업이나 미연결
자동화 데이터셋은 없지만, 아침 작업과 Health 쓰기가 아직 여러 Windows
작업으로 나뉘어 있다. 2026-09-02 KST에 글로벌 commodity·ETF·index governing
영수증은 각각 `NOOP_CURRENT / api_calls=0 / scheduler_process_status=SUCCESS /
Health PASS 39/39`로 복구됐다. 14:10 KR 묶음은 다섯 API 호출로 세 Data 레인을
성공시켰지만, 당시 pre-20:30 Health 오분류 때문에 최종 결과 1을 남겼다. 이
분류기는 `6c6f32a`까지 보정·검증됐고, KR governing envelope는 20:30 자연
실행을 기다린다.
KR 묶음 v5의 첫 자연 20:30 실행은 source-width와 publication-boundary 불일치를
정확히 드러낸 뒤 부분 승격 없이 실패했고, 제한된 후속 복구와 API-zero replay가
완료됐다. Yahoo 현재 경로의 03:02 단일 실패는 03:32 자연 실행에서 17개 경로
모두 정상으로 회복됐다.

| 영역 | 설치 작업 | KST 주기 | 현재 경계 |
|---|---|---:|---|
| 글로벌 일별 | `STOCK_DATA_FRED_DAILY`, `STOCK_DATA_GLOBAL_ETF_SOXX_DAILY`, `STOCK_DATA_GLOBAL_INDEX_DAILY` | 06:00 / 06:10 / 06:20 | 기존 Yahoo S&P 500·NASDAQ Composite·NASDAQ-100 index와 SOXX는 유지. EWY·SOX(`^SOX`)·DOW_JONES(`^DJI`)는 `REGISTERED_NOT_YET_COLLECTED`; 기존 ETF/index task가 registry default로 다음 자연 실행에서 수집하며 새 Windows task는 없음 |
| Health | `STOCK_DATA_DAILY_HEALTH` | 매일 06:30 | 공급자 호출 없이 typed universe 검증; 레인별 Health 쓰기와 일부 중복 |
| 프로젝트 상태 | `STOCK_PROJECT_ISSUE_STATE_SYNC` | 매일 06:45 | Data 작업 아님; 첫 자연 실행 확인 대기 |
| Toss 계좌 | `STOCK_DATA_TOSS_ACCOUNT_DAILY` | 매일 07:00 | 읽기 전용, 식별자 제거; 2026-08-27 자연 실행이 terminal receipt, snapshot digest, call budget을 통과 |
| KB 계좌 | `STOCK_DATA_KBSEC_ACCOUNT_DAILY` | 매일 07:10 | `SSQM2952` 읽기 전용, 식별자 제거; 2026-09-02 자연 실행은 한 supplier call 뒤 fail-closed, 이전 정상 snapshot 보존, immutable failed receipt 유지. 별도 키의 수동 읽기 전용 refresh는 성공했지만 이 영수증을 덮어쓰지 않음 |
| Telegram 아침 | `STOCK_TELEGRAM_MORNING_BRIEF` | 평일 07:30 | Data 통합 대상 아님 |
| Toss 국내 현재 | `STOCK_DATA_TOSS_DOMESTIC_30M` | 평일 09:00~15:00, 30분마다 | 4개 화면용 현재 관측; 이력 데이터셋 아님 |
| 한국장 일별 묶음 | `STOCK_DATA_KR_MARKET_DAILY_0910`, `_1410`, `_2030` | 09:10 / 14:10 / 20:30 | 계약 v5. 09:10 결과 0. 14:10은 Canonical/Lending 갱신과 Short Selling no-op을 성공시켰지만 수정 전 Health 오분류로 결과 1; 분류 보정 뒤 native Health 39/39. 20:30 작업은 오늘 자연 13-lane 실행 대기 |
| Telegram 마감 | `STOCK_TELEGRAM_KR_CLOSE_BRIEF` | 평일 16:10 | Data 통합 대상 아님 |
| BOK 국채 증거 | `STOCK_DATA_BOK_TREASURY_DAILY` | 매일 17:10 | 3-batch gate reviewed, 후속 실행 API 0; permanent publication finality unknown이므로 Canonical 금리·예측·GUI 숫자 아님 |
| 글로벌 선물 | `STOCK_DATA_GLOBAL_FUTURES_DAILY` | 매일 22:10 | 기존 NQ=F/GC=F/CL=F 유지. SP500_FUTURES(`ES=F`)·DOW_FUTURES(`YM=F`)·DOLLAR_INDEX_FUTURES(`DX=F`)는 `REGISTERED_NOT_YET_COLLECTED`; 같은 task의 registry default가 다음 자연 실행에서 수집하며 새 Windows task는 없음 |
| Yahoo 현재 | `STOCK_DATA_YAHOO_MARKET_30M` | 매시 :02/:32 | 30분봉 13개와 공급자 원형 15분봉 4개; 03:32 17/17 회복 |
| 과거 KB 시장 스냅샷 | `StockInvestmentRev1-KBSecDailySnapshot` | 과거 평일 17:00 | 비활성 IVSA0070 시장 자료; KB 계좌 작업과 별개 |

활성 `STOCK_DATA_*` 작업 13개는 모두 `.venv\Scripts\pythonw.exe`를 직접
실행한다. `STOCK_DATA_TOSS_DOMESTIC_30M`도 등록 직후 임시 `cmd.exe` action을
동일한 비콘솔 Python action으로 교체하므로, 대화형 로그인 세션에서 주기 실행
때 검은 콘솔 창을 만들지 않는다.

구성된 KB 계좌는 존재한다. 허용된 런타임 설정과 식별자를 제거한 실검증
스냅샷이 있고, 07:10 단일 실행·5분 제한·이전 정상값 보존 정의도 설치됐다.
2026-09-02 자연 실행은 한 번의 supplier call 뒤 `KB_ACCOUNT_SUPPLIER_FAILED`로
fail-closed 처리됐고 이전 정상값과 식별자 없는 immutable receipt를 보존했다.
별도 키의 수동 읽기 전용 refresh 성공은 그 자연 실행의 실패를 재작성하지 않는다.
비활성 KB 작업은 계좌가 아니라 별도의 시장 스냅샷이므로 계좌 자동화로
세지 않는다.

## 한국장 묶음 v5

묶음 종료 후 Health 투영은 설명 상태이며, 관리 데이터셋의 오래됨·런타임 실패는
`DEGRADED` 목록으로 남기되 정상 레인의 성공과 프로세스 종료 코드 0을 바꾸지 않는다.
Health 보고서 구조 자체가 잘못된 `FAIL`만 전체 레인을 `FAIL_AFTER_HEALTH`로 표시한다.

20:30 슬롯은 다음 13개 레인을 실패 격리된 자식 작업으로 실행한다.

1. `CANONICAL_EQUITY_DAILY`
2. `KOSPI200_BREADTH_DAILY`
3. `SHORT_SELLING_DAILY`
4. `SHORT_SELLING_BALANCE_DAILY`
5. `SHORT_SELLING_INVESTOR_DAILY`
6. `LENDING_DAILY`
7. `VKOSPI_DAILY`
8. `KR_INDEX_DAILY`
9. `DERIVATIVES_PRICE_DAILY`
10. `MARKET_INVESTOR_DAILY`
11. `LIQUIDITY_CREDIT_DAILY`
12. `LS_T8462_DAILY`
13. `TOSS_KR_TREASURY_DAILY`

기존 묶음에 편입한 7개 데이터셋은 유동성, 신용잔고, 공매도 잔고,
공매도 투자자, LS t8462 Raw, BOK 국채 원천 관찰, Toss 국채 일별이다.
BOK 관찰은 발표 시각이 다른 기존 17:10 작업을 유지하며 20:30 묶음에
중복 추가하지 않았다. 여기에 이미 실행 경로가 있던
`kr_market_investor_trading_daily`도 `MARKET_INVESTOR_DAILY`의 명시적
자동화 데이터셋으로 바로잡았다.

실환경 한정 검증에서는 공매도 잔고가 2026-08-25, 공매도 투자자가
2026-08-27까지 진행됐고, exact KOSPI200 breadth chain과 index fundamentals는
2026-08-26까지 승격됐다. Toss 국채 6개 만기는 T+1 대상까지 한 거래로
갱신됐다. LS t8462는 2026-08-26의 18개 범위를 Raw로 보존했고
Normalized/Published 쓰기는 없었다. 첫 자연 실패 후의 제한된 복구 결과는
다음 자연 13-lane 영수증을 대체하지 않는다.

## 자동화 비활성 재분류

조사 대상 25개는 다음처럼 판정했다.

| 분류 | 개수 | 스케줄 판단 |
|---|---:|---|
| 즉시 자동화 | 1 | 기존 실행 경로가 있던 시장 투자자 원천을 현행 20:30 레인에 명시적으로 등록 |
| 이벤트형 | 5 | 배당·종목 마스터·권리·발행처럼 사건이 있을 때만 갱신; 매일 빈 행 생성 금지 |
| 수동 KB 시장 스냅샷 | 7 | 계좌가 아닌 현재 시장 조각; 원자성·복구·중복 역할 계약 전에는 과거 작업을 재활성화하지 않음 |
| Raw·연구 전용 | 7 | 데이터는 보존하되 최종성·PIT·현재 소비 경계가 부족해 자동 승격하지 않음 |
| 미구현 연구 계약 | 5 | 유사 데이터와 단위·키·공급자 동등성이 확인되지 않아 별칭이나 자동 대체로 처리하지 않음 |

조사 대상에 포함되지 않은 자동화 비활성 17개는 계약·의미 미확정 8개와
자동 갱신하지 않는 보존 자료 9개다. 조사 뒤 남은 24개와 합쳐 전체 41개며,
정확한 ID와 이유는 [스케줄러 데이터 연결표](../data/SCHEDULER_DATA_MAP.md)에
한국어로 유지한다.

`ls_t1633_program_trading_candidate`는 2026-08-26 재검증에서도 제한된
transient 재시도 뒤 공급자 오류가 반복됐다. 이전 정상 데이터 보호와
원자적 승격 경계는 통과하지 못했으므로 연구 전용으로 유지하며 스케줄에
넣지 않는다.

## 남은 문제와 다음 확인

| 우선순위 | 문제 | 다음 안전 작업 |
|---|---|---|
| P1 | KR 묶음 v5 보정 후 20:30 자연 실행 미확인 | 2026-09-02 20:30 영수증에서 13개 레인과 관리 Health 39/39를 확인; 현재 데이터는 각 레인의 계약에 따라 API 0이어야 하며 공급자 지연 레인은 typed 결과로 보존 |
| P1 | BOK permanent finality 미확정 | reviewed 3-batch/API-zero 경계를 유지하고 추가 공식 증거 없이 Canonical 승격·예측·GUI 숫자 사용 금지 |
| P2 | 아침 작업 분산 | 레인별 영수증과 실패 격리를 보존한 단일 아침 오케스트레이션으로 통합 검토 |
| P2 | Health 중복 쓰기 | 레인별 장애 증거는 유지하고 최종 아침 Health 산출은 한 번으로 축소 검토 |
| P2 | 비활성 KB 과거 작업 | 참조와 보존 증거 확인 뒤 삭제 또는 격리 여부 결정 |
| P2 | 스케줄러 진단 로그 | 비활성 Operational 로그를 대신할 제한된 식별자 제거 로컬 증거 마련 |

목표 구성은 Telegram을 제외한 활성 작업을 BOK 관찰 기간에는 9개,
BOK 관찰 종료 뒤에는 8개까지 줄이는 것이다. 이 목표는 제안 상태이며,
이번 변경에서는 데이터별 실패 격리와 발표 시각을 보존하기 위해 Windows
작업 정의를 추가·삭제하지 않았다.

## Retired orchestration boundary

정리 2단계에서 비활성 저장소 로컬 PM 구현과 전용 scheduler entry point를
제거했다. 삭제 전 상태는 `backup/repo-cleanup-phase2-20260903`에 보존돼 있으며,
현재 반복 Data 작업은 기존 Data-owned Windows 작업만 사용한다.

## 읽기 전용 확인

```powershell
Get-ScheduledTask |
    Where-Object {
        $_.TaskName -like "STOCK_*" -or
        $_.TaskName -eq "StockInvestmentRev1-KBSecDailySnapshot"
    } |
    Sort-Object TaskName |
    Select-Object TaskName, State

.\.venv\Scripts\python.exe .\scripts\maintenance\run_release_readiness_smoke.py `
    --output artifacts\release_readiness\release_readiness_latest.json
```

첫 명령은 정의만 읽는다. 두 번째 명령은 공급자를 호출하지 않지만 지정한
로컬 보고서를 갱신할 수 있으며 Windows 작업 정의는 바꾸지 않는다.
