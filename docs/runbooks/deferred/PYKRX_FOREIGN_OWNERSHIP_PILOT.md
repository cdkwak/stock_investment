# C010 authenticated KRX foreign-ownership readiness

Status: offline pilot ready; live execution prohibited while A007 owns KRX.

## Verified local source semantics

- Installed pykrx source declares `MDCSTAT03701` as full-market/date and
  `MDCSTAT03702` as per-symbol/date-range.
- Full-market `balance_limit=0` means no restriction-only filter; `1` would
  return only foreign-ownership-limit securities and is not appropriate for a
  complete market dataset.
- C005 retained lossless `03702` response for `005930` on 2026-08-10 contains
  `LIST_SHRS`, `FORN_HD_QTY`, `FORN_SHR_RT`, `FORN_ORD_LMT_QTY`, and
  `FORN_LMT_EXHST_RT`. The two counts are shares; both ratios are percent, not
  fractions. Raw 46.61 is consistent with 2,725,008,527 / 5,846,278,608 * 100.
- pykrx's public wrapper casts ratios to float16 and replaces blanks with zero.
  A future provider parser must instead parse the lossless raw response, retain
  missing separately from valid zero, and use a non-lossy decimal/float64 type.

## Candidate contract after successful full-market pilot

Candidate name: `kr_equity_foreign_ownership_daily`; candidate primary key:
`(date, market, symbol)`. Proposed source-preserving columns are `date`,
`market`, `symbol`, `source_name`, `listed_shares`, `foreign_held_shares`,
`foreign_ownership_percent`, `foreign_order_limit_shares`,
`foreign_limit_exhaustion_percent`, `source_operation`, and `collected_at`.

This is a design, not a registered DatasetContract. It becomes eligible only
after `03701` returns are captured and audited. The parser must preserve raw
zeros and nulls; reject duplicate keys, negative share/ratio values, nonfinite
values, malformed dates/symbols, and schema drift. Ratio recomputation is a
diagnostic tolerance check (0.02 percentage points), not a replacement for the
source value.

## Survivorship and point-in-time policy

The historical collector, if later approved, must use `03701` full-market/date
responses. It must never fan out from today's ticker list or use `03702` to
construct the universe. `03702` is bounded QA for a currently listed symbol and
known historical/delisted KOSPI/KOSDAQ symbols only. The 2008-01-02 probes are
source-coverage sentinels and do not establish earliest availability.

Source rows are labeled by trading date, but local evidence does not establish
the original publication timestamp, later revisions, or when a historical
response first became available. Therefore `availability_date` remains unknown
and predictive use is blocked until timing/revision behavior is separately
verified. Listing shares and foreign order limits are date-specific source
values; they must not be filled from current master data.

## Failure and empty policy

HTTP 403/429, non-200, HTML/restriction pages, non-JSON, source error payloads,
missing `output`, and required-field drift stop immediately. A non-empty recent
full-market response is required. Empty historical/delisted sentinel responses
are recorded as `COVERAGE_EMPTY`, not converted to success rows and not treated
as transport/parser failures.

## Bounded execution gate

The pilot is seven sequential business calls, hard-capped at 15 raw HTTP calls
including authentication/session overhead, retry=0, with 8??0 seconds between
business calls. Exact non-auth bodies, hashes, redacted append-only ledger,
manifest, and checkpoint are retained under Landing. It uses the same D-owned
KRX lock path as A007, so it fails closed if A007 is active.

After D confirms A007 is stopped and integrates these files, run exactly:

```powershell
Set-Location C:\Users\k4545\Desktop\stock_investment_rev1
.\.venv\Scripts\python.exe .\scripts\manual\pilot_pykrx_foreign_ownership.py --confirm-live-manual-pilot
```

Pilot success authorizes only contract/parser review. It does not authorize a
historical backfill.

