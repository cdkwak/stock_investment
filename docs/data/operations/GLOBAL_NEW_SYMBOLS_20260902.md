# Yahoo 신규 종목 최초 bounded 수집

역할: 이 문서는 2026-09-02 bounded 수집·재개 절차 기록이며, 현재 선택과 실행 권위는 [Data Status](../DATA_STATUS.md)와 각 Dataset Contract에 있다.

상태: EWY, SOX, DOW_JONES, SP500_FUTURES, DOW_FUTURES는 2026-09-02에
`COLLECTED_AND_PROMOTED`되었다. 실패한 `DOLLAR_INDEX_FUTURES` / `DX=F` 등록은
제거되었고, 대체 index 종목 DOLLAR_INDEX / `DX-Y.NYB`만
`REGISTERED_NOT_YET_COLLECTED`이다. 아래 명령은 최초 수집 batch와 수정된 재개
job을 기록한다. Scheduler가 호출하는 동일 `global_current_refresh` prepare/promote
경로를 쓰며, 종목별 1회 호출·retry 0·Landing 우선·전체 데이터셋 CAS 교체를
유지한다. 완료 세션 범위는 `2025-09-01..2026-09-01`이다.

`DOLLAR_INDEX`는 ICE의 `DX-Y.NYB` 경로이므로 XNYS 현물지수와 같은 endpoint
label을 강제하지 않는다. 2026-09-02에 보존된 Landing 응답은 요청 범위
`2025-09-01..2026-09-01`에 대해 실제 `2025-09-02..2026-09-02`를 반환했다.
Registry의 `provider_native` 정책은 시작 label이 계획 시작의 5개 XNYS session
이내이고 마지막 label이 계획 종료 5개 XNYS session 전보다 늦은 경우만
후보를 허용한다. 후보/checkpoint의 symbol별 `coverage_first`, `coverage_last`,
`coverage_policy`를 promotion 전에 확인한다. 다른 index는 기본
`strict_exchange` 정책과 정확한 종료 endpoint 검사를 그대로 유지한다.

PowerShell에서 저장소 루트 기준으로 한 번 실행한다. 각 성공 후보의 JSON,
Landing hash, coverage, revision report를 확인한 뒤 Enter를 눌러 offline
promotion한다. 한 종목 실패는 `catch` 뒤 다음 종목으로 계속된다.

```powershell
$env:PYTHONIOENCODING = "utf-8"
$jobs = @(
    @{ phase = "yahoo_etf"; symbol = "EWY" },
    @{ phase = "yahoo"; symbol = "SOX" },
    @{ phase = "yahoo"; symbol = "DOW_JONES" },
    @{ phase = "yahoo_dashboard_futures"; symbol = "SP500_FUTURES" },
    @{ phase = "yahoo_dashboard_futures"; symbol = "DOW_FUTURES" },
    @{ phase = "yahoo"; symbol = "DOLLAR_INDEX" }
)
foreach ($job in $jobs) {
    try {
        $json = & .venv\Scripts\python.exe scripts\manual\collect\refresh_global_current.py `
            --project-root . --phase $job.phase --symbols $job.symbol `
            --start 2025-09-01 --end 2026-09-01 --confirm-live-landing-only | Out-String
        $candidate = $json | ConvertFrom-Json
        $json
        if ($candidate.status -ne "CANDIDATE_REVIEW_REQUIRED") { throw "candidate not review-ready" }
        Read-Host "Review $($job.symbol), then press Enter to promote"
        $checkpoint = "data/state/global_current_refresh/$($candidate.run_id)/checkpoint.json"
        & .venv\Scripts\python.exe scripts\manual\collect\refresh_global_current.py `
            --project-root . --promote-checkpoint $checkpoint `
            --confirm-offline-promotion --approval-digest $candidate.approval_digest
    } catch {
        Write-Error "$($job.symbol) preserved/unpromoted: $($_.Exception.GetType().Name)" -ErrorAction Continue
    }
}
```

예상 Landing은
`data/landing/global_current_refresh/<run_id>/yahoo/{etf_chart_daily|chart|commodity_chart_daily}/<capture_id>/{call.json,response.body}`이다.
승격 후 Normalized는 다음 symbol/year partition에 존재해야 한다.

- `data/normalized/global_etf_price_daily/symbol=EWY/year={2025,2026}/data.parquet`
- `data/normalized/global_index_price_daily/symbol={SOX,DOW_JONES,DOLLAR_INDEX}/year={2025,2026}/data.parquet`
- `data/normalized/global_commodity_futures_daily/symbol={SP500_FUTURES,DOW_FUTURES}/year={2025,2026}/data.parquet`

기존 Windows task는 변경하지 않는다. 06:10 ETF, 06:20 index, 22:10 futures
task는 `--symbols`를 생략하므로 다음 자연 실행부터 registry 전체를 자동 사용한다.
