# C011 authenticated KRX ETF readiness

Status: offline pilot ready; live execution prohibited while A007 owns KRX.

## Verified local source boundary

- Installed pykrx 1.2.8 declares `MDCSTAT04301` as ETF full-market/date and
  `MDCSTAT04501` as per-ETF/date-range.
- C005 retained a lossless 2026-08-10 `04301` response with 1,160 rows. It
  includes `069500` / `KR7069500007` and the full raw field inventory enforced
  by the pilot.
- `MDCSTAT04601` is a current ETF basic-information snapshot with listing date,
  index name, manager, shares, fees, and other metadata. It has no request date
  and cannot reconstruct the historical ETF universe or delistings.
- `MDCSTAT05901` and `MDCSTAT06001` are per-symbol tracking-error and price/NAV
  deviation histories. They are verified in local source but excluded from the
  first OHLCV pilot; their revision, formula, and availability semantics need a
  later separate pilot.

## Field evidence and limits

Local wrappers identify `NAV`/`LST_NAV`, market OHLC, volume, trading value, and
`OBJ_STKPRC_IDX` as the underlying-index value. C005 shows KODEX 200 close
98,265, NAV 98,250.40, and underlying index 977.84. These values establish
distinct source measures; they do not establish a universal scaling relation.
Underlying indices across ETF families are not directly comparable.

The local source does not independently establish currency/unit metadata for
every field, original publication timestamps, revision policy, NAV calculation
cutoff, or the precise tracking formula. A future parser must preserve raw
values and missing separately from zero and must not derive NAV, deviation, or
tracking error from guessed formulas.

## Candidate contract after successful pilot

Candidate only: `kr_etf_ohlcv_daily`, PK `(date, symbol)`. Proposed fields:
`date`, `symbol`, `isin`, `source_name`, source NAV, open/high/low/close,
volume, trading value, market cap, investment-asset net assets, listed shares,
underlying-index name/value, source operation, and `collected_at`.

No DatasetContract is registered until historical `04301` responses are
captured, schema consistency is audited, and units/availability policy is
approved. OHLC ordering and nonnegative volume/value are diagnostics. All-zero
no-trade source rows are preserved rather than discarded.

## Survivorship and PIT policy

Historical collection, if separately approved, must use `04301` full-market
responses by date. It must not fan out from the current `04601` list. This
retains ETFs that later delisted even though no locally verified delisted ETF
identifier was guessed for this pilot. `04501` is QA only for current KODEX 200
at a recent date and the 2008-01-02 historical source-coverage sentinel.

The 2008 sentinel is not an earliest-coverage claim. Listing/delisting dates
must come from dated source evidence; current metadata may not be backfilled
into old observations. Predictive use is blocked until publication timing and
revision behavior are established.

## Empty/failure policy and execution gate

HTTP 403/429, other non-200, HTML/restriction pages, non-JSON, source error
payloads, absent `output`, and required-field drift stop immediately. Recent
full-market and current-symbol probes must be non-empty. The weekend symbol
probe must be empty and is labeled `VALID_EMPTY`; an empty historical sentinel
is `COVERAGE_EMPTY`, not a transport/parser failure.

The pilot has five sequential business calls and a hard 13-raw-call cap,
including up to eight authentication/session calls. It uses retry=0,
parallelism=1, a 20-second timeout, 8??0 second business throttle, lossless
non-auth Landing bodies, hashes, redacted ledger, manifest, checkpoint, and the
same fail-closed D-owned KRX lock path as A007.

After D integrates the files and confirms A007 stopped, no KRX process remains,
and the shared lock is absent, run exactly:

```powershell
Set-Location C:\Users\k4545\Desktop\stock_investment_rev1
.\.venv\Scripts\python.exe .\scripts\manual\pilot\pilot_pykrx_etf.py --confirm-live-manual-pilot
```

Pilot success permits contract/parser review only. It does not authorize a
historical backfill or the tracking/deviation endpoints.
