# Add a Korean government-bond yield curve to the Dashboard

## Problem
The Dashboard has no Korean sovereign-yield levels or curve, and it may not infer them from futures prices or use the existing 817Y002 source observations until the semantic and Data finality gates are accepted.

## Evidence
GUI Status keeps Korean sovereign rows numeric-free; BB30 defines future exact yield/curve semantics; 744E owns the remaining BOK ECOS 817Y002 publication/finality observation and Data route. U.S. rate rows do not supply Korean tenor identities.

## Scope
allow:
- After prerequisite reviews, add only the thin local six-tenor consumer and compact GUI rendering in the listed files, existing tests, and GUI_STATUS.

deny:
- No provider call, Data contract/collection/promotion, futures-price-to-yield inference, interpolation, mixed-date curve, GUI-side source semantics, prediction/recommendation/order, or unrelated layout redesign.

## Done When
After all semantic, Data and GUI prerequisites are accepted, a thin local GUI service reads only the Data-owned accepted six-tenor Korean Treasury projection for one exact common provider date; validates 2Y/3Y/5Y/10Y/20Y/30Y identity, percent units, finality/state row-count/date/hash and freshness; exposes typed VALUE/STALE/INCOMPATIBLE/UNAVAILABLE without calculating from futures; Dashboard renders a compact ordered curve and exact as-of/source/finality details without clipping; partial, duplicate, mixed-date, stale, malformed or unknown-finality input is numeric-free and independent U.S. rows remain intact; GUI makes no provider/Data write.

## Verify
Add service tests for exact six-tenor success, order/units/date/state/hash, missing/duplicate/mixed/stale/finality failures and API-zero/no-write behavior; add widget tests for curve labels, tooltip/accessibility, numeric suppression, U.S. row preservation and density; run full owning suites plus provider-disabled 1600x900 offscreen/native smoke and clean quiescence.
