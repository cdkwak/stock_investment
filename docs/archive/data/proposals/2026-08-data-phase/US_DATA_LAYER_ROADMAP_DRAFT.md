# U.S. Data Layer Roadmap — Draft

> Planning proposal only. It neither changes current authority nor authorizes collection.

## Existing retained scope (do not duplicate)

The repository already retains FRED Treasury yields/spreads, USD FX, Yahoo global indices, Yahoo vendor-continuous Gold/Silver/Copper/WTI/Brent, and CFTC TFF/Disaggregated/Legacy Landing Raw. Their documented PIT limitations remain in force; this roadmap does not recapture or promote them.

## Single-category priority classification

| capability | category | rationale and gate |
|---|---|---|
| US all-security OHLCV | `PAID_OPTIONAL` | High research value but a license, raw/adjusted semantics, historical universe, and retained-data rights are unresolved. Norgate/Polygon pilot only after provider confirmation. |
| Security Master / delisted / symbol history | `PAID_OPTIONAL` | Required companion to OHLCV; no free verified all-history provider. Must arrive before any full-universe price backfill. |
| Distribution / corporate action | `PIT_BLOCKED` | Event identity, original/amended history, price adjustment method, and availability time are not yet evidenced. |
| Cboe P/C + VIX | `PIT_BLOCKED` | Historical values/archives exist but record-level publication timing and storage/redistribution terms are unresolved. |
| FINRA daily short-sale volume | `HIGH_VALUE_SUPPORTING` | Official/no-fee, same-day posted source with explicit scope and revisions. Keep separate from short interest; source-specific collection terms still need a runbook. |
| FINRA equity short interest | `HIGH_VALUE_SUPPORTING` | Official/no-fee semi-monthly position snapshots with published calendar and historical files. Preserve release date/revision state. |
| SEC filing/fundamental observations | `HIGH_VALUE_SUPPORTING` | Official/no-fee source and PIT-relevant acceptance timestamps; canonical metric selection remains blocked. |
| ALFRED vintages | `HIGH_VALUE_SUPPORTING` | Directly resolves existing FRED revision/vintage limitation but needs a source-specific availability policy. |
| ETF holdings | `DEFERRED` | Provider/issuer-level point-in-time holdings, historical coverage and rights vary substantially; defer until a concrete strategy requires it. |
| 13F | `DEFERRED` | Official filings are delayed, manager-level and not a market-wide ownership universe; model only after SEC filing architecture is stable. |
| Form 4 | `DEFERRED` | Official filing events are available but issuer/insider identity, transaction semantics and acceptance-time policy should reuse SEC architecture. |
| Option chain | `PAID_OPTIONAL` | Large, licensing-heavy, contract-lifecycle-sensitive, and not required for current backtest foundation. |

## Ordered ROI sequence

1. Establish a no-network, documented `security_master`/symbol-history design and select a licensed OHLCV path only if its rights/PIT pilot passes.
2. Add official FINRA **Landing-only** source contracts for daily short-sale volume and semi-monthly short interest; retain publication/revision provenance.
3. Establish SEC filing-observation Landing architecture before any fundamentals metric canonicalization.

ALFRED is the next low-complexity supporting source after these three only if the backtest requires revised-vintage macro data. Cboe P/C remains blocked until availability evidence is obtained.

## Cross-source anti-duplication boundaries

- FRED observations are not ALFRED vintages; retain both only if vintage/release semantics are materially different.
- Yahoo global indices are not a U.S. all-security price universe and must not be used to fill delisted security gaps.
- Yahoo continuous commodities are vendor-constructed continuous series, not CFTC positions or futures-contract settlement history.
- FINRA daily short-sale volume, FINRA short interest, exchange short volume and SEC fails-to-deliver are distinct economic variables.
- SEC company facts/FSDS do not supersede original filings; they are separate source families with different update/revision behavior.

## Exit criteria before any collection authorization

Each candidate requires a dataset contract, isolated state/Landing namespace, source-specific access rate policy, provenance/hash policy, schema-fail-closed rule, single-attempt behavior where appropriate, and explicit Normalized/Canonical promotion gate. None is granted by this roadmap.
