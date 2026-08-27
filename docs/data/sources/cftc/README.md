# CFTC Commitments of Traders

## Status

- Project status: `RETAINED RAW`; normalized/PIT use remains gated.
- Accepted use: official historical compressed COT archives with report identity preserved.

## Official reference

- [Historical compressed COT files](https://www.cftc.gov/MarketReports/CommitmentsofTraders/HistoricalCompressed/index.htm)
- [COT release schedule](https://www.cftc.gov/MarketReports/CommitmentsofTraders/ReleaseSchedule/index.htm)

Checked-in official file templates:

```text
https://www.cftc.gov/files/dea/history/fut_disagg_txt_<YEAR>.zip
https://www.cftc.gov/files/dea/history/fut_fin_txt_<YEAR>.zip
```

## Authentication

No project credential is used for these public archives.

## Safe read example

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\pilot\pilot_cftc_cot_historical.py --help
```

Allowlist a year and report family, use a bounded timeout, verify ZIP signature
and member names, then save immutable Landing evidence before parsing.

## Project route

- URL/parser provider: `src/stock_data/providers/cftc.py`
- Pilot: `scripts/manual/pilot/pilot_cftc_cot_historical.py`
- Raw backfills: `scripts/manual/backfill/backfill_cftc_*_raw.py`

## Boundaries

- Disaggregated, Traders in Financial Futures, and legacy report families are distinct.
- Never merge different CFTC market codes because their names look similar.
- A report date is not automatically its public release timestamp. Historical predictive use remains blocked without release-date evidence.
