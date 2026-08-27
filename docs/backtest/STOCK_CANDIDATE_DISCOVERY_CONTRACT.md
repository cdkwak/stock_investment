# Stock Candidate Discovery Contract

Contract version: `stock-candidate-research/v1`

Status: `INTERFACE_IMPLEMENTED / PRODUCTION_INPUT_UNAVAILABLE`

## Purpose and boundary

This contract supports the P4 screen for explanatory research candidates that
are simultaneously oversold, revising earnings upward, and relatively
undervalued. A candidate is not a recommendation, suitability decision, target
price, order intent, ranking, or authorization to trade.

This is the strict combined-validation layer, not the permissive daily scanner.
The GUI may display an independently available technical axis under
[`stock-exploratory-scanner/v1`](../gui/STOCK_EXPLORATORY_SCANNER_CONTRACT.md)
without claiming that all three axes or historical PIT validation are complete.

The projector is pure and provider-free. It never fetches data, derives factor
values, searches thresholds, ranks instruments, or fills an absent axis with a
neutral state. Production candidates remain unavailable until the Forward EPS,
revision and Forward ROE source in the P1 contract is licensed and PIT-validated.

## Conjunctive evidence rule

Every instrument must bind an accepted historical-universe dataset, version and
digest and carry three independent evidence roles at one exact, timezone-aware
`decision_at`:

1. a stock-scoped, split/corporate-action-safe oversold axis;
2. a same-scope, same-vintage Forward EPS revision axis; and
3. market, sector and own-history relative-value evidence with financial-health
   and value-trap checks inside its versioned definition.

Each axis state is exactly `MATCH`, `NO_MATCH`, `INSUFFICIENT_HISTORY`,
`UNAVAILABLE`, `PIT_BLOCKED`, or `INVALID`. Every role preserves an evidence ID,
typed reason, source dataset/contract/version and digest, definition ID, unit,
observation date, publication/retrieval/availability/usable clocks, PIT status
and freshness. Clocks must satisfy publication <= retrieval <= availability <=
usable <= decision. A complete axis requires `PIT_SAFE_AS_OF_DECISION` and
`CURRENT_AT_DECISION`. Any incomplete axis withholds the entire current candidate
view rather than becoming zero or neutral.

The engine preserves only a deterministic `(market, symbol)` presentation order.
It calculates no score, rank, winner, expected return or portfolio weight.
Excluded instruments contribute typed reason counts; unavailable production
inputs produce an empty, numeric-free view.

## Required upstream contracts

- PIT-safe membership including delisted and later-merged instruments;
- a pre-registered oversold definition with split/dividend adjustment and exact
  close/decision timing;
- Forward EPS and revision vintages with provider publication/availability time;
- valuation semantics matched across stock, sector, market and own history;
- financial-health and value-trap definitions with report-period, currency,
  unit, filing-publication time and revision lineage.

Current trailing/current KRX market PER/PBR never substitutes for stock-level
forward valuation or earnings revisions. A sector state never fills a missing
instrument state. The GUI consumes only the typed view and performs no provider
call or persistent write.

## Validation

Provider-free tests must prove:

- only the fixed three-axis conjunction emits a strict research candidate;
- missing/limited/PIT-unsafe, future-available and mixed-decision evidence is
  rejected before display;
- duplicates are rejected and output ordering is deterministic;
- empty production inputs are typed `UNAVAILABLE`, numeric-free and contain no
  recommendation, ranking or order state; and
- GUI transitions clear prior candidate rows when an unavailable view arrives.
