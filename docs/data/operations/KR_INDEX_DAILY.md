# Korean index daily lane

Status: `20:30_AUTOMATION_ACTIVE / FOUR_CALL_EXACT_DATE / ONE_CALL_IT_BACKFILL`.

`KR_INDEX_DAILY` retains KOSPI (`1001`), KOSDAQ (`2001`), KOSPI200 IT
(`1155`, canonical symbol `KOSPI200_IT`), and the separately contracted
KOSPI200 (`1028`). The 20:30 lane makes one exact-date pykrx call per ticker,
writes every response to immutable Landing, validates all registered identities,
and promotes `kr_index_daily` plus `kr_kospi200_index_daily` as one atomic unit.
Ticker `1150` is not registered.

The historical IT onboarding call is deliberately unchunked: one
`stock.get_index_ohlcv(start, end, "1155")` request, then Landing-first validation
and atomic merge. Run these human commands from the repository root; they were
not executed during implementation.

```powershell
$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python.exe .\scripts\maintenance\run_provider_scheduler.py --project-root . --lane KR_INDEX_DAILY --dry-run
$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python.exe -c "import sys,json; sys.path.insert(0,'src'); from pathlib import Path; from stock_data.orchestration.kr_index_daily_live import backfill_kospi200_it_history; print(json.dumps(backfill_kospi200_it_history(Path('.'), start_date='2010-01-04', end_date='2026-09-04', confirm_live=True), ensure_ascii=False))"
$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python.exe .\scripts\maintenance\run_provider_scheduler.py --project-root . --lane KR_INDEX_DAILY
```

The backfill refuses conflicting retained rows and records a one-call receipt.
No extra Windows task or slot is created.
