# CFTC COT historical Landing pilot

Status: **PILOT_COMPLETE_REVIEW_REQUIRED / LANDING_ONLY / PIT_BLOCKED**  
Pilot run: `20260816T135401Z_fde6d38c055d4fb4be800991a6258960`  
Scope: exactly two official CFTC 2025 annual Futures Only ZIP files; retry zero;
no Normalized or Canonical write.

The pilot has been followed by the official historical Raw backfill; current
coverage, source manifests, and ongoing PIT restriction are in
[CFTC COT Historical Raw backfill](CFTC_COT_HISTORICAL_RAW_BACKFILL.md).

## Retained source evidence

| Report family | Official source URL | Capture (UTC) | Bytes | SHA-256 |
|---|---|---:|---:|---|
| Traders in Financial Futures (TFF) | `https://www.cftc.gov/files/dea/history/fut_fin_txt_2025.zip` | 2026-08-16T13:54:01.391466Z | 627,068 | `2ea0cda6395f7dd6501c27422be3763e1f2f5b41b768cfd36b871f092a07d438` |
| Disaggregated | `https://www.cftc.gov/files/dea/history/fut_disagg_txt_2025.zip` | 2026-08-16T13:54:01.513194Z | 2,420,076 | `17ac2fef1b53303d01e486bbf5768a4e062308a3c34a2970871f3eab89829cbc` |

The exact ZIP responses and per-call provenance are retained under
`data/landing/cftc/cot_historical_pilot/20260816T135401Z_fde6d38c055d4fb4be800991a6258960/`.
The separate US-only checkpoint is
`data/state/us_cftc_cot_historical_pilot/latest.json`.

The original execution stopped because the official ZIP schema uses
`Report_Date_as_YYYY-MM-DD`, while an older variable-name page labels a
different field name. That stop manifest is retained unchanged. A parser-only,
zero-network revalidation reconciled the retained bodies and wrote
`adoption.json`; it is the authoritative pilot result.

## Confirmed bounded coverage

All dates below are source `As_of_Date_In_Form_YYMMDD` (Tuesday position date).
Each source market had unique position dates.

| Family | Target | Official source market name | Coverage | Rows |
|---|---|---|---:|---:|
| TFF | S&P 500 | `E-MINI S&P 500 - CHICAGO MERCANTILE EXCHANGE` | 2025-01-07..2025-12-30 | 52 |
| TFF | Nasdaq-100 | `NASDAQ-100 Consolidated - CHICAGO MERCANTILE EXCHANGE` | 2025-01-07..2025-12-30 | 52 |
| TFF | Russell 2000 | `MICRO E-MINI RUSSELL 2000 INDX - CHICAGO MERCANTILE EXCHANGE` | 2025-01-07..2025-12-16 | 48 |
| TFF | Treasury 2Y | `UST 2Y NOTE - CHICAGO BOARD OF TRADE` | 2025-01-07..2025-12-30 | 52 |
| TFF | Treasury 5Y | `UST 5Y NOTE - CHICAGO BOARD OF TRADE` | 2025-01-07..2025-12-30 | 52 |
| TFF | Treasury 10Y | `UST 10Y NOTE - CHICAGO BOARD OF TRADE` | 2025-01-07..2025-12-30 | 52 |
| TFF | Ultra Treasury Bond | `ULTRA UST BOND - CHICAGO BOARD OF TRADE` | 2025-01-07..2025-12-30 | 50 |
| Disaggregated | Gold | `GOLD - COMMODITY EXCHANGE INC.` | 2025-01-07..2025-12-30 | 52 |
| Disaggregated | Silver | `SILVER - COMMODITY EXCHANGE INC.` | 2025-01-07..2025-12-30 | 52 |
| Disaggregated | Copper | `COPPER- #1 - COMMODITY EXCHANGE INC.` | 2025-01-07..2025-12-30 | 52 |
| Disaggregated | WTI | `WTI-PHYSICAL - NEW YORK MERCANTILE EXCHANGE` | 2025-01-07..2025-12-30 | 52 |
| Disaggregated | Natural Gas | `HENRY HUB - NEW YORK MERCANTILE EXCHANGE` | 2025-01-07..2025-12-30 | 52 |

## Schema and PIT boundary

The raw ZIP members are CSV-formatted text. Both have source market name,
position date, source report date, CFTC market/commodity codes, open interest,
contract units, and `FutOnly_or_Combined`. TFF exposes Dealer, Asset Manager,
Leveraged Money, and Other Reportable categories; Disaggregated exposes
Producer/Merchant, Swap, Managed Money, and Other Reportable categories.

`Report_Date_as_YYYY-MM-DD` is preserved as a **source report-date field**. It
is not a release timestamp. CFTC states that the positions are generally as of
Tuesday and reports are generally released Friday, but holidays can vary the
schedule; it also states that no historical release-date list is available.
Accordingly `release_date` remains null and must never be inferred from either
source date. This Landing is not eligible for predictive/Backtest use until a
separate historical release-date/availability policy is reviewed.

No CFTC contract was registered, and no raw field has been transformed,
imputed, or promoted to Normalized/Canonical data.
