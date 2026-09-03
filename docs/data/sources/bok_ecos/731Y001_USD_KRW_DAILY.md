# BOK ECOS 731Y001 USD/KRW daily source contract

Status: implementation complete with offline fixtures; first bounded live collection
is pending a human run.

## Source identity and verification boundary

- Official guide: [ECOS Open API — StatisticSearch](https://ecos.bok.or.kr/api/#/)
- Operation: `StatisticSearch`
- Table: `731Y001` (`주요국 통화의 대원화환율`)
- Cycle: `D`
- Item: `0000001` (`원/미국달러(매매기준율)`)
- Value unit retained from the response: expected `원`, never inferred or converted.

The official ECOS guide host and `StatisticSearch` application were reachable on
2026-09-03, but its client-rendered parameter/table content could not be opened in
this implementation environment. Therefore the table name, item name, response
field set, `INFO-200` meaning, and their continued official-guide publication are
**UNVERIFIED in this implementation session**. They are implemented from the
user-supplied, verified 2026-09-03 specification and the project's existing
`817Y002` StatisticSearch request/validation pattern. The collector fails closed
if returned table, cycle/item identity, names, unit, date, numeric value, or result
shape differs.

The provider's exact publication timestamp, holiday calendar, revision policy,
vintage semantics, and finality are **UNVERIFIED**. The operational target is a
bounded project rule, not an official timing claim: today after 17:00 KST;
otherwise the previous weekday. An absent target row is
`EXPECTED_PROVIDER_LAG`, not a failed collection. Predictive/backtest use remains
blocked until publication timing and PIT/finality are verified.

## Request and response contract

Credential-bearing URL shape (documentation only; never log the expanded URL):

```text
StatisticSearch/{key}/json/kr/{start}/{end}/731Y001/D/{YYYYMMDD}/{YYYYMMDD}/0000001
```

The accepted response is `StatisticSearch.row[]` with exact required fields
`TIME`, `DATA_VALUE`, `ITEM_CODE1`, `ITEM_NAME1`, `UNIT_NAME`, `STAT_CODE`, and
`STAT_NAME`. `RESULT.CODE/MESSAGE` is the provider error envelope; `INFO-200` is
handled as a valid no-data response under the unverified boundary above. Other
provider errors and malformed/identity-mismatched responses fail closed.

`BOK_ECOS_API_KEY` is read from the environment after the runner loads the project
`.env` with `python-dotenv`. The key, expanded URL, authorization material, and
request exception text must never be printed, logged, or persisted.

## Storage and consumer boundary

- Dataset: `bok_ecos_usd_krw_daily`
- Layer: Normalized, Parquet partitioned by `year`
- Key: `date`
- Columns: `date`, `rate_krw_per_usd`, `item_code`, `stat_code`, `unit`, `source`,
  `source_operation`, `retrieved_at`
- Landing: immutable raw JSON, redacted call ledger, and manifest before promotion
- Promotion: schema/read-back validated, atomic, append-only, idempotent replay;
  conflicting same-date values fail closed rather than revise history silently
- Backfill bound: at most 400 calendar days per explicit `--start/--end` run
- Daily lane: `BOK_FX_DAILY`, at most 30 missing weekday sessions, oldest first;
  zero calls when already current
- Consumers: dashboard display and account valuation only; no backtest/predictive
  use until publication timing and PIT/finality are verified

## First bounded human-run sequence

These commands intentionally were **not** run during implementation. Run them in
order from the repository root. The first command is the only historical backfill;
the dry-run must make zero provider calls, and the final command runs one bounded
daily-lane occurrence.

```powershell
$env:PYTHONIOENCODING='utf-8'
.\.venv\Scripts\python.exe .\scripts\manual\collect\refresh_bok_ecos_fx_daily.py --project-root . --start 2026-06-01 --end 2026-09-03 --confirm-live
.\.venv\Scripts\python.exe .\scripts\maintenance\run_provider_scheduler.py --project-root . --lane BOK_FX_DAILY --dry-run
.\.venv\Scripts\python.exe .\scripts\maintenance\run_provider_scheduler.py --project-root . --lane BOK_FX_DAILY
```

The scheduler lane belongs to the existing 17:10 KST BOK task mapping and follows
the Treasury observation lane. This change does not install or mutate the Windows
task; task-definition deployment remains governed by the scheduler runbook.
