# Korean ETF current universe daily operation

Status: `MANUAL_ONE_CALL_READY / CURRENT_LIST_DISPLAY_IDENTITY / PIT_BLOCKED`.

## Purpose and boundary

`kr_etf_universe_daily` is a dated snapshot of every ETF returned by KRX's ETF
전종목 기본정보 screen at collection time. It exists so local identity search is
not limited to the small `kr_etf_master` watchlist. It is not the historical Raw
`MDCSTAT04301` universe, does not establish delisting history or revision
finality, and must not be back-projected into research or predictive inputs.

The 2026-09-04 reference observation contained 1,167 rows, including `0015B0`
KoAct 미국나스닥성장기업액티브 (listed 2025-02-25). Row count is intentionally
not hard-coded because KRX's current listed universe changes.

## Source and contract

- Provider call: `from pykrx.website.krx.etx.core import ETF_전종목기본종목`,
  then exactly one `ETF_전종목기본종목().fetch()`.
- KRX operation: `MDCSTAT04601`; credentials are loaded from the project `.env`
  through the existing pykrx initialization pattern and are never printed.
- Normalized contract: `KR_ETF_UNIVERSE_DAILY` version 1 in
  `src/stock_data/contracts/kr_etf.py`.
- Primary key: `(source_date, symbol)`; partition:
  `source_date=YYYY-MM-DD/data.parquet`.
- Identity mapping: `ISU_SRT_CD -> symbol`, `ISU_ABBRV -> name`, `ISU_NM ->
  full_name`, `ISU_CD -> isin`, `LIST_DD -> listing_date`, and
  `ETF_OBJ_IDX_NM -> underlying_index`. Optional absent source columns remain
  null; codes remain six-character uppercase alphanumeric strings.
- Constant identity: `market=KRX`, `security_type=ETF`,
  `listing_status=LISTED_AT_SOURCE_DATE`, `source=KRX/pykrx`.

## Landing-first and replay behavior

The operation checks the validated Normalized date first. An already retained
date returns `ALREADY_CURRENT` with API calls zero. If Normalized is missing but
that date's immutable Landing exists, it revalidates and promotes from Landing
with API calls zero. Otherwise it makes exactly one provider call, atomically
creates
`data/landing/pykrx/kr_etf_universe_daily/source_date=YYYY-MM-DD/response.json`,
reads it back, normalizes it, and atomically publishes the full Normalized
dataset while preserving all earlier valid date partitions. Empty, malformed,
duplicate, or future-listing identity rows fail closed before Normalized write.

Normal tests inject a synthetic frame and never instantiate the live provider.
There is no retry, fallback, cross-provider merge, price collection, or broker
mutation.

## Human command

Run from the repository root with Python 3.13. The default source date is the
current KST date:

```powershell
$env:PYTHONIOENCODING='utf-8'; .venv\Scripts\python.exe scripts\manual\collect\refresh_kr_etf_universe.py --project-root .
```

An exact replay date may be supplied with `--source-date YYYY-MM-DD`.

## Intended scheduler hook (not registered)

No scheduler lane or `expected_latest.py` route is added by this change. A later
owner may register one daily post-listing-change/current-list hook that invokes
`run_kr_etf_universe_daily` once for the KST date, treats `ALREADY_CURRENT` as
success, and keeps this dataset display-identity-only. That later change must
update scheduler ownership, expected-latest policy, and Data Status together.
