# KB / Toss historical integration audit

Status: retained-evidence baseline; no provider request or dataset write was made.

## Integration verdict

Only sources with verified historical range behavior, understood source-date
semantics, and a survivorship-safe request grain may enter a historical backfill.
The retained repository currently proves two Toss market-level historical operation
families, but their useful targets are already integrated. It proves that Toss stock
history is tied to current-symbol routes. KB metadata proves two historical request
shapes, but neither currently supplies a survivorship-safe historical market universe.

| Rank | Candidate | Retained verdict | Gap replacement value | Required action |
|---:|---|---|---|---|
| 1 | Market-level derivatives investor trading, especially options CALL / PUT | No KB or Toss historical operation is retained | Would replace the highest-value unfilled investor-statistics target | Keep as `HISTORICAL_SOURCE_UNVERIFIED`; require a market/date or date-range response that preserves option right, session, investor class, sides, measures, units, and source date |
| 2 | Market-level program trading | KB has one provisional snapshot slice; Toss has only a blocked stock endpoint | Would replace the unimplemented historical market-series gap if a market-level range exists | KB remains `DAILY_FORWARD_COLLECTION`; reject Toss present-symbol fan-out; define a new provider-bounded market contract only after response grain and units are captured |
| 3 | Foreign ownership | No retained KB/Toss candidate | Would replace the deferred authenticated KRX target only if all-symbol-by-date or market/date history exists | Require source-date universe membership; reject current-symbol fan-out and any current master-data fill |
| 4 | Valuation / fundamentals | No retained KB/Toss candidate | Would replace a production-history gap | Require all-symbol-by-date source coverage, exact reported fields/units, and explicit historical revision/availability limits |
| 5 | Futures/options market history | KB `IVS11560` has date/count history per required instrument code, including OHLCV/value/open interest, but no historical contract-universe operation is retained | Could fill pre-2010 KOSPI200 futures/options gaps | `TRUE_HISTORY_PER_INSTRUMENT / BACKFILL_BLOCKED`; require historical contract membership, contract/maturity/right/strike/session identity, source dates, limits, and provider terms |
| 6 | KB `IVU10430` investor trading | Explicit start/end dates and dated rows are retained for one equity symbol; omitted-symbol market semantics and derivatives are unverified | Does not establish market-level or derivatives investor replacement | `TRUE_RANGE_PER_SYMBOL / BACKFILL_BLOCKED`; reject present-universe fan-out |
| 7 | Toss per-symbol program trading | Endpoint and draft contract exist, but the delisted-symbol sentinel returned HTTP 404 | Unique values, but a present-day symbol fan-out would be survivorship-biased | `REJECT_HISTORICAL_BACKFILL`; retain as blocked unless the provider supplies a historical-universe or market-level operation |
| 8 | Toss per-symbol investor/foreign ownership | True cursor history exists on a current-symbol route; no market/universe endpoint is retained | Potential foreign-holding history, but not survivorship-safe at market grain | `SURVIVORSHIP_BLOCKED`; do not use a current-symbol fan-out |
| 9 | Toss per-symbol credit trading | Endpoint and draft contract exist, but the delisted-symbol sentinel returned HTTP 404 | Security-level fields are not supplied by the retained market-level credit artifact | `REJECT_HISTORICAL_BACKFILL`; do not infer historical membership from today's symbols |
| 10 | Toss short selling | Stock endpoint is not survivorship-safe | Duplicates the retained official Short-selling Trading artifact, which has 2008-01-02..2026-08-07 coverage | Reject as an inferior duplicate |
| 11 | Toss securities lending | Stock endpoint is not survivorship-safe | Duplicates retained official lending detail/market/participant artifacts | Reject as an inferior duplicate |
| 12 | Toss market investor trading | Verified and complete for KOSPI/KOSDAQ: 5,946 rows, 2014-07-01..2026-08-11; already published through a provider-boundary bridge | No remaining historical gap at this grain | Maintenance only; never equate its KRW values with the legacy segment's unknown unit |
| 13 | Toss market-indicator candles | Only KOSPI/KOSDAQ indices and six Korean bond instruments are retained | Indices duplicate longer retained sources; bonds are already complete at 11,162 rows, 2019-01-02..2026-08-10 | No new backfill; maintain provider boundaries and existing availability/unit limits |

## KB boundary

The retained `IVSA0070` implementation describes itself as a provisional snapshot
and explicitly prohibits writes to official historical datasets. The pre-open raw
capture contains an inquiry date of 2026-08-14, a liquidity source date of
2026-08-12, and global-symbol source dates of 2026-08-13. Therefore:

- `snapshot_date` is capture-partition metadata only;
- no common `market_date` may be assigned across the seven slices;
- breadth, program trading, investor flow, liquidity, derivatives summary,
  domestic indices, and global symbols are all `DAILY_FORWARD_COLLECTION` unless
  a different read-only operation independently proves historical support;
- the quarantined pre-open rows must not be replayed until the post-close comparison
  defines a non-ambiguous date contract for each slice;
- mini futures/options remain unobserved and cannot be inferred from other
  derivatives fields.

Other retained KB metadata does not change this forward-only boundary:

- `IVA10370` is a liquidity snapshot with separate lagged `dt`/`dt2` fields;
- `IVU10450` supplies bounded recent per-symbol daily/hourly program rows, not a
  market-level date range;
- `IVU10140`/`IVU10020` foreign-ownership and `IVU10140`/`IVM10050` valuation
  shapes are current snapshot/ranking or per-symbol views, not PIT market history;
  ranking endpoints are selection-biased;
- mini futures/options remain unsupported in the retained metadata.

Existing PIT breadth and official liquidity artifacts are superior to KB forward
snapshots. The retained KRX/FSC futures/options bridge is superior for 2010 onward.
No KB pilot or backfill is presently authorized because retained documentation does
not establish provider bulk-use terms or rate limits.

The final contract should replace the common-date assumption with a slice-specific
observation/source date and one of `CURRENT_DAY_CLOSE`, `PREVIOUS_DAY_CLOSE`,
`INTRADAY/NIGHT`, `LAGGED_SOURCE_DATE`, or `DATE_UNRESOLVED`. A row classified
`DATE_UNRESOLVED` is Landing-only and prevents operational promotion. The precise
column names and nullability remain gated on the post-close retained response; this
audit does not pre-commit them.

## Contract and validation gates for a new historical candidate

A candidate passes integration review only when its pilot establishes all of the
following from retained raw evidence:

1. Provider-bounded dataset name, source operation, layer, frequency, timezone,
   primary key, sort key, and partitions are fixed before normalization.
2. Request grain is market/date, market/date-range, or an all-symbol response for
   each historical date. Present-day symbol enumeration is prohibited.
3. Observation date, provider source date, collection timestamp, and any published
   update/availability timestamp remain separate. Historical retrieval time is not
   original availability time.
4. Exact source labels, values, signs, zeros, nulls, units, session/right/measure
   dimensions, and valid-empty responses are preserved without synthesis.
5. Validation rejects duplicate primary keys, malformed dates/identities, unexpected
   nulls, NaN/infinity, schema drift, rows outside the requested scope, and cursor
   non-advancement.
6. Landing response hashes, parsed row counts, checkpoint scopes, Normalized rows,
   and final partitions reconcile deterministically. Retry/backoff and every request
   are represented in an append-only ledger.
7. Existing superior sources are not overwritten or silently concatenated. Any
   provider bridge preserves source, operation, units, availability limits, and
   predictive-use status.

## Independent backfill audit

Before `DATA_STATUS` promotion, an independent reviewer must reproduce the input
Landing inventory and hashes, then verify request-ledger totals, checkpoint/resume
idempotence, one authoritative writer, primary-key uniqueness, required-null policy,
finite numerics, requested-versus-observed coverage, valid-empty scopes, source-date
semantics, Landing-to-Normalized reconciliation, partition inventory, and provenance.
No ambiguous date mapping or survivorship failure may remain. A successful artifact
with unknown historical availability may be integrated as
`PREDICTIVE_USE_BLOCKED`; it must not be labeled PIT-safe.

## Retained evidence

- `docs/data/DATA_STATUS.md`
- `src/stock_data/contracts/tossinvest_historical.py`
- `src/stock_data/contracts/kbsec_snapshot.py`
- `src/stock_data/pipelines/kbsec_snapshot.py`
- `data/state/toss_survivorship.json`
- `data/state/toss_kr_market_investor_trading_daily.json`
- `data/state/kbsec_daily_snapshot.json`
- `data/state/audits/kbsec_daily_snapshot/e37cf7786a2f619be003390b9d1c59537a66579d20fb1770b74615f240aa1939.json`
- `docs/archive/data/operations/2026-08-data-phase/superseded/KRX_PROGRAM_TRADING_SOURCE_READINESS.md`
- `docs/archive/data/operations/2026-08-data-phase/superseded/PYKRX_FOREIGN_OWNERSHIP_PILOT.md`
- `docs/archive/data/audits/2026-08-data-phase/TOSS_HISTORICAL_CANDIDATE_AUDIT.md`
