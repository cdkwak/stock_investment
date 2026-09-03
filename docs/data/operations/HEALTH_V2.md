# Health V2 운영 기준

Health V2는 보존 데이터의 존재와 자동화 장애를 분리해 표시하는 로컬 읽기 전용
투영이다. 원본의 `freshness`·최종성·PIT 필드는 유지하며, 데이터 페이지는 별도
`display_status`를 사용한다.

## 화면 상태

| 상태 | 기준 |
|---|---|
| `CURRENT` / 정상 | `latest >= expected`. 원본이 `EXPECTED_LAG`여도 두 날짜가 같으면 정상이다. |
| `LATE` / 지연 | 스케줄러 lane과 자동화가 활성화되어 있고 `latest < expected`이다. |
| `FAILED` / 실패 | 마지막 실행이 실패했다. |
| `PRESERVED` / 수동·보존 | lane이 없거나 자동화 대상이 아니며, 최신 보존일은 정보로만 표시한다. |
| `REFERENCE` / 참고 | 이벤트·분기 자료와 법인코드 맵의 최신 이벤트일/기간말/원천일이다. |

수동, 중단, 대체, 레거시 자료는 오래되었다는 이유만으로 지연이나 실패가 되지
않는다. 각 데이터셋의 짧은 보존 사유는 typed universe가 소유한다. 다른 웹 화면이
실제로 읽는 `research_target_price_consensus`는 데이터 페이지에 화면 사용 보존
자료로 별도 고지한다.

FRED `VIXCLS`는 다음 미국 영업일 약 08:40 CT에 공개된다. 06:00 KST FRED lane보다
뒤에 공개된 값은 같은 날 늦은 것이 아니며 다음 06:00 KST 실행부터 기대값이 된다.
`kr_etf_master`는 가격이 이미 최신인 실행에서도 원천 ticker list를 한 번만 Landing에
보존한 뒤 `source_date`를 갱신한다. `kr_corp_code_map`은 `modify_date` 최대값,
`kr_fundamentals_quarterly`는 `period_end` 최대값을 참고 기준일로 사용한다.

## 호환성과 경고

`artifacts/daily_health/universe_data_v2_20260819.json` 파일명 날짜는 호환 경로일 뿐
신선도 기준이 아니다. 코드가 파일명으로 최신본을 고르지 않으며, 파일 내부의
timezone-aware `as_of`가 생성 기준시각이다.

실행 중인 코드보다 새 artifact에 등록 ID가 먼저 생겨도 알려진 행은 계속 표시한다.
모르는 ID는 정렬된 경고 목록과 데이터 페이지의 `미등록 N`으로 노출하며 전체 화면을
비우지 않는다. 중복 ID, 잘못된 날짜, datasets 배열 손상은 계속 fail-closed다.

## 사람이 실행할 로컬 재생성

아래 명령은 네트워크를 사용하지 않고 보존된 core artifact와 로컬 자료만 읽는다.
자동 실행 에이전트는 이 문서 작성 작업에서 실행하지 않는다.

```powershell
$env:PYTHONIOENCODING = "utf-8"
.venv\Scripts\python.exe scripts\maintenance\reconcile_daily_health_artifact.py --artifact artifacts\daily_health\core_data_20260818.json --universe-output artifacts\daily_health\universe_data_v2_20260819.json --execution-log artifacts\scheduler_logs\STOCK_DATA_DAILY_HEALTH_last.json --universe-only
```

재생성 뒤에는 JSON의 `as_of`, `dataset_count`, `dimension_summary.display_status`,
`runtime_coverage_failures`와 데이터 페이지의 다섯 타일 및 `미등록 N`을 확인한다.
