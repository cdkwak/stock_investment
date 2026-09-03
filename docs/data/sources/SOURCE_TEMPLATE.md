# Data source README template

역할: 이 문서는 provider README의 작성 형식만 소유하며, 실행 순서는 [Datasource agent runbook](AGENT_RUNBOOK.md), 상태 권위는 [Data Status](../DATA_STATUS.md)다.

새 공급자를 추가할 때 `docs/data/sources/<source_id>/README.md`에 아래 항목을
짧게 기록한다. 이 문서는 상태 승인이나 데이터 계약을 대체하지 않는다.

## Status

- Project status: `ACTIVE`, `RETAINED`, `PILOT`, `CANDIDATE`, `BLOCKED` 중 하나
- Data class: official API, official file, broker API, library adapter, or empirical endpoint
- Allowed use: 실제 허용 범위

## Official reference

- 공식 포털과 사용한 endpoint 문서만 링크한다.
- 웹 예제를 그대로 복사하지 말고 확인한 요청/응답 의미만 적는다.

## Authentication

- 환경변수 **이름만** 기록한다.
- `.env`, 키, 토큰, 인증 헤더, 계좌번호는 읽거나 출력하지 않는다.

## Safe read example

- 조회 전용, 작은 범위, 명시적 timeout, 무제어 retry 없음.
- 가능하면 raw HTTP 대신 기존 프로젝트 client/collector를 사용한다.
- 응답 저장 전 HTTP, JSON, 필수 필드, 날짜, 중복, 빈 결과를 검증한다.

## Project route

- provider, contract, operation, focused test를 각각 정확히 링크한다.

## Boundaries

- 빈/오류 응답으로 기존 데이터를 덮어쓰지 않는다.
- capture time, market date, publication date, revision/vintage를 혼동하지 않는다.
- 라이선스, PIT, 단위, 세션, 만기가 불명확하면 Landing 또는 Pilot에서 멈춘다.
