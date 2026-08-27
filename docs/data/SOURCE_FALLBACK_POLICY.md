# Source Fallback Policy

## Purpose and default

This policy prevents a provider outage, missing value, or collector error from
silently changing economic meaning. The default action is **preserve the
provider-specific gap**. A fallback is exceptional and requires the registry
record plus this policy to pass every gate.

Standing [Data Status](DATA_STATUS.md) authorization governs collection and
contract-valid promotion. This policy decides only whether a fallback preserves
economic meaning; it is not an additional permission gate. Payment, license or
contract acceptance, and silent Raw/Normalized/Canonical rewriting remain
outside its scope.

## Non-negotiable rules

1. Never silently substitute a provider, endpoint, market, or session.
2. Never average, splice, rescale, or synthetically merge source values to
   fill a gap. Preserve provider lineage on every retained row/artifact.
3. A fallback must retain its own raw provenance, source operation, retrieval
   time, date semantics, and version/revision information. It must not inherit
   the failed provider's provenance.
4. A failed request, blocked source, empty response, or unavailable date does
   not authorize zero filling, previous-value carry, symbol remapping, or an
   alternate source.
5. If a gate fails, leave the data gap visible and retain the failure evidence.

## Equivalence gate

The minimum equivalence status for an automatic or pre-approved fallback is:

```text
CONFIRMED_OFFICIAL
or
CONFIRMED_EMPIRICAL_MULTI_DATE
```

`PARTIAL_EQUIVALENCE`, `UNRESOLVED`, and `NOT_EQUIVALENT` never pass. Even a
passing equivalence status is necessary but not sufficient: it must refer to
the exact proposed source pair and economic variable, rather than only to one
field or one unit.

The candidate must additionally prove all of the following:

| Gate | Required proof |
|---|---|
| Economic meaning | Same economic variable and field semantics; participant categories, venue, instrument, and aggregation definition match. |
| Market and universe | Same market scope and an historical universe/delisted treatment at least as safe as the primary. |
| Session | Same regular/extended/combined session and same close/cut-off convention. |
| Dates | Same observation/reference/effective-date meaning; no source-date assumption. |
| Units | Same unit and multiplier, or an accepted direct equivalence that explicitly covers the conversion. No guessed multiplier. |
| PIT | Candidate has equal-or-better publication/availability/revision evidence for the requested predictive use. |
| License | Collection, retention, internal use, and any intended redistribution are allowed for the exact operation. |
| Operational safety | Contract, checkpoint, rate/lock policy, and active-runbook authorization exist; raw capture and schema validation are available. |

## Decision procedure

1. Record the primary-source incident and preserve any valid existing data.
2. Locate the exact variable and source-family row in
   [Source Registry](SOURCE_REGISTRY.md); do not choose by a similar label.
3. Test every gate above and record the evidence. A candidate that lacks one
   proof is rejected without retrying it under another name.
4. Obtain the required explicit operational authorization when the registry
   says `fallback_allowed=NO`, or when a new collector,
   license, or contract would be needed.
5. Capture the fallback Landing response under its own provider namespace;
   validate schema/key/duplicates before any promotion.
6. Surface the provider change in status, lineage, and consumer metadata. Do
   not overwrite primary rows merely because a fallback succeeded.

## Current automatic-fallback set

The following exact routes are accepted at the reusable orchestration boundary:

| Dataset / exact route | Eligible primary failure | Fallback identity | Limits | Recovery |
|---|---|---|---|---|
| `fred_vix_daily:VIXCLS` | direct FRED parser `SCHEMA_ERROR` only, after its one counted primary request | FinanceDataReader 0.9.202 `FRED:VIXCLS`, actual upstream FRED `fredgraph.csv` | one scoped fallback attempt, exactly two counted GETs, timeout 10 seconds, retry zero; raw missing values are rejected before FinanceDataReader forward-fill; no timeout/HTTP/auth/rate-limit cascade | validate direct FRED again on the next normal scheduled invocation (or an authorized health check), atomically close the scoped circuit with the primary value, and never rewrite historical fallback observations |
| `KR_EQUITY_REGULAR_CLOSE:XKRX:{000660,005930}` | pykrx technical failure only: `TIMEOUT`, `HTTP_ERROR`, or `RATE_LIMITED` | FinanceDataReader `NAVER:{symbol}` for the same exact symbol and expected KRX trading date | provider-aware bounded primary retry, then bounded fallback; wrong date, identity mismatch, empty/schema/price error, or ambiguous semantics fail closed without fallback; values remain atomic `CurrentObservation`, `display_only=true`, `pit_safe=false`, `AS_RETRIEVED`; no canonical/history/Backtest promotion | retry pykrx on the next eligible completed KRX session; preserve the prior valid display observation if both routes fail; activation and scheduling may proceed under standing Data authority with the same semantic boundary |

The accepted code boundaries are deliberately narrower than generic provider
substitution. `src/stock_data/orchestration/fred_vix_fallback.py` owns the exact
VIX policy. `src/stock_data/orchestration/kr_equity_regular_close.py` owns the
two-symbol display-only Korean close policy. Both require an atomic
data+decision+circuit transaction. The existing `fred_vix` prepare/promote path
is active with unchanged cadence; the Korean close path is implemented and
tested offline but is not provider- or scheduler-activated.

Every other FinanceDataReader 0.9.202 route inspected by UR-108, except the
two explicitly user-authorized display-only Korean close routes above, remains
route-specifically `cross_check_only`, `hold`, or `exclude`. Community KRX
caches, Naver and Yahoo automated-access rights, current-universe/PIT limits,
and unresolved semantics prevent those routes from passing. This is not a
blanket prohibition on FinanceDataReader. The former UR-108 matrix artifact is
not retained; the route decisions recorded here and in Source Registry are the
current reference.

The following are specifically not fallback routes:

| Primary variable | Forbidden or cross-check source | Reason |
|---|---|---|
| Equity price/cap/canonical universe | KB current snapshot; current data.go.kr master | Snapshot/reference-date and historical-universe semantics differ. |
| Market investor flow | Legacy pykrx ↔ Toss segments; KB snapshot | The bridge preserves provider boundaries; neither segment fills the other. |
| Foreign ownership | LS t1716 | Retained same-date values differ; no mixing. |
| Program trading | KRX [12012], KB IVSA0070, Toss per-symbol | KRX is a unit/method reference; KB dates unresolved; Toss full-market historical aggregation is unreliable and forbidden. |
| Short selling/lending | Toss per-symbol | Duplicate/current-universe unsafe source-observation routes. |
| Korean Treasury | BOK ECOS ↔ Toss | Values remain provider-specific; they are cross-checks, not replacements. |
| KOSPI200 derivatives | data.go.kr inputs ↔ bridge; LS samples | Contract/session/provider boundaries and pre-2010 rights remain distinct. |
| Global indices/rates/FX | KB snapshots | Current snapshot date semantics do not replace retained Yahoo/FRED history. |
| Commodity continuous futures | any CFTC COT or contract series | Vendor continuous price construction is a distinct variable. |
| CFTC COT report families | Legacy ↔ TFF/Disaggregated; futures-only ↔ futures-and-options | Participant categories and report types must never be converted or merged. |
| U.S. OHLCV | Tiingo, Stooq, Yahoo versus Sharadar candidate | Delist/universe, identifier, entitlement, and PIT guarantees do not meet the same standard. |
| VIX / Cboe P/C | any alternate page/archive | Cboe retention license and PIT evidence are blocked. |
| FINRA daily short volume ↔ short interest | each other | They are different economic variables and different publication grains. |
| SEC fundamentals | companyfacts/FSDS versus filings | Source-family revision/context semantics differ; no canonical metric policy exists. |

## Emergency use

`EMERGENCY_FALLBACK` may be added only by an explicit review that records all
gate evidence and changes the relevant registry row from `NO` to `YES`.
Emergency means a documented operational need; it never waives license,
universe, session, or PIT requirements. If the primary is unavailable but a
candidate is only a cross-check, publish a gap/status instead.

## Required evidence for a future approval

An approval packet must identify: primary source and failed operation; proposed
fallback and exact endpoint; variable/schema/key mapping; at least multi-date
field comparison; unit/multiplier proof; market/session/date/PIT comparison;
historical universe and delisted analysis; license terms; raw provenance and
schema test plan; rollback/no-overwrite plan; and the authorized runbook.

The policy deliberately treats the latest LS t1633 result narrowly: its units
are `CONFIRMED_EMPIRICAL_MULTI_DATE`, but KRX [12012] remains an
`OFFICIAL_REFERENCE`, not a replacement LS time series. The confirmation does
not authorize KRX-to-LS substitution or Normalized/Backtest promotion.
