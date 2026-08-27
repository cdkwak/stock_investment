# Enrich the 아빠-tab holdings basis with provider-labelled current prices and derived Account metrics

## Problem
The accepted dated 아빠 holdings basis has no source-labelled current-price cache, so derived valuation cannot update without risking source mixing or mutation of acquisition facts.

## Evidence
33A4 is independently accepted and preserves exact manual basis rows; current price fields remain absent. Prior user authorization names Yahoo/FDR, but current Data Status does not authorize broad ETF live execution, so implementation and tests must remain supplier-injected/API-zero until a separate bounded live route is authorized.

## Scope
allow:
- Add the isolated manual-account market-value/cache service and source-neutral mapping needed by the accepted 33A4 contract; use injected suppliers and explicit symbol maps; add focused offline tests.

deny:
- Live Yahoo/FDR calls, generic ticker guessing, provider averaging/fallback, cross-currency totals, changes to acquisition facts, credentials/private identifiers, GUI-thread networking, scheduler/data/backtest writes, unrelated files.

## Done When
A strict supplier-injected service resolves only explicitly mapped exchange-qualified symbols, validates aware as-of/captured-at and currency, preserves every acquisition field, derives per-section currency-safe market value/weight/return/unrealized PnL only from accepted prices, marks unsupported or failed symbols unavailable, and atomically retains the prior valid sanitized cache on any rejected refresh. No GUI thread network call or live provider execution is added.

## Verify
Use synthetic manual basis plus injected accepted/unsupported/failing price results; assert acquisition rows are byte-equivalent, exact derived arithmetic and section/currency denominators, explicit provider/as-of labels, no symbol guessing, prior-cache preservation, and zero network dependency. Run owning GUI service tests and diff check.
