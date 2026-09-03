# OpenDART weekly fundamentals lane

Status: `20:30_EXISTING_BUNDLE / LAST_XKRX_SESSION_OF_ISO_WEEK / PIT_BLOCKED`.

`KR_FUNDAMENTALS_WEEKLY` is a child of the existing `KR_MARKET_DAILY` 20:30
slot. It runs only when the next XKRX session belongs to a different ISO week;
other sessions return `SKIPPED_NOT_REFRESH_DAY` before credentials or network.
The runner loads `.env` without displaying the OpenDART key.

The symbol plan prioritizes Korean watchlist stocks and unions the retained
`kr_fundamentals_quarterly` symbols because the web scanner function imports
web-only modules. It caps the result at 200 symbols. The plan uses the current
year and previous two years, requires room for every base report query plus a
possible corporation-map refresh, and never exceeds 2,600 HTTP calls.

Live execution reuses the existing CFS-first/OFS-fallback capture pipeline:
immutable Landing, call ledger/checkpoint, receipt-date validation
(`period_end <= date(rcept_no)`), candidate validation, approval-digest binding,
and atomic compare-and-swap promotion. A provider or validation error is caught
as failure for this lane while later bundle lanes continue.

Human commands (not executed during implementation):

```powershell
$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python.exe .\scripts\maintenance\run_provider_scheduler.py --project-root . --lane KR_FUNDAMENTALS_WEEKLY --dry-run
$env:PYTHONIOENCODING='utf-8'; .\.venv\Scripts\python.exe .\scripts\maintenance\run_provider_scheduler.py --project-root . --lane KR_FUNDAMENTALS_WEEKLY
```

The dry-run performs no provider calls and reports the planned symbol count,
three years, refresh-day decision, base query calls, and 2,600-call ceiling.
