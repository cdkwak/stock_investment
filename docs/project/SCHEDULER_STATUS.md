# 스케줄러 상태

상태: `현재 / 실환경 정의 확인 / 일부 운영 확인 대기`

확인 시각: `2026-08-31 14:46 KST`

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
활성 작업은 Data 13개, 프로젝트 상태 동기화 1개, Telegram 2개다. 실패한
Python PM 이벤트 러너 작업은 containment를 위해 제거되어 이 수치에 없다.

80개 Dataset Universe 가운데 **39개 자동화 활성 데이터셋이 19개 논리 레인에
모두 연결**돼 있다. Windows Data 작업 수는 13개로 유지했고, 한국장 일별
20:30 묶음을 계약 v5로 확장했기 때문에 데이터셋마다 별도 Windows 작업을
추가하지 않았다. 공급자 호출이 없는 v5 20:30 사전 검증은 13개 레인으로
통과했다. 현재 관리 자동화 Health는 **39/39 허용 상태**이고 런타임 검증
실패는 0개다. 비관리 연구·보존 행의 오래됨과 미확정까지 포함한 전체 Typed
Health 표시는 `DEGRADED`이며, 관리 자동화 실패를 뜻하지 않는다.

운영 판정은 `DEGRADED`를 유지한다. 활성 정의 중 중지된 작업이나 미연결
자동화 데이터셋은 없지만, 아침 작업과 Health 쓰기가 아직 여러 Windows
작업으로 나뉘어 있고 KB 계좌의 첫 자연 실행과 BOK 관찰은 아직 확인 전이다.
KR 묶음 v5의 첫 자연 20:30 실행은 source-width와 publication-boundary 불일치를
정확히 드러낸 뒤 부분 승격 없이 실패했고, 제한된 후속 복구와 API-zero replay가
완료됐다. Yahoo 현재 경로의 03:02 단일 실패는 03:32 자연 실행에서 17개 경로
모두 정상으로 회복됐다.

| 영역 | 설치 작업 | KST 주기 | 현재 경계 |
|---|---|---:|---|
| 글로벌 일별 | `STOCK_DATA_FRED_DAILY`, `STOCK_DATA_GLOBAL_ETF_SOXX_DAILY`, `STOCK_DATA_GLOBAL_INDEX_DAILY` | 06:00 / 06:10 / 06:20 | FRED H15/FX/VIX와 파생 미국채 스프레드 포함; 제한된 후속 FRED 실행이 VIX를 검증된 2026-08-26 발표 목표까지 승격하고 API-zero replay를 통과 |
| Health | `STOCK_DATA_DAILY_HEALTH` | 매일 06:30 | 공급자 호출 없이 typed universe 검증; 레인별 Health 쓰기와 일부 중복 |
| 프로젝트 상태 | `STOCK_PROJECT_ISSUE_STATE_SYNC` | 매일 06:45 | Data 작업 아님; 첫 자연 실행 확인 대기 |
| Toss 계좌 | `STOCK_DATA_TOSS_ACCOUNT_DAILY` | 매일 07:00 | 읽기 전용, 식별자 제거; 2026-08-27 자연 실행이 terminal receipt, snapshot digest, call budget을 통과 |
| KB 계좌 | `STOCK_DATA_KBSEC_ACCOUNT_DAILY` | 매일 07:10 | `SSQM2952` 읽기 전용, 식별자 제거, 첫 자연 실행 확인 대기 |
| Telegram 아침 | `STOCK_TELEGRAM_MORNING_BRIEF` | 평일 07:30 | Data 통합 대상 아님 |
| Toss 국내 현재 | `STOCK_DATA_TOSS_DOMESTIC_30M` | 평일 09:00~15:00, 30분마다 | 4개 화면용 현재 관측; 이력 데이터셋 아님 |
| 한국장 일별 묶음 | `STOCK_DATA_KR_MARKET_DAILY_0910`, `_1410`, `_2030` | 09:10 / 14:10 / 20:30 | 계약 v5. 첫 자연 20:30 실행은 두 contract mismatch를 fail-closed로 노출; source-width/same-day 경계 보정 후 breadth·short-investor·index-fundamental 복구와 API-zero replay 통과, 다음 자연 영수증 확인 필요 |
| Telegram 마감 | `STOCK_TELEGRAM_KR_CLOSE_BRIEF` | 평일 16:10 | Data 통합 대상 아님 |
| BOK 국채 증거 | `STOCK_DATA_BOK_TREASURY_DAILY` | 매일 17:10 | Landing-only 3회 발표·최종성 관찰; Canonical 금리 아님 |
| 글로벌 선물 | `STOCK_DATA_GLOBAL_FUTURES_DAILY` | 매일 22:10 | 최근 실행 결과 0 |
| Yahoo 현재 | `STOCK_DATA_YAHOO_MARKET_30M` | 매시 :02/:32 | 30분봉 13개와 공급자 원형 15분봉 4개; 03:32 17/17 회복 |
| 과거 KB 시장 스냅샷 | `StockInvestmentRev1-KBSecDailySnapshot` | 과거 평일 17:00 | 비활성 IVSA0070 시장 자료; KB 계좌 작업과 별개 |

구성된 KB 계좌는 존재한다. 허용된 런타임 설정과 식별자를 제거한 실검증
스냅샷이 있고, 07:10 단일 실행·5분 제한·이전 정상값 보존 정의도 설치됐다.
비활성 KB 작업은 계좌가 아니라 별도의 시장 스냅샷이므로 계좌 자동화로
세지 않는다.

## 한국장 묶음 v5

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
| P1 | KR 묶음 v5 보정 후 자연 실행 미확인 | 다음 09:10·20:30 영수증에서 20:30 13개 레인, 관리 Health 39/39와 현재 데이터의 API 0 재실행을 확인 |
| P1 | KB 계좌 자연 실행 미확인 | KB 07:10을 식별자 없는 영수증으로 확인; 실패 시 이전 정상값 보존 |
| P1 | BOK 발표·최종성 증거 미완료 | 17:10 세 차례 관찰 중 남은 배치를 완료한 뒤에만 Canonical 승격 여부를 판정 |
| P2 | 아침 작업 분산 | 레인별 영수증과 실패 격리를 보존한 단일 아침 오케스트레이션으로 통합 검토 |
| P2 | Health 중복 쓰기 | 레인별 장애 증거는 유지하고 최종 아침 Health 산출은 한 번으로 축소 검토 |
| P2 | 비활성 KB 과거 작업 | 참조와 보존 증거 확인 뒤 삭제 또는 격리 여부 결정 |
| P2 | 스케줄러 진단 로그 | 비활성 Operational 로그를 대신할 제한된 식별자 제거 로컬 증거 마련 |

목표 구성은 Telegram을 제외한 활성 작업을 BOK 관찰 기간에는 9개,
BOK 관찰 종료 뒤에는 8개까지 줄이는 것이다. 이 목표는 제안 상태이며,
이번 변경에서는 데이터별 실패 격리와 발표 시각을 보존하기 위해 Windows
작업 정의를 추가·삭제하지 않았다.

## Python PM 이벤트 러너

`STOCK_PROJECT_PYTHON_PM_EVENT_RUNNER`의 revision-4 정의는 프로젝트
`pythonw.exe`로
`scripts/maintenance/workflow_controller.py event-run-once`를 1분마다 한 번
실행하고, `IgnoreNew`, 10분 wake 경계보다 긴 15분 작업 제한, 현재 사용자
제한 권한과 정확한 소유권 표식을 사용한다. 콘솔 창, Orca, 공급자, 계좌,
브로커 호출이나 Queue 파일 직접 변경 경로는 없다.

이전 PT2M 실행은 부모 제한이 Codex wake 경계보다 짧아 실패했지만, 정확한
terminal pin 공개 조정 proof로 `f4885fa...` generation은 recovered로 보존됐다.
revision-4 Windows 작업은 `Install`=`PYTHON_PM_TASK_INSTALLED`,
`Check`=`PYTHON_PM_TASK_OK`로 정확히 설치·readback 됐다. 이어진
`a2f370...` material generation은 PM과 routed Lead의 direct wake receipt를
모두 보존해 woken으로 완료됐다. 이어진 proof-bound replay도 PM/Lead receipt와
pending 0으로 정산됐다. Queue lease heartbeat의 `updated_at`만 변한 경우는
material key에서 제외하므로 새 wake generation을 만들지 않는다.

Codex 앱 작업 ID는 조정 식별자로만 남고 무인 실행 대상으로 쓰지 않는다.
PM과 라우팅 Lead는 별도 CLI 소유 세션으로 정확히 이관됐으며, 보류된
material generation은 새 generation/session fingerprint로 재결합하되 완료
처리하지 않는다. pending operation이 남은 dead-writer 상태는 기존
`recover-stranded`를 그대로 사용한다. 현재처럼 자연 종료가 operation까지
terminal failed로 정리한 상태는 공개 `reconcile-terminal --preflight-only`로
exact prior generation/operation/request/profile/error/release pin, writer idle,
boundary pending 0, OS mutex available을 먼저 확인하고, 같은 공개 명령의
reconciliation receipt만 생성한다. 그 proof로 `event-recover-generation`이
기존 실패를 보존하고 fresh generation을 연다. 이 절차와 revision-4 task의
exact readback은 완료됐으며 PM/Lead receipt와 pending-zero/idle receipt도
보존됐다. 이후 실제 Queue 또는 Listener material 변화만 새 bounded wake를
시작한다. 새 Queue discovery 또는 triage count 변화가 live generation을 만들 수
있으므로, pending-zero/idle은 매번 현재 공개 runner/controller status에서만
주장한다.

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
