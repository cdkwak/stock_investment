# BOK ECOS Korean daily market rates

## Scope

`bok_ecos_kr_market_rate_daily` retains two independent annual-percent series for descriptive research: `CORP_BOND_3Y_AA_MINUS` and `CALL_RATE_OVERNIGHT`. They must remain separately labelled and must never be spliced into `kr_treasury_yield_daily` or the BOK Treasury observation dataset.

## Source identity

The requested `StatisticItemList/.../721Y001` lookup on 2026-09-06 returned `7020000` / `회사채(3년, AA-)`, `1010000` / `무담보콜금리(1일)`, and the distinct `1020000` / `무담보콜금리 전체`, but only for A/M/Q cycles. Bounded `721Y001/D` searches returned `INFO-200`. The working daily table is therefore ECOS `817Y002`, cycle `D`, whose search responses identify:

- `010300000` — `회사채(3년, AA-)`
- `010101000` — `콜금리(1일, 전체거래)`

The collector rejects any table code/name, item code/name, unit, date, duplicate, truncation, negative/non-finite value, HTML, or non-`INFO-200` result-code mismatch before promotion.

## Backfill

Run from the repository root with the existing `.env` credential:

```powershell
$env:PYTHONIOENCODING='utf-8'
.venv\Scripts\python.exe scripts/manual/collect/backfill_bok_ecos_market_rates.py --project-root . --start 1987-01-01
```

The operation plans inclusive windows of at most 400 calendar days, makes one request per series/window, waits at least 0.55 seconds between calls, captures immutable raw responses under `data/landing/bok_ecos/kr_market_rates_daily/`, atomically promotes Hive `year=` Parquet partitions, and checkpoints completed windows in `data/state/bok_ecos_kr_market_rate_daily_backfill.json`. Re-running the same range skips checkpointed windows. Use `--verify-only` for provider-free contract and partition validation.

## Availability boundary

The observed retained maxima are 2026-09-04 for the corporate-bond series and 2026-09-03 for the call-rate series. Neither the metadata nor `StatisticSearch` response exposes a publication clock, revision freeze, or vintage timestamp. A BOK historical footnote says the corporate-bond benchmark used A+ unsecured bonds through 2000-09 and AA- unsecured bonds from 2000-10, although current daily API rows label the full retained series `회사채(3년, AA-)`; consumers must treat that methodology boundary as unresolved rather than infer a homogeneous AA- history. Publication lag/finality and predictive point-in-time use remain unverified; no scheduled automation is registered.
