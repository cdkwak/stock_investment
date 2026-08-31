# 1인 퀀트 투자자 GUI 감사 — 독립 재판정 보고서

## 결론

`FAIL (operator usability) / 안전 경계 PASS`.

감사는 `3a993fbf37f805a91d5128a2e783a6b21690c0e2`에서 10/10 화면,
117/117 컨트롤, 활성 104/104 실제 실행, 비활성 전제조건 13건, Qt
메시지 0건 및 정상 종료를 확인했다. 실주문, 이체, 공급자 호출 및
스케줄러 실행은 없었다.

## 독립 Reviewer 판정

| ID | 판정 | 처리 |
|---|---|---|
| QI-01 | KEEP P1 | 읽기 전용 판단 Cockpit/출처 요약을 만든다. 추천·목표비중을 발명하지 않는다. |
| QI-02 | KEEP (re-scoped) P0 | 36/39 managed-health 실패를 진단하고 dependency를 전파한다. 기존 Dashboard의 `시장폭 미반영`/`기준 N/A` 억제를 보존한다. |
| QI-03 | KEEP P1 | 로컬 카탈로그 기반 예시와 보이는 복구 경로를 제공한다. |
| QI-04 | KEEP P1 | 내부 토큰을 사용자 언어와 실제 복구 제어로 바꾼다. |
| QI-05 | GENERIC P2 | 위험예산·VaR/ES·목표 sizing은 사용자 정책/검증 입력 없이 만들지 않는다. 읽기 전용 노출 근거 계약만 허용한다. |
| QI-06 | GENERIC P2 | sealed holdout과 development-only 경계를 지킨다. 검증 번들 한계 설명만 후속 후보이다. |
| QI-07 | KEEP P1 | Research/순자산/누락된 Dashboard 1280×720 clipping을 고친다. |
| QI-08 | GENERIC P3 | 전역 dark-orange 재테마는 증거 기반 결함이 아니다. 한국어 우선 문구는 QI-04와 함께 처리한다. |

## 데이터 진실성

회귀 `268 passed, 1 failed, 1 skipped`의 실패는 실제 managed-health
release gate다. `kr_index_constituent_daily`,
`kr_kospi200_breadth_daily`, `kr_kospi200_constituent_price_daily`가
expected `2026-08-28` 대신 `2026-08-27`에 머물렀다. 이 보고서는
공급자 호출이나 데이터 재작성을 지시하지 않으며, 영향 표면은 계속
숫자 없이 `판단 보류`로 남아야 한다.

## 승인된 work packages

1. Decision UX: 좁은 읽기 전용 Cockpit, 카탈로그 예시/복구, Research
   문구, 1280×720 responsive 및 Dashboard clipping.
2. Data Truth: stale 3건의 provider-free 진단, catalog/freshness
   dependency map, numeric-free/decision-hold regression.
3. Quant Validation: concentration/currency와 validated-bundle 한계를
   위한 읽기 전용 evidence contract. target sizing, VaR/ES, holdout
   unseal, provider/broker 동작은 금지한다.

원본 관찰과 전체 증거는 `REPORT_DRAFT.md`, `PERSONA.md`,
`INTERACTION_MANIFEST.md`, `ledger.json`, `inventory.json`, `stress.json`,
`evidence/`에 보존한다.
