# Datasource agent runbook

역할: 이 문서는 source 조사·pilot·promotion의 작업 순서를 소유하며, 상태 권위는 [Data Status](../DATA_STATUS.md), schema 권위는 Dataset Contract다.

이 문서는 동일한 API 문서를 반복해서 브라우저로 찾거나, 이미 있는 client를
우회하는 일을 줄이기 위한 순서다.

## Before any browser or network call

1. [Source index](README.md)에서 공급자 상태를 확인한다.
2. [Endpoint catalog](ENDPOINT_CATALOG.md)에서 endpoint와 기존 구현을 찾는다.
3. 해당 `<provider>/README.md`를 끝까지 읽는다.
4. [Dataset Index](../DATASET_INDEX.md)에서 dataset contract와 현재 gate를 확인한다.
5. operation 문서와 기존 collector의 `--help`를 확인한다.
6. 같은 목적의 retained Landing/checkpoint가 있으면 API-0 replay가 가능한지 본다.

위 자료에 endpoint, field, unit, date, success code가 없을 때만 공식 vendor
문서를 연다. 블로그, 검색 결과 요약, 브라우저 화면 예제는 계약 근거가 아니다.

## Discovery-only source

아직 사용하지 않는 source를 조사할 때는 다음까지만 허용한다.

- `<provider>/README.md` 생성
- 공식 portal/product/endpoint 링크
- 인증 방식과 환경변수 **이름**
- license, display, retention, rate-limit 확인 항목
- 작은 read-only request shape 또는 호출을 막는 이유
- `CANDIDATE`나 `BLOCKED` 상태와 진입 조건

provider code, scheduler, production path, secret 설정은 discovery 문서만으로
추가하지 않는다.

## Bounded pilot

1. exact endpoint와 공식 request/response schema를 기록한다.
2. credential은 환경변수에서만 읽고 메모리에 둔다.
3. 날짜·symbol·page·year를 allowlist하고 최대 호출 수를 정한다.
4. connect/read timeout을 사용하고 uncontrolled retry를 금지한다.
5. auth request/response, header, full URL, account payload를 log하지 않는다.
6. HTTP와 provider result code를 모두 검사한다.
7. empty, malformed, wrong-date, duplicate, NaN/inf, range 오류를 분리한다.
8. 성공 응답은 immutable Landing에 atomic write한다.
9. pilot artifact에 call count, scope, hashes, validation, blocker를 남긴다.

## Promotion gate

다음 질문에 하나라도 답할 수 없으면 Landing/Pilot에서 멈춘다.

- source date, publication date, capture time은 각각 무엇인가?
- revision/vintage가 있는가? PIT 용도는 가능한가?
- unit, multiplier, timezone, market session, contract maturity가 정의됐는가?
- valid-empty와 provider error를 구분하는가?
- date + symbol 또는 contract key uniqueness가 정의됐는가?
- 기존 provider와 join/substitution policy가 있는가?
- license가 저장, 표시, 재배포를 허용하는가?
- rollback과 completed-date API-0 replay가 가능한가?

## Dashboard gate

[Dashboard Source Map](DASHBOARD_SOURCE_MAP.md)을 읽고, metric view에 최소한
value, source date, expected date, provider, freshness, PIT status, display
state, blocked reason을 전달한다. 실패한 metric만 즉시 비우고 이전 값이나
다른 provider 값으로 대체하지 않는다.

## Documentation after a verified change

- 공급자 README: 새로 검증한 endpoint/auth/schema/limit만 반영
- Endpoint catalog: 실제 checked-in route가 바뀐 경우만 반영
- Dataset Index/Data Status: coverage, date, automation, gate 변경
- Operation: call budget, checkpoint, replay, rollback 변경
- GUI Status: display/readiness 변경

긴 응답 예제, 전체 vendor archive, token, 실제 계좌 값은 문서에 넣지 않는다.
