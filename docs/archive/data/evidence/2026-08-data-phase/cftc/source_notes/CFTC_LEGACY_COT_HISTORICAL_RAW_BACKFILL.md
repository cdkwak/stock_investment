# CFTC Legacy COT Historical Raw backfill

Status: **RAW_BACKFILL_COMPLETE / LANDING_ONLY / PIT_PREDICTIVE_USE_BLOCKED**  
Final manifest run: `20260816T141445Z_4903292d010d41a2bb3c562d9950b499`

This is a separate source family from TFF and Disaggregated. No participant
category, contract identity, or source row is merged or translated across
families.

## Report-type boundary

| Report type | Source scope | Retained coverage | Raw rows |
|---|---|---:|---:|
| `LEGACY_FUTURES_ONLY` | official Legacy Futures Only | 1986-01-15..2026-08-11 | 287,411 |
| `LEGACY_FUTURES_OPTIONS_COMBINED` | official Legacy Futures-and-Options Combined | 1995-03-21..2026-08-11 | 276,523 |
| `TFF_FUTURES_ONLY` | separate prior collection | not part of this Landing | — |
| `DISAGGREGATED_FUTURES_ONLY` | separate prior collection | not part of this Landing | — |

The two Legacy report types are retained under distinct manifest entries and
per-call operations. The raw source participant columns are unchanged;
`participant_category_policy` is `SOURCE_COLUMNS_UNCHANGED_NO_CROSS_FAMILY_MAPPING`.

## Official archive plan and integrity

- Landing: `data/landing/cftc/legacy_cot_historical_raw/`
- State: `data/state/us_cftc_legacy_cot_historical_raw/latest.json`
- Final manifest: `data/landing/cftc/legacy_cot_historical_raw/20260816T141445Z_4903292d010d41a2bb3c562d9950b499/manifest.json`
- Official original ZIP files: **22**; Raw source rows: **563,934**.
- Hash/provenance verification: **22/22** response bodies reconcile to their
  `call.json` source URL, SHA-256, and byte count; no Legacy backfill lock remains.

CFTC supplies one combined history archive plus 2017–2026 annual archives for
each report type:

| Report type | Combined official ZIP | Annual official ZIP |
|---|---|---|
| `LEGACY_FUTURES_ONLY` | `deacot1986_2016.zip` | `deacot{year}.zip` |
| `LEGACY_FUTURES_OPTIONS_COMBINED` | `deahistfo_1995_2016.zip` | `deahistfo{year}.zip` |

All source years in these supplied ranges have retained rows. 2026 is partial
through source position date 2026-08-11.

## Schema and historical limitations

Both Legacy source families used the same observed 129-field header fingerprint:
`4739160d468db933d8bc0a89e719782c83bcb71c8374f0429b98d730a34c1ae0`.
Legacy header labels are source-native (for example, `Market and Exchange
Names` and `As of Date in Form YYMMDD`) and were retained unchanged.

`LEGACY_FUTURES_ONLY` preserves CFTC's pre-1992-09-30 limitation as metadata:

```json
{
  "before_position_date": "1992-09-30",
  "source_behavior": "MID_MONTH_AND_MONTH_END_ONLY",
  "source_warning": "mid-month data was not published before that time and may contain identifiable data errors"
}
```

This limitation is not applicable to `LEGACY_FUTURES_OPTIONS_COMBINED`, whose
official history starts in 1995.

`position_date` remains source-native. `release_date` is null and is never
inferred because CFTC does not provide historical release dates. This Raw
Landing remains `PIT_PREDICTIVE_USE_BLOCKED`; no Normalized, Derived,
Published, or Canonical artifact was created.
