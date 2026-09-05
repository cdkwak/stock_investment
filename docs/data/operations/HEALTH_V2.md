# Health V2 운영 기준

Health V2는 보존 데이터의 존재와 자동화 장애를 분리해 표시하는 로컬 읽기 전용
투영이다. 원본의 `freshness`·최종성·PIT 필드는 유지하며, 데이터 페이지는 별도
`display_status`를 사용한다.

`latest`와 `coverage_start`는 먼저 `data/normalized/<dataset>` 또는
`data/retained/<dataset>`의 Parquet에서 구한다. `date=`·`capture_date=` 같은
날짜 파티션은 디렉터리명만 읽고, 그 밖의 레이아웃은 날짜 열의 Parquet row-group
통계를 읽는다. 통계가 없는 파일만 해당 날짜 열 하나를 읽는다. 프로브 결과는
중첩 파티션 디렉터리 mtime을 키로 프로세스 안에서 캐시하며 네트워크나 Landing,
provider, core artifact의 오래된 `actual_latest`를 최신일 근거로 사용하지 않는다.

커버리지 결정 순서는 `probe -> _COVERAGE 정적 표 -> none`이다. 모든 행은
`coverage_source`를 `probe`, `static_table`, `none` 중 하나로 기록한다. 정적 표는
삭제 전 호환 fallback일 뿐이며 이 경로를 쓴 행의 분류 사유에는
`표는 손으로 적은 값`을 붙인다. 프로브가 정적 종료일과 다르면 artifact와 실행
로그의 `coverage_warnings`에 `WARN`, dataset, `static_end`, `probed_end`를 남긴다.
보존 Parquet도 정적 표도 없는 행은 계속 미수집으로 표시한다.

## 화면 상태

| 상태 | 기준 |
|---|---|
| `CURRENT` / 정시 | `latest >= expected`, 또는 표시 전용 수집 예정 시각 전이다. 후자는 원본 `freshness=STALE`을 유지하면서 `display_status=CURRENT`에 집계하되, 화면에는 실제 최신일의 연령과 다음 수집 시각을 함께 표시한다. `정시`는 데이터가 오늘 값이라는 뜻이 아니라 정책상 예정된 최신 날짜와 일치한다는 뜻이다. |
| `LATE` / 지연 | 스케줄러 lane과 자동화가 활성화되어 있고 `latest < expected`이다. |
| `FAILED` / 실패 | 마지막 실행이 실패했다. |
| `PRESERVED` / 수동·보존 | lane이 없거나 자동화 대상이 아니며, 최신 보존일은 정보로만 표시한다. |
| `REFERENCE` / 참고 | 이벤트·분기 자료와 법인코드 맵의 최신 이벤트일/기간말/원천일이다. |

수동, 중단, 대체, 레거시 자료는 오래되었다는 이유만으로 지연이나 실패가 되지
않는다. 각 데이터셋의 짧은 보존 사유는 typed universe가 소유한다. 다른 웹 화면이
실제로 읽는 `research_target_price_consensus`는 데이터 페이지에 화면 사용 보존
자료로 별도 고지한다.

FRED `VIXCLS`는 다음 미국 영업일 약 08:40 CT에 공개된다. 06:00 KST FRED lane보다
뒤에 공개된 값은 같은 날 스케줄 기대값을 앞당기지 않으며 다음 06:00 KST 실행부터
기대값이 된다.
`kr_etf_master`는 가격이 이미 최신인 실행에서도 원천 ticker list를 한 번만 Landing에
보존한 뒤 `source_date`를 갱신한다. `kr_corp_code_map`은 `modify_date` 최대값,
`kr_fundamentals_quarterly`는 `period_end` 최대값을 참고 기준일로 사용한다.

일별 자동화 행은 timezone-aware `due_at`을 함께 기록한다. KRX 20:30 묶음은
20:45 KST, 다음 거래일 09:10/14:10 묶음은 각각 09:25/14:25 KST, 글로벌
06:10/06:20 및 FRED 일별 묶음은 06:35 KST를 기준으로 한다. BOK·Toss와
그 밖의 고정 lane은 실행 시각에 15분을 더한다. 최신값이 기대값보다 하루 이상
밀렸다면 새 관측값의 예정 시각 전이라도 이미 기한이 지난 이전 누락을 숨기지 않는다.
수동 자료는 `due_at`과 `pending_until`을 기록하지 않으며 기존 분류를 유지한다.
`due_at`·`pending_until`·`display_status`는 Health/데이터 페이지 표시 전용이다.
`freshness`·`expected_market_date`·`expected_available_observation`·
`collection_required`와 스케줄러 phase target을 변경하지 않는다. `latest < expected`
이고 `as_of < due_at`인 한 대상 차이만 `display_status=CURRENT`로 유지하며 수집 예정
힌트를 강조한다. 그 밖의 미수집은 `지연`이다.

데이터 페이지의 연령 배지는 `latest` 다음 날부터 조회 당일까지 해당 행의 XKRX/XNYS
거래 세션 수를 센다. 거래 캘린더가 계약되지 않은 수동·스냅샷·이벤트·주별 행도
연령을 숨기지 않고 달력 날짜 경과로 표시한다. `0~1`세션은 중립, `2~3`세션은
amber `#a8621a`, `4`세션 이상은 red `#c0392b`다. 요약의
`오늘 · 어제 · 그 이전`은 자동화 활성 행 중 날짜가 있는 행만 센다.

다음 수집 힌트는 artifact의 `scheduler_lane`, `provider_availability_policy`,
`due_at`을 사용한다. KRX 묶음은 09:10·14:10·20:30, 글로벌 ETF/지수는
06:10·06:20, FRED는 06:00으로 표시한다. FRED H.10 주별 정책은
`매주 월 06:00`, 수동 정책은 `수동`으로 표시한다. `latest`가 오늘 거래 세션보다
뒤처지고 현재 시각이 `due_at` 전이면 상태 칸에서 해당 수집 힌트를 강조한다.
화면 그룹 안에서는 연령이 큰 행부터 정렬하며 `오늘 데이터만` 필터를 제공한다.

## 호환성과 경고

`artifacts/daily_health/universe_data_v2_20260819.json` 파일명 날짜는 기존 소비자를
위한 호환 경로일 뿐 신선도 기준이 아니다. 재생성기는 이 파일과 함께 안정 최신
포인터 `artifacts/daily_health/universe_data_v2_latest.json`도 원자적으로 쓴다.
데이터 페이지는 최신 포인터를 우선 읽고, 포인터가 없을 때만 호환 경로를 읽는다.
두 파일 모두 내부의 timezone-aware `as_of`가 생성 기준시각이다.

실행 중인 코드보다 새 artifact에 등록 ID가 먼저 생겨도 알려진 행은 계속 표시한다.
모르는 ID는 정렬된 경고 목록과 데이터 페이지의 `미등록 N`으로 노출하며 전체 화면을
비우지 않는다. 중복 ID, 잘못된 날짜, datasets 배열 손상은 계속 fail-closed다.

## 사람이 실행할 로컬 재생성

아래 명령은 네트워크를 사용하지 않고 보존된 core artifact와 로컬 자료만 읽는다.
core artifact의 시각은 `core_reference_time` 계보로만 남고 최신일을 고정하지 않는다.

```powershell
$env:PYTHONIOENCODING = "utf-8"
.venv\Scripts\python.exe scripts\maintenance\reconcile_daily_health_artifact.py --artifact artifacts\daily_health\core_data_20260818.json --universe-output artifacts\daily_health\universe_data_v2_20260819.json --execution-log artifacts\scheduler_logs\STOCK_DATA_DAILY_HEALTH_last.json --universe-only
```

위 호환 경로를 `--universe-output`으로 유지하면 같은 내용의
`universe_data_v2_latest.json`이 자동으로 함께 갱신된다. Windows 예약 작업 정의는
변경할 필요가 없다.

재생성 뒤에는 JSON의 `as_of`, `dataset_count`, `dimension_summary.display_status`,
`coverage_source_summary`, `coverage_warnings`, `runtime_coverage_failures`와 데이터
페이지의 다섯 타일 및 `미등록 N`을 확인한다.
