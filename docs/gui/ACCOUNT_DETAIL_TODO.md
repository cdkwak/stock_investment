# Account Detail P5 Todo

## Outcome and grain

The Account page is an identifier-free, read-only view over one validated local
snapshot per source. Holdings remain at `(source, currency, symbol)` grain.
Currency summaries remain separate; no implicit FX conversion or cross-currency
total is allowed. Unsupported provider fields stay `N/A`, not zero.

## Current inventory

| ID | Work item | State | Evidence / next boundary |
|---|---|---|---|
| P5-01 | Source selector, consolidated/individual scope, unavailable-source isolation | DONE | Individual source selection restores valid values without weakening the incomplete consolidated gate. |
| P5-02 | Shoulder-surfing privacy, stale-widget scrubbing, exact local snapshot removal | DONE | Amount/symbol hiding clears charts, table content, tooltips, accessibility metadata, and detached widgets. |
| P5-03 | Currency-safe totals, allocation, actual observation history | DONE | KRW/USD remain separate; incomplete denominators suppress allocation; fewer than two real observations suppress history. |
| P5-04 | Use every verified provider holding field in detail UI | DONE | Table shows average cost, current price, purchase/market value, return, unrealized P/L, after-cost P/L, daily P/L, weight, source and reference time. Tooltip exposes orderable quantity, provider/finality, after-cost/daily return, commission and tax when supported. |
| P5-05 | Currency-summary after-cost and daily P/L context | DONE | Headline metadata shows each supported currency independently; missing or partial fields remain `N/A`. |
| P5-06 | Per-source freshness action panel | DONE | Each source shows last accepted time, freshness/reason, read-only refresh capability, latest allowlisted outcome and next scheduled/manual eligibility without identifiers or values. |
| P5-07 | Longitudinal account-value observations | DONE | User requested this on 2026-08-27. Accepted snapshots now atomically retain identifier-free source/currency observations; KB exact total assets, Toss observable component sum, and legacy securities-only history remain distinctly labelled. Privacy removal deletes the history. |
| P5-08 | Manual/family current-price automation | CONTRACT_REQUIRED | Current cache is API-zero/local-only. A live provider symbol map, terms review, Landing capture and scheduler contract must precede activation. |
| P5-09 | Account-to-net-worth linkage | DEFERRED | Keep account values and user-declared net-worth assets separate until an explicit economic-claim identity and double-count prevention contract exists. |
| P5-10 | Export/reporting | NOT_AUTHORIZED | No account export exists. Any future export must preserve masking, source/as-of, currency separation and explicit user action. |

## Stable quality checks

- Exact source schema and identifier-free position text.
- Unique provider position identity and currency membership.
- Summary-to-position reconciliation before display.
- Valid finite prices, quantities, P/L and rates; valid zero is preserved.
- Source/as-of/finality shown for current price evidence.
- No cross-currency sum, inferred cash, realized P/L, or unsupported field.
- Hide/unavailable transitions leave no private value in visible or hidden widget
  text, tooltip, accessibility metadata, chart series, or detached child widget.

## Next implementation order

1. Accumulate the second natural observation required for a real account-scale
   line; never fabricate a historical point from the current snapshot.
2. Reassess P5-08 only after its live provider boundary is explicitly activated.
3. Add a flow-adjusted performance series only after a deposit/withdrawal and
   transfer ledger contract exists.
