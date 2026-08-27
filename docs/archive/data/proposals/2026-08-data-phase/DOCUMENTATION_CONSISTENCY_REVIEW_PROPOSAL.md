# Documentation Consistency Review — Proposal Only

> Review date: 2026-08-16. No authoritative status, Dataset Index, contract, state, or CFTC document was edited.

## Observations

| area | evidence | proposal; not an applied change |
|---|---|---|
| CFTC completion visibility | Current CFTC backfill documents state TFF/Disaggregated and Legacy Raw backfills are complete, with separate Landing/state namespaces and release-date/PIT limitations. | On the next authorized Data Status consolidation, add one concise CFTC Raw completion row with report-family scope, `release_date = null`, and `PREDICTIVE_USE_BLOCKED`; do not label it canonical or predictive-ready. |
| KRX foreign ownership status conflict | `DATA_STATUS.md` states Raw backfill complete; `DATASET_INDEX.md` still shows `READY_FOR_BACKFILL` and a pending collector. | Authorized document owner should reconcile Dataset Index routing to the higher-authority status. Do not update it during this research task. |
| KRX fundamental stream | `DATA_STATUS.md` describes `STOPPED_SCHEMA_ANOMALY`; user reports another process is currently running. | Do not infer a new status or touch any KRX file/lock. The process owner must reconcile run evidence after it stops. |
| U.S. research role | US OHLCV and newly written FINRA/SEC/Cboe documents are research/design evidence, not active contracts or collection authorization. | Keep under `docs/data/research/`; do not add active Dataset Index entries until a contract, source rights, and an authorized operation exist. |
| Existing global sources | Dataset Index already registers Yahoo global indices, FRED yields/FX, and Yahoo commodity continuous futures with limitations. | Any eventual U.S. security master/OHLCV entry must state its non-overlap: Yahoo global indices are not all-security OHLCV; FRED is macro; vendor-continuous commodities are not individual futures contracts. |
| Archive/current-authority separation | Current status explicitly says archived research/runbooks are evidence, not authorization. | Label all new documents as draft/audit only and avoid linking them as current routes. |

## Required owner decisions before edits

1. Whether completed CFTC Raw families belong in the next authoritative status consolidation and the exact wording of their predictive blocker.
2. Whether the Dataset Index foreign-ownership record should be updated to match current status and what immutable inventory evidence supports any count/path update.
3. Whether a licensed U.S. OHLCV pilot is in scope. If not, keep all source-selection material as `PAID_OPTIONAL` research only.

## Review conclusion

No erroneous authoritative claim was changed. The only identified clear stale-view candidate is the foreign-ownership routing mismatch; correcting it is explicitly deferred to the document owner. The CFTC documents themselves consistently preserve Raw-only and PIT-blocked semantics.
