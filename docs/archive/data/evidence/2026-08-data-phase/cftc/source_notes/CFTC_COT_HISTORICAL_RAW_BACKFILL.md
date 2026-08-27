# CFTC COT Historical Raw backfill

Status: **RAW_BACKFILL_COMPLETE / LANDING_ONLY / PIT_PREDICTIVE_USE_BLOCKED**  
Final manifest run: `20260816T140716Z_63c354599ecf4d46a1e6fbc76edd0ba8`  
Scope: official CFTC TFF and Disaggregated **Futures Only** historical files,
from the official 2006 start through the source contents available on
2026-08-16 UTC.

## Storage and integrity

- Landing and exact per-call provenance: `data/landing/cftc/cot_historical_raw/`
- Final manifest: `data/landing/cftc/cot_historical_raw/20260816T140716Z_63c354599ecf4d46a1e6fbc76edd0ba8/manifest.json`
- US-only state: `data/state/us_cftc_cot_historical_raw/latest.json`
- Physical official ZIP files: **22**; Raw source rows: **229,850**.
- Every retained response reconciled to its `call.json` provider, source URL,
  SHA-256, and byte count before use. The second manifest pass used only these
  verified local bodies; it made no new network request.

CFTC provides two historical 2006–2016 combined ZIPs and then individual 2017
through 2026 annual ZIPs. Therefore the 22 physical originals are two combined
files plus 10 annual files for each family—not 42 duplicated annual extracts.
The prior attempted `fut_fin_txt_2006.zip` returned HTTP 404 and remains an
immutable failed Landing record; it is not an official provided history path
and is excluded from this completed scope.

| Family | 2006–2016 source | 2017–2026 source | Raw rows |
|---|---|---|---:|
| TFF | `fin_fut_txt_2006_2016.zip` | `fut_fin_txt_{year}.zip` | 59,848 |
| Disaggregated | `fut_disagg_txt_hist_2006_2016.zip` | `fut_disagg_txt_{year}.zip` | 170,002 |

All calendar years 2006–2026 have a source row count for both families. 2026
is partial: both target families end at position date **2026-08-11**.

## Schema observations

Every source satisfied required market/date/code/open-interest/category/unit and
`FutOnly_or_Combined` fields. No unhandled schema anomaly remained.

| Family | Header fields | Observed header fingerprint |
|---|---:|---|
| TFF | 87 | `d06466b76647943cebbdab425b2a661d53f0ab7a60f890cf1aa581d167dfd05d` |
| Disaggregated | 191 | `c90472ca3e659196581baf698081a1b61c2104352186858fe27321044f6c63d3` |

The combined historical files use source report-date values such as
`MM/DD/YYYY 12:00:00 AM`, while newer annual files use ISO-like values. The
raw values remain unchanged; the parser validates both official forms. Target
matching uses the exact CFTC contract-market, market, and commodity identifier
triples observed in the 2025 pilot. A nonmatching historical identity is
reported as missing rather than guessed or remapped.

## Target coverage

| Target | Position-date coverage | Raw rows | Identity limitation |
|---|---:|---:|---|
| S&P 500 | 2006-06-13..2026-08-11 | 1,053 | none |
| Nasdaq-100 | 2010-06-15..2026-08-11 | 844 | exact pilot identity absent before 2010-06-15 |
| Russell 2000 | 2021-11-30..2026-08-11 | 134 | exact pilot identity absent in 2006–2020; no alias inference |
| Treasury 2Y | 2006-06-13..2026-08-11 | 1,053 | none |
| Treasury 5Y | 2006-06-13..2026-08-11 | 1,053 | none |
| Treasury 10Y | 2006-06-13..2026-08-11 | 1,053 | none |
| Long-bond / Ultra source identity | 2010-03-02..2026-08-11 | 859 | source names include Long-Term and Ultra; not Canonicalized |
| Gold | 2006-06-13..2026-08-11 | 1,053 | none |
| Silver | 2006-06-13..2026-08-11 | 1,053 | none |
| Copper | 2006-06-13..2026-08-11 | 1,053 | none |
| WTI | 2006-06-13..2026-08-11 | 1,053 | none |
| Natural Gas | 2006-06-13..2026-08-11 | 1,053 | none |

`position_date` remains the source `As_of_Date_In_Form_YYMMDD`. Historical
`release_date` is **null** for all records: CFTC does not publish a historical
release-date list, so Friday timing must not be inferred. Consequently this
Raw Landing remains `PIT_PREDICTIVE_USE_BLOCKED`. No Normalized, Derived,
Published, or Canonical artifact was created.
