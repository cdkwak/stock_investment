# 저장소 정리 인벤토리 — 2026-09-03

상태: **2단계 제거 반영 / 3단계 문서 정리 기준 / 커밋 미수행**

역할: 이 문서는 2026-09-03 저장소 정리 범위와 단계 기록이며, 현재 프로젝트 우선순위와 실행 권위는 [Project Status](PROJECT_STATUS.md)다.

## 1. 조사 기준과 결론

이 문서는 `AGENTS.md → PROJECT_STATUS → 각 Domain Status → REPOSITORY_MAP` 순서와
2026-08-15 정적 사용 감사(`docs/archive/project/audits/repository_usage_20260815/`)를
따랐다. 크기는 파일 바이트 합계이고, 마지막 변경일은 각 경로에 대해
`git log -1 --format=%cd --date=short -- <path>`로 측정했다. 추적 여부와 개수는
`git ls-files` 기준이다. 참조 검색 범위는 `src/`, `scripts/`, `tests/`, `docs/`,
`README.md`, `AGENTS.md`이며 `docs/archive/**`는 제외했다.

요청한 GNU `du`와 `grep`도 실행했으나, 이 관리형 Windows 세션에서는 두 프로그램
모두 MSYS `CreateFileMapping ... Win32 error 5`로 실패했다. 따라서 크기는 Python
3.13 읽기 전용 순회로, 참조는 `rg`로 재측정했다. `.tmp/`에는 ACL 거부 경로가
하나 있어 `rg --files`가 읽을 수 있는 파일 기준이며, 아래 값은 안전한 하한이다.

핵심 결론은 다음과 같다.

| 판단 | 근거 |
|---|---|
| 비활성 저장소 로컬 PM 하위시스템은 2단계에서 제거됨 | 삭제 전 상태와 정확한 파일 목록은 `backup/repo-cleanup-phase2-20260903`에 보존된다. 현재 소비자 Dashboard와 Data scheduler는 이 하위시스템을 사용하지 않는다. |
| Queue 전체는 잔해가 아님 | `scripts/request_queue.py`, `.agents/skills/request-queue/`, `artifacts/request_queue/`는 `AGENTS.md`, Issue State, Telegram bridge가 현재 참조한다. 제거된 PM 하위시스템과 file-backed Queue 생명주기를 구분한다. |
| PySide6 폴더는 지금 삭제할 수 없음 | 웹 앱이 `stock_data.gui.services`, `query`, `health_service`, `account_snapshot_service`, `manual_account_store`, `net_worth_service`, `watchlist_service` 등을 직접 import한다. 비-Qt 서비스를 중립 패키지로 옮긴 뒤 Qt 파일을 퇴역해야 한다. |
| 가장 즉시 회수 가능한 공간은 `.tmp/` | 접근 가능한 파일만 8.61 GB, 파일 209,608개, 파일이 존재하는 `pytest-*` 디렉터리 449개다. 단, 실행 중 agent 경로를 먼저 배제해야 한다. |
| Git churn의 중심은 생성 산출물 | 조사 당시 최근 7일 `artifacts/**/*.json|csv` 중 추적 경로는 176개(JSON 162, CSV 14)였다. Queue 상태와 정적 GUI evidence가 대부분이었고, 후자는 2단계에서 제거했다. |

분류 뜻은 `삭제(명백한 잔해)` = 재생성 가능하고 현재 참조가 없는 생성물,
`보관 브랜치로 이동` = 코드·문서·증거이나 현재 런타임에서 미사용,
`유지` = 현재 계약·런타임·복구 경로가 사용,
`웹 전환 후 퇴역` = 웹 parity와 서비스 분리 완료 전 보존이다.

## 2. 저장소 루트

| 경로 | 크기 / 파일 수 | Git 추적 | 마지막 변경일 | 비-archive 참조 | 분류 |
|---|---:|---:|---|---|---|
| `c/` | 0 B / 0 | 아니오 | Git 기록 없음 | 의미 있는 정확 경로 참조 없음(일반 문자열 `c/` 오탐만 존재) | 삭제(명백한 잔해) |
| `debug.log` | 87,036 B / 1 | 아니오(ignored) | Git 기록 없음; FS 2026-09-02 | `.gitignore`만 참조 | 삭제(명백한 잔해) |
| `GATES.md` | 2,380 B / 1 | 아니오(ignored) | Git 기록 없음; FS 2026-09-03 | `.gitignore`; 현재 `local read-only web dashboard` 완료 ledger | 유지 |
| `uv.lock` | 550,436 B / 1 | 아니오(untracked) | Git 기록 없음; FS 2026-09-02 | 없음 | 삭제(명백한 잔해) |
| `.unlazy/` | 885,919 B / 123 | 아니오(ignored) | Git 기록 없음; FS 2026-09-03 | `.gitignore`; 2026-09-03 Yahoo 작업 및 lock 흔적 존재 | 유지(활성 세션 종료 후 재감사) |
| `.tmp/` | **8,611,489,968 B / 209,608+** | 아니오(ignored) | Git 기록 없음; FS 2026-09-02 | `AGENTS.md`, 테스트·runbook의 임시경로 계약 | 삭제(명백한 잔해; 실행 중 agent 제외) |
| `.agents/` | 35,841 B / 17 | 예, 17 | 2026-08-31 | `AGENTS.md`, `REPOSITORY_MAP.md`, `PROJECT_GOAL.md`, control-plane 테스트 | 유지 |
| `.codex/` | 18 B / 1 | 예, 1 | 2026-08-31 | `REPOSITORY_MAP.md`; `hooks.json`은 현재 `{ "hooks": {} }` | 보관 브랜치로 이동 |
| `.worktrees/` | 0 B / 0 | 아니오(ignored) | Git 기록 없음 | `.gitignore`, `REPOSITORY_MAP.md`만 참조 | 삭제(명백한 잔해) |
| `.vscode/` | 691 B / 1 | 예, 1 | 2026-08-27 | 코드 참조 없음; 대형 data/venv watcher 제외 설정 | 유지 |
| `__pycache__/` | 5,205 B / 1 | 아니오(ignored) | Git 기록 없음; FS 2026-09-02 | `.gitignore`와 정리 도구만 참조 | 삭제(명백한 잔해) |
| `artifacts/recovery/acl_denied_dirs.txt` | 44,662 B / 1 | 아니오(ignored) | Git 기록 없음 | `.gitignore`만 참조 | 삭제(명백한 잔해) |
| `app.py` | 4,091 B / 1 | 예, 1 | 2026-09-02 | `README.md`, `GUI_STATUS.md`, release-readiness 코드/테스트 | 웹 전환 후 퇴역 |
| `src/stock_data/gui/` | 1,521,242 B / 24 소스 파일 | 예, 24 | 2026-09-03 | `src/stock_web/api/*.py`, `tests/unit/web/*`, GUI Status와 다수 테스트 | 웹 전환 후 퇴역 |

`.tmp/`의 `pytest-*` 개수 449는 `rg --files`에 보이는 파일 보유 디렉터리의 고유
경로 수다. ACL 거부 또는 빈 디렉터리는 포함되지 않아 실제 수는 같거나 더 많다.

## 3. `artifacts/` 최상위 폴더

파일 수는 현재 디스크, 추적 수는 Git index 기준이다. 최근 변경은
`git log --since=7.days --name-only -- artifacts`에서 날짜별 고유 경로 수를 센 값이며,
현재 삭제된 과거 경로도 해당 날짜의 변경 수에는 포함될 수 있다.

| 경로 | 크기 | 파일 수 | Git 추적 파일 | 마지막 변경일 | 비-archive 참조 예 | 최근 7일 변경(날짜:파일) | 분류 |
|---|---:|---:|---:|---|---|---|---|
| `artifacts/analysis/` | 175,422 B | 1 | 1 | 2026-09-02 | `gui/services.py`, `DATASET_INDEX.md` | 08-28:1, 08-31:1, 09-02:1 | 유지 |
| `artifacts/backtest/` | 27,963,738 B | 51 | 45 | 2026-08-28 | replay/overnight entrypoint와 Backtest 문서 | 08-28:45 | 유지 |
| `artifacts/daily_health/` | 188,883 B | 4 | 3 | 2026-08-28 | scheduler, Issue State, Dataset Index | 08-28:4 | 유지 |
| `artifacts/data_inventory/` | 84,504 B | 2 | 2 | 2026-09-03 | `REPOSITORY_MAP.md`, `DATASET_INDEX.md`, tests | 08-28:2, 09-03:1 | 유지 |
| `artifacts/gui/` | 0 B | 0 | 0 | 없음 | `docs/gui/DESIGN.md`가 경로만 언급 | 없음 | 삭제(명백한 잔해) |
| `artifacts/gui_validation/` | 2,637,333 B | 27 | 27 | 2026-08-28 | retention 도구와 테스트 | 08-28:27 | 웹 전환 후 퇴역 |
| `artifacts/issue_state/` | 209,529 B | 2 | 2 | 2026-09-02 | Issue State contract/code/test | 08-28:2, 09-02:1 | 유지 |
| `artifacts/local_user/` | 65,913 B | 6 | 0 | Git 기록 없음 | 웹/Qt 계정·watchlist 서비스와 테스트 | 없음 | 유지 |
| `artifacts/recovery/` | 185,309 B | 3 | 2 | 2026-09-02 | 정확 경로 참조 없음 | 09-02:2 | 보관 브랜치로 이동(`acl_denied_dirs.txt`는 루트 잔해 단계) |
| `artifacts/release_readiness/` | 54,141 B | 11 | 11 | 2026-09-02 | release-readiness entrypoint, Repository Map | 08-28:7, 09-02:4 | 유지(날짜별 JSON 보존정책 필요) |
| `artifacts/request_queue/` | 565,872 B | 343 | 343 | 2026-08-31 | `AGENTS.md`, README, Repository Map, Issue State | 08-28:217, 08-29:40, 08-30:29, 08-31:113 | 유지 |
| `artifacts/runtime_logs/` | 1,277,977 B | 1,279 | 1 | 2026-08-28 | diagnostics, replay/ML, Issue State | 08-28:1 | 유지 |
| `artifacts/scheduler_logs/` | 52,811 B | 32 | 0 | Git 기록 없음 | Scheduler Status, web/GUI services/tests | 없음 | 유지 |

`request_queue`의 일별 변경량이 큰 것은 단순 캐시가 아니라 file-backed 상태 이동과
Done receipt까지 Git에 기록하는 설계 때문이다. Queue를 유지하는 동안 무조건 ignore하면
복구 계약을 깨뜨린다. 조사 당시 런타임 비의존 정적 evidence 여섯 묶음은 2단계에서
제거했고 `backup/repo-cleanup-phase2-20260903`에 보존했다.

## 4. 비활성 저장소 로컬 PM 하위시스템과 Queue 구분

초기 조사에서 PM 전용 소스 26개(817,116 B / 18,763줄), 전용 entry/display
파일 3개, 전용 테스트 22개는 현재 소비자 웹 앱과 일반 Data 작업에서 사용되지
않는 것으로 확인됐다. 이 세트는 2단계에서 제거했고, 삭제 전 정확한 경로·크기·
줄 수·테스트 목록은 `backup/repo-cleanup-phase2-20260903`에 보존했다. 현재 문서는
그 제거 세트를 실행·복구 경로로 제공하지 않는다.

`scripts/request_queue.py`, `.agents/roles/`, `.agents/skills/`,
`artifacts/request_queue/`, Issue State 동기화는 제거 대상이 아니며 각각 현재 Queue
CLI, role packet, router/queue/data 절차, file-backed 상태/receipt, local issue
projection 역할을 유지한다.

### 4.2 역할/skill과 테스트

| 경로 | 크기 / 줄 수 | 추적 | 마지막 변경일 | 외부 참조 | 분류 |
|---|---:|---:|---|---|---|
| `.agents/roles/` (8파일) | 16,343 B / 296줄 | 예, 8 | 2026-08-31 | `AGENTS.md` Queue role packet 규칙 | 유지 |
| `.agents/skills/` (9파일) | 19,498 B / 310줄 | 예, 9 | 2026-08-31 | `AGENTS.md`, 현재 세션 router/queue/data 절차 | 유지 |

초기 분류의 24개 테스트 중 Queue/Issue State 소유 2개는 유지했고, 제거된 PM
하위시스템 전용 22개는 2단계에서 함께 제거했다. 삭제 전 개별 모듈명, 크기,
줄 수, 날짜는 `backup/repo-cleanup-phase2-20260903`에 보존한다.

## 5. `docs/` 현황과 router 미직접참조 문서

### 5.1 최상위별 개수

| `docs/` 하위 | 파일 수 | 크기 | 분류 |
|---|---:|---:|---|
| 루트(`docs/README.md`) | 1 | 별도 라우터 | 유지 |
| `project/` | 6(본 문서 작성 전) | 105,976 B | 유지 |
| `data/` | 124 | 1,053,291 B | 유지 |
| `backtest/` | 14 | 143,126 B | 유지 |
| `gui/` | 11 | 165,433 B | 웹 전환 후 퇴역 여부를 파일별 판단 |
| `archive/` | **230** | **5,277,398 B (5.28 MB)** | 유지(비기본 역사 증거) |

### 5.2 Router 직접 참조 재검토 대상

다음 19개는 초기 조사에서 지정된 7개 router/status 파일(`docs/README.md`,
Project/Data/Backtest/GUI Status, 루트 README, AGENTS)에 직접 이름이 없던 문서다.
이 조건은 “미사용”과 같지 않다. 3단계에서 요구된 3개는 Data Status에 연결했고,
나머지는 현재 inbound 참조와 역할을 재확인했다.

| 경로 | 크기 | 추적 / 마지막 변경일 | 다른 현재 문서 참조 | 분류 |
|---|---:|---|---|---|
| `docs/backtest/PORTFOLIO_RISK_VALIDATION_EVIDENCE_CONTRACT.md` | 5,831 B | 예 / 2026-08-31 | GUI 동명 contract | 유지 |
| `docs/data/config/LS_T8462_ANALYSIS_FEATURES.md` | 4,533 B | 예 / 2026-08-27 | Dataset Index, LS operation/source/research | 유지 |
| `docs/data/operations/GLOBAL_NEW_SYMBOLS_20260902.md` | 3,713 B | 예 / 2026-09-03 | Data Status | 유지(라우팅 완료) |
| `docs/data/queues/KRX_EQUITY_FUNDAMENTAL_RAW_DAILY_REVIEW_REQUIRED.md` | 3,411 B | 예 / 2026-08-27 | Dataset Index, Source Registry, queue README | 유지 |
| `docs/data/queues/KRX_ETF_RAW_DAILY_INCREMENTAL_REVIEW_REQUIRED.md` | 6,299 B | 예 / 2026-08-27 | queue README | 유지 |
| `docs/data/queues/KRX_FOREIGN_OWNERSHIP_RAW_DAILY_REVIEW_REQUIRED.md` | 2,931 B | 예 / 2026-08-27 | Dataset Index, Source Registry, queue README | 유지 |
| `docs/data/queues/LOCAL_DATA_BACKUP_RESTORE.md` | 4,637 B | 예 / 2026-08-27 | queue README | 유지 |
| `docs/data/sources/AGENT_RUNBOOK.md` | 3,474 B | 예 / 2026-08-27 | source README | 유지 |
| `docs/data/sources/bok_ecos/817Y002_PUBLICATION_FINALITY.md` | 4,350 B | 예 / 2026-08-31 | BOK source README | 유지 |
| `docs/data/sources/DASHBOARD_SOURCE_MAP.md` | 5,513 B | 예 / 2026-08-27 | source README/agent runbook | 유지 |
| `docs/data/sources/ENDPOINT_CATALOG.md` | 5,572 B | 예 / 2026-08-27 | source README/agent runbook | 유지 |
| `docs/data/sources/forward_valuation/KOSPI_FORWARD_PER_PBR_SOURCE_DECISION.md` | 9,764 B | 예 / 2026-08-27 | active PIT research contract | 유지 |
| `docs/data/sources/krx/MDCSTAT03501_EQUITY_FUNDAMENTAL_FINALITY.md` | 5,004 B | 예 / 2026-08-27 | Dataset Index, Source Registry, queue 문서 | 유지 |
| `docs/data/sources/krx/MDCSTAT03701_FOREIGN_OWNERSHIP_FINALITY.md` | 3,329 B | 예 / 2026-08-27 | Dataset Index, Source Registry, queue 문서 | 유지 |
| `docs/data/sources/MARKET_SESSION_RULES.md` | 8,328 B | 예 / 2026-08-27 | Data Status | 유지(라우팅 완료) |
| `docs/data/sources/SOURCE_TEMPLATE.md` | 1,427 B | 예 / 2026-08-27 | source README | 유지 |
| `docs/data/sources/us_option_pcr/US_OPTION_PCR_SOURCE_DECISION.md` | 14,144 B | 예 / 2026-08-27 | Data Status | 유지(라우팅 완료) |
| `docs/gui/DESIGN.md` | 8,351 B | 예 / 2026-09-02 | GUI Status를 역할 권위로 명시 | 유지; PySide6 퇴역 때 재검토 |
| `docs/gui/PORTFOLIO_RISK_VALIDATION_EVIDENCE_CONTRACT.md` | 4,142 B | 예 / 2026-08-31 | Backtest 동명 contract | 유지 |

중복 재검토 결과 삭제 대상은 없다. 의도적으로 겹치는 역할은 Backtest risk
evidence 의미 대 GUI 표시 투영, KRX source/finality policy 대 비실행 Queue 후보,
source 조사 runbook 대 template/endpoint 색인, full-market ETF Raw 후보 대 현재
watchlist ETF operation이다. 각 관련 문서 상단에 한 줄 역할과 권위 문서를 명시했다.

## 6. `scripts/manual/` 대 `scripts/maintenance/`

생성된 `__pycache__`를 빼면 `manual/`은 149개, `maintenance/`는 21개 소스
스크립트다. 아래는 Scheduler Status, `register_data_operations_tasks.ps1`, 테스트,
현재 docs 어디에도 정확 경로·파일명·module import가 없는 목록이다. “참조 없음”은
동적 호출 가능성을 완전히 부정하지 않으므로 추적 코드는 삭제 대신 백업 브랜치가 기본이다.

| 경로(모두 참조 없음) | 크기 | 추적 / 마지막 변경일 | 분류 |
|---|---:|---|---|
| `scripts/manual/audit/audit_a007_short_selling.py` | 3,326 B | 예 / 2026-08-27 | 보관 브랜치로 이동 |
| `scripts/manual/backfill/backfill_fsc_stock_lending.py` | 1,736 B | 예 / 2026-08-27 | 보관 브랜치로 이동 |
| `scripts/manual/backfill/backfill_krx_mdc_vkospi.py` | 15,879 B | 예 / 2026-08-27 | 보관 브랜치로 이동 |
| `scripts/manual/build/build_kospi200_derivatives_bridge.py` | 1,801 B | 예 / 2026-08-27 | 보관 브랜치로 이동 |
| `scripts/manual/build/build_kospi200_futures_basis.py` | 1,560 B | 예 / 2026-08-27 | 보관 브랜치로 이동 |
| `scripts/manual/build/build_legacy_kospi200_option_pcr.py` | 1,172 B | 예 / 2026-08-27 | 보관 브랜치로 이동 |
| `scripts/manual/build/promote_kr_index_daily_landing.py` | 1,465 B | 아니오(개별 ignore) / 기록 없음 | 삭제(명백한 잔해) |
| `scripts/manual/collect/capture_kr_index_daily_live.py` | 1,211 B | 예 / 2026-08-27 | 보관 브랜치로 이동 |
| `scripts/manual/collect/collect_cboe_vix_delayed_quote_ur187.py` | 2,446 B | 예 / 2026-08-27 | 보관 브랜치로 이동 |
| `scripts/manual/collect/collect_dividend_snapshot.py` | 1,309 B | 예 / 2026-08-27 | 보관 브랜치로 이동 |
| `scripts/manual/collect/collect_nasdaq_soxx_ur193_windows.py` | 1,308 B | 예 / 2026-08-27 | 보관 브랜치로 이동 |
| `scripts/manual/collect/collect_naver_mobile_home_ur167_windows.py` | 1,519 B | 예 / 2026-08-27 | 보관 브랜치로 이동 |
| `scripts/manual/collect/collect_naver_mobile_home_ur176_post_close.py` | 1,275 B | 예 / 2026-08-27 | 보관 브랜치로 이동 |
| `scripts/manual/collect/fdr_future_display_collector.py` | 192 B | 예 / 2026-08-27 | 보관 브랜치로 이동 |
| `scripts/manual/collect/refresh_toss_short_watchlist_daily.py` | 1,890 B | 예 / 2026-08-27 | 보관 브랜치로 이동 |
| `scripts/manual/collect/run_toss_domestic_ur246_task.ps1` | 356 B | 예 / 2026-08-27 | 보관 브랜치로 이동 |
| `scripts/manual/derived/rebuild_dashboard_derivatives.py` | 5,577 B | 예 / 2026-08-27 | 보관 브랜치로 이동 |
| `scripts/manual/diagnostic/a007_investor_access_recovery_support.py` | 4,959 B | 예 / 2026-08-27 | 보관 브랜치로 이동 |
| `scripts/manual/diagnostic/a007_investor_h1_diagnostic_support.py` | 8,303 B | 예 / 2026-08-27 | 보관 브랜치로 이동 |
| `scripts/manual/diagnostic/a007_investor_h2_diagnostic_support.py` | 4,854 B | 예 / 2026-08-27 | 보관 브랜치로 이동 |
| `scripts/manual/diagnostic/a007_investor_h3_diagnostic_support.py` | 4,845 B | 예 / 2026-08-27 | 보관 브랜치로 이동 |
| `scripts/manual/diagnostic/a007_investor_h4_boundary_diagnostic_support.py` | 6,024 B | 예 / 2026-08-27 | 보관 브랜치로 이동 |
| `scripts/manual/diagnostic/a007_investor_h4_boundary_parity_support.py` | 3,757 B | 예 / 2026-08-27 | 보관 브랜치로 이동 |
| `scripts/manual/diagnostic/a007_investor_h4_diagnostic_support.py` | 4,845 B | 예 / 2026-08-27 | 보관 브랜치로 이동 |
| `scripts/manual/diagnostic/a007_investor_range_diagnostic_support.py` | 6,763 B | 예 / 2026-08-27 | 보관 브랜치로 이동 |
| `scripts/manual/diagnostic/a007_investor_s1_diagnostic_support.py` | 5,659 B | 예 / 2026-08-27 | 보관 브랜치로 이동 |
| `scripts/manual/diagnostic/diagnose_a007_investor_access_recovery.py` | 1,802 B | 예 / 2026-08-27 | 보관 브랜치로 이동 |
| `scripts/manual/diagnostic/diagnose_a007_investor_h4_boundary_parity.py` | 3,790 B | 예 / 2026-08-27 | 보관 브랜치로 이동 |
| `scripts/manual/diagnostic/diagnose_pykrx_login.py` | 10,004 B | 예 / 2026-08-27 | 보관 브랜치로 이동 |
| `scripts/manual/pilot/bok_ecos_treasury_pilot_support.py` | 21,292 B | 예 / 2026-08-27 | 보관 브랜치로 이동 |
| `scripts/manual/pilot/capture_nasdaq_index_chart_ur215.py` | 1,012 B | 예 / 2026-08-27 | 보관 브랜치로 이동 |
| `scripts/manual/pilot/capture_nasdaq_tnx_info_ur219.py` | 1,035 B | 예 / 2026-08-27 | 보관 브랜치로 이동 |
| `scripts/manual/pilot/capture_nasdaq_tnx_ur201_discovery.py` | 956 B | 예 / 2026-08-27 | 보관 브랜치로 이동 |
| `scripts/manual/pilot/capture_nasdaq_vix_ur200_discovery.py` | 991 B | 예 / 2026-08-27 | 보관 브랜치로 이동 |
| `scripts/manual/pilot/ls_t8412_current_15m_pilot.py` | 4,759 B | 예 / 2026-08-27 | 보관 브랜치로 이동 |
| `scripts/manual/pilot/pilot_bok_ecos_treasury_page_semantics.py` | 8,000 B | 예 / 2026-08-27 | 보관 브랜치로 이동 |
| `scripts/manual/pilot/pilot_krx_mdc_vkospi.py` | 3,571 B | 예 / 2026-08-27 | 보관 브랜치로 이동 |
| `scripts/manual/pilot/pykrx_etf_pilot_support.py` | 11,387 B | 예 / 2026-08-27 | 보관 브랜치로 이동 |
| `scripts/manual/pilot/pykrx_foreign_ownership_pilot_support.py` | 14,420 B | 예 / 2026-08-27 | 보관 브랜치로 이동 |
| `scripts/manual/pilot/pykrx_fundamentals_pilot_support.py` | 18,264 B | 예 / 2026-08-27 | 보관 브랜치로 이동 |
| `scripts/manual/pilot/pykrx_short_investor_range_recheck_support.py` | 7,905 B | 예 / 2026-08-27 | 보관 브랜치로 이동 |
| `scripts/maintenance/inventory_storage.py` | 3,175 B | 예 / 2026-08-27 | 보관 브랜치로 이동 |
| `scripts/maintenance/plan_repository_restructure.py` | 4,868 B | 예 / 2026-08-27 | 보관 브랜치로 이동 |
| `scripts/maintenance/repair_denied_acls.ps1` | 5,305 B | 예 / 2026-09-02 | 유지(Project Status의 승인된 정확 ACL 복구 경로) |
| `scripts/maintenance/run_global_market_60m.py` | 1,380 B | 예 / 2026-08-27 | 보관 브랜치로 이동 |

## 7. `src/` 최대 파일 10개

Git 추적 소스만 대상으로 했고 `__pycache__/*.pyc`는 제외했다. 아래 표는 조사
당시 순위를 보존하되, 2단계에서 제거된 PM 전용 상위 4개 행은 현재 경로로
오해하지 않도록 생략했다. 그 행의 정확한 값은
`backup/repo-cleanup-phase2-20260903`에 보존한다.

| 순위 | 경로 | 바이트 | 줄 수 | 분류 |
|---:|---|---:|---:|---|
| 1 | `src/stock_data/gui/main_window.py` | 766,198 | 16,004 | 웹 전환 후 퇴역 |
| 2 | `src/stock_data/gui/services.py` | 353,211 | 7,198 | 유지(웹 import 분리 후 경로 이전) |
| 5 | `src/stock_data/orchestration/release_readiness.py` | 104,795 | 2,402 | 유지 |
| 6 | `src/stock_data/orchestration/daily_operations.py` | 98,292 | 2,093 | 유지 |
| 7 | `src/stock_data/gui/backtest_service.py` | 81,186 | 1,988 | 유지(웹 공유 서비스 후보) |
| 10 | `src/stock_data/gui/account_snapshot_service.py` | 64,376 | 1,504 | 유지(웹이 직접 import) |

`main_window.py` 16,004줄과 `services.py` 7,198줄은 퇴역 작업 전에 각각 UI와
재사용 서비스 경계를 쪼개야 할 가장 큰 결합 지점이다.

## 8. `.gitignore` 공백과 최근 생성물 churn

최근 7일에 수정된 현재 추적 JSON/CSV 176개를 상위 폴더별로 묶었다. 이미 추적된
파일은 `.gitignore` 행을 추가하는 것만으로 추적이 멈추지 않으므로, 향후 정책 변경은
백업 branch 확보 후 `git rm --cached`와 producer/test 수정이 함께 필요하다.

| 경로군 | 추적 생성형 JSON/CSV 수 | 대표 예 | 판단 |
|---|---:|---|---|
| `artifacts/request_queue/` | 91 | `COMPLETED_INDEX.json`, 각 `META.json` | 유지. 현재 file-backed Queue 계약이므로 단순 ignore 금지 |
| `artifacts/release_readiness/` | 11 | 날짜별 JSON, `release_readiness_latest.json` | latest/receipt 보존 단위를 계약으로 정하고 날짜별 churn 분리 |
| `artifacts/backtest/` | 8 | bundle/result/ledger JSON, signals CSV | 재현 manifest와 대용량 결과를 분리; 현재는 유지 |
| `artifacts/daily_health/` | 3 | health JSON/CSV | 현재 consumer가 읽으므로 유지 |
| `artifacts/gui_validation/` | 3 | retention manifest/receipt, stall JSON | PySide6 퇴역 전 유지 |
| `artifacts/data_inventory/` | 2 | full-universe CSV | 현재 navigation 증거로 유지 |
| `artifacts/issue_state/` | 2 | `issues.json`, policy JSON | 현재 상태 계약. Git churn 허용 여부 별도 결정 필요 |
| `artifacts/analysis/` | 1 | option-wall CSV | GUI 서비스가 직접 읽으므로 유지 |
| `artifacts/recovery/` | 1 | ACL repair report JSON | 백업 branch 이동 |
| **합계** | **176 (JSON 162, CSV 14)** |  |  |

이 표의 나머지 54개 추적 JSON/CSV를 구성하던 정적 evidence 여섯 경로군은
2단계에서 제거했고 `backup/repo-cleanup-phase2-20260903`에 보존했다.

## 9. 제안하는 4단계 정리 계획

각 단계는 별도 diff/검증으로 실행한다. 아래 Git 명령은 초기 제안과 완료 기록이며,
이 3단계 문서 정리에서는 branch 생성·commit을 실행하지 않는다. 추적 파일을 지우기
전에 현재 commit을 가리키는 백업 branch를 만드는 공통 형태는 다음과 같다.

```powershell
git branch backup/repo-cleanup-phase<N>-20260903 HEAD
```

### 1단계 — 루트 잔해

정확한 대상:

```text
c/
debug.log
uv.lock
.tmp/                         # 실행 중 process/agent 소유 경로 제외 후
.worktrees/                   # 빈 디렉터리인 경우만
__pycache__/
artifacts/recovery/acl_denied_dirs.txt
scripts/manual/build/promote_kr_index_daily_landing.py
```

`GATES.md`와 `.unlazy/`는 2026-09-03 작업 흔적이므로 이 단계에서 건드리지 않는다.
무추적/ignored 파일도 백업해야 한다면 branch는 담을 수 없으므로 먼저 별도 안전 경로에
복사하거나 임시 backup branch에서 명시적으로 `git add -f` 후 검토해야 한다.

```powershell
git switch -c backup/repo-cleanup-phase1-20260903
git add -f -- uv.lock scripts/manual/build/promote_kr_index_daily_landing.py
git commit -m "backup: preserve untracked cleanup candidates"
git switch master
```

### 2단계 — 정적 artifacts와 비활성 저장소 로컬 PM

> **실행됨 2026-09-03 (Claude):** 런타임 비의존 정적 evidence 여섯 경로군,
> 비활성 PM 전용 소스·entry/display·테스트 세트를 제거했다. 정확한 삭제 목록과
> 파일 내용은 `backup/repo-cleanup-phase2-20260903`에 보존한다.

`scripts/request_queue.py`, `.agents/`, `artifacts/request_queue/`, Queue/Issue
State 테스트, `artifacts/recovery/`는 명시적으로 제외되어 현재 경로에 남는다.

```powershell
git branch backup/repo-cleanup-phase2-20260903 HEAD
```

### 3단계 — 문서

§5.2의 19개 문서와 다음 7개 router만 범위로 삼아, 링크가 없는 3개
(`GLOBAL_NEW_SYMBOLS_20260902.md`, `MARKET_SESSION_RULES.md`,
`US_OPTION_PCR_SOURCE_DECISION.md`)를 현재 authority에서 연결하고 중복/역할을 재확인한다.
이 단계는 삭제 단계가 아니다.

> **문서 갱신 2026-09-03:** 위 3개와 이번 주 생성 문서는 Project/Data/GUI
> Status의 해당 route에 연결했다. 19개 문서는 삭제하지 않았고, 겹치는 문서에는
> 의미·표시·source evidence·비실행 후보 중 어느 권위가 우선인지 역할 줄을 추가했다.

```text
docs/README.md
docs/project/PROJECT_STATUS.md
docs/data/DATA_STATUS.md
docs/backtest/BACKTEST_STATUS.md
docs/gui/GUI_STATUS.md
README.md
AGENTS.md
docs/backtest/PORTFOLIO_RISK_VALIDATION_EVIDENCE_CONTRACT.md
docs/data/config/LS_T8462_ANALYSIS_FEATURES.md
docs/data/operations/GLOBAL_NEW_SYMBOLS_20260902.md
docs/data/queues/KRX_EQUITY_FUNDAMENTAL_RAW_DAILY_REVIEW_REQUIRED.md
docs/data/queues/KRX_ETF_RAW_DAILY_INCREMENTAL_REVIEW_REQUIRED.md
docs/data/queues/KRX_FOREIGN_OWNERSHIP_RAW_DAILY_REVIEW_REQUIRED.md
docs/data/queues/LOCAL_DATA_BACKUP_RESTORE.md
docs/data/sources/AGENT_RUNBOOK.md
docs/data/sources/bok_ecos/817Y002_PUBLICATION_FINALITY.md
docs/data/sources/DASHBOARD_SOURCE_MAP.md
docs/data/sources/ENDPOINT_CATALOG.md
docs/data/sources/forward_valuation/KOSPI_FORWARD_PER_PBR_SOURCE_DECISION.md
docs/data/sources/krx/MDCSTAT03501_EQUITY_FUNDAMENTAL_FINALITY.md
docs/data/sources/krx/MDCSTAT03701_FOREIGN_OWNERSHIP_FINALITY.md
docs/data/sources/MARKET_SESSION_RULES.md
docs/data/sources/SOURCE_TEMPLATE.md
docs/data/sources/us_option_pcr/US_OPTION_PCR_SOURCE_DECISION.md
docs/gui/DESIGN.md
docs/gui/PORTFOLIO_RISK_VALIDATION_EVIDENCE_CONTRACT.md
```

```powershell
git branch backup/repo-cleanup-phase3-20260903 HEAD
```

### 4단계 — PySide6 퇴역(웹 parity 이후만)

> 2026-09-05 update: the Qt runtime boundary and dependencies were retired; web-imported read-only services remain under `src/stock_data/gui/` pending any later neutral-package rename.

먼저 웹이 직접 import하는 공유 서비스를 중립 패키지로 이동하고 import/test를 바꾼다.
그 다음 아래 Qt 경계를 퇴역한다. `src/stock_data/gui/` 전체 삭제는 서비스 분리 전 금지다.

```text
app.py
src/stock_data/gui/main_window.py
src/stock_data/gui/font_policy.py
src/stock_data/gui/__init__.py              # 공유 서비스 이동 완료 후
tests/integration/gui/test_release_readiness.py
tests/unit/gui/test_dashboard_preferences.py
tests/unit/gui/test_font_policy.py
tests/unit/gui/test_gui_backtest.py
tests/unit/gui/test_gui_health.py
tests/unit/gui/test_net_worth_page.py
tests/unit/gui/test_stock_candidate_discovery_gui.py
docs/gui/DESIGN.md
artifacts/gui_validation/
```

공유 서비스 이동 대상으로 재분류할 현재 경로는
`src/stock_data/gui/{account_snapshot_service,account_value_history,backtest_scenario_service,backtest_service,current_display,dashboard_preferences,google_sheet_account_import,health_service,korean_equity_nxt_session,manual_account_market_values,manual_account_snapshot,manual_account_store,net_worth_service,query,refresh_status,research_workspace_preferences,services,us_option_pcr_adapter,vix_futures_adapter,watchlist_service}.py`다.

```powershell
git branch backup/repo-cleanup-phase4-pyside6-20260903 HEAD
```

## 10. 분류 집계

아래는 초기 조사 시점 집계다. 중복 세부 module 행을 다시 세지 않고, 루트 14 +
artifacts 19 + control-plane 의사결정 9 + router 미직접참조 docs 19 + 미참조
scripts 45 = **106개 정리 의사결정 단위**를 기준으로 한다. 2단계 제거 후 현재
경로별 재집계는 이번 문서-only 단계의 범위가 아니며, 삭제 전 상세는
`backup/repo-cleanup-phase2-20260903`에 보존한다.

| 분류 | 개수 |
|---|---:|
| 삭제(명백한 잔해) | 9 |
| 보관 브랜치로 이동(코드/문서이지만 현재 미사용) | 55 |
| 유지 | 36 |
| 웹 전환 후 퇴역 | 6 |
| **합계** | **106** |
