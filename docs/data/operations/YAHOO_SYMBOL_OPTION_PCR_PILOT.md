# Yahoo per-symbol option volume P/C — completed route-limited pilot

Status: `TESTED_ROUTE_UNAUTHORIZED / NUMERIC_DATA_NOT_ACCEPTED / RETRY_ZERO / NO_REPEAT`

This runbook was selected by Data Status for the one UR-094 attempt only. It is
now a completed route-validation record and authorizes no second attempt. It inherits the
accepted UR-073 API-zero parser, per-symbol volume P/C contract, GUI separation,
and numeric-free failure behavior. It does not authorize a second expiry,
conditional-symbol calls, automation, canonical history, or Backtest use.

Yahoo is an unofficial Dashboard/research provider. The endpoint is not treated
as a documented public Yahoo Finance API or an exchange/OCC source. A generic
Finance webpage, browser cookie, copied crumb, TradingView, and Investing.com are
not evidence and are not fallback routes.

The HTTP 401 result below is specific to the exact unauthenticated request path
and access mode tested by this runbook. It does not establish that Yahoo Finance
lacks web option-chain functionality, and it does not evaluate or authorize any
other Yahoo access method.

## Exact selected scope and call budget

| Phase | Symbols | Exact expiry | Business calls | Rule |
|---|---|---|---:|---|
| Initial live evidence | `SPY`, `QQQ`, `IWM`, `TLT`, `SOXX`, `SOXL`, `TQQQ` | `2026-09-18T00:00:00Z` (`date=1789689600`) | maximum 7, one per symbol | serial, one attempt, retry zero |
| Conditional | `EWY`, `KORU`, `QLD` | none selected | 0 | not called in this operation; a separate reviewed extension requires initial-source viability plus exact per-symbol standard-chain/liquidity evidence |
| Price/ADR-only hard gate | `DRAM`, `SKHY` | none | 0 | option endpoint access forbidden by this runbook |

The request pattern is exactly
`https://query1.finance.yahoo.com/v7/finance/options/{SYMBOL}` with the single
query parameter `date=1789689600`, a 20-second timeout, and no retry,
pagination, alternate host, crumb/cookie bootstrap, browser fallback, or second
expiry. Calls run in the table order. The selected operation window is
2026-08-20 19:30 through 22:29 KST, before the 2026-08-20 XNYS regular open;
the latest completed XNYS session is fixed to 2026-08-19. Outside this window
the command stops at API zero.

Global transport stop conditions are HTTP 401/403/429, redirect to a consent or
HTML page, timeout, non-JSON content, or an invalid `optionChain` root. Retain
that attempted response metadata/body first, reconcile the consumed call, then
stop before all later symbols. A symbol-local empty chain, wrong expiry,
nonstandard-only set, missing volume, or insufficient two-sided volume blocks
that symbol but may proceed to the next selected initial symbol.

## Completion checkpoint

At 2026-08-20 20:19:05 KST the ordered operation called only `SPY`. The exact
response and secret-free request metadata were retained before inspection. The
response was HTTP 401 with JSON content, which triggered the reviewed global
stop. The reconciled live ledger is 1/7 calls, zero retries, with `QQQ`, `IWM`,
`TLT`, `SOXX`, `SOXL`, and `TQQQ` not attempted. Conditional `EWY`, `KORU`, and
`QLD`, plus price/ADR-only `DRAM` and `SKHY`, also have zero calls.

The retained SPY body SHA-256 is
`d91e2dc406dd9704af1981d5a6669ccdf8855e1a1eb6f69ceb0cb6d2bcf2beac`.
Immediate retained replay verified that hash and reproduced
`GLOBAL_TRANSPORT_STOP / HTTP_RESTRICTION:401` with API calls 0, retries 0, and
no mismatch. No standard-chain, expiry, multiplier, two-sided-volume, or
completed-session freshness evidence was accepted from this tested route for
any symbol. This is not a general Yahoo option-support conclusion. All numeric
option P/C outputs therefore remain suppressed.

- Landing:
  `data/landing/yahoo_symbol_option_chain/20260820T111905.538768Z_9dc6c566c0fd467d9327bc98b005b47e/`
- Checkpoint:
  `artifacts/agent_runs/ur094_yahoo_symbol_option_live_evidence_20260820T201906+0900.json`
- Mutations: Landing and the checkpoint only; Normalized, Published, canonical,
  Dashboard runtime, Backtest, scheduler, cookies, and alternate hosts remained
  untouched.

## Independent standard-contract evidence

OCC states that each standard ETF option contract represents 100 shares and
that corporate actions can create adjusted contracts with different
deliverables. The fixed initial identities are independently confirmed as ETFs
by their issuer pages:

- OCC: [ETF option unit of trade and adjusted-contract exception](https://www.theocc.com/clearance-and-settlement/clearing/etf-options)
- `SPY`: [State Street SPDR S&P 500 ETF Trust](https://www.ssga.com/us/en/individual/etfs/state-street-spdr-sp-500-etf-trust-spy)
- `QQQ`: [Invesco QQQ ETF](https://www.invesco.com/qqq-etf/en/home.html)
- `IWM`: [iShares Russell 2000 ETF](https://www.ishares.com/us/products/239710/ishares-russell-2000-etf)
- `TLT`: [iShares 20+ Year Treasury Bond ETF](https://www.ishares.com/us/products/239454/ishares-20-year-treasury-bond-etf)
- `SOXX`: [iShares Semiconductor ETF](https://www.ishares.com/us/products/239705/ishares-semiconductor-etf)
- `SOXL`: [Direxion Daily Semiconductor Bull 3X ETF](https://www.direxion.com/product/daily-semiconductor-bull-bear-3x-etfs)
- `TQQQ`: [ProShares UltraPro QQQ](https://www.proshares.com/our-etfs/leveraged-and-inverse/tqqq)

This establishes only the base standard multiplier for the exact initial ETF
allowlist. A returned contract is eligible only when all of these also hold:

1. Yahoo reports `contractSize=REGULAR`;
2. `contractSymbol` is a valid OSI-style identity whose root equals the exact
   requested symbol, encoded expiry is `260918`, side matches the source list,
   and encoded strike equals the returned strike;
3. the contract identity is unique within the retained expiry;
4. adjusted/alternate roots and every non-`REGULAR` row are excluded and counted.

The OCC rule is not extended to `EWY`, `KORU`, `QLD`, `DRAM`, or `SKHY` in this
operation. No returned Yahoo flag alone is called independent proof.

## Landing-first transaction

The operation ID is `ur094-yahoo-option-20260918-20260820`. Before parsing each
consumed call, atomically retain:

`data/landing/yahoo_symbol_option_chain/<capture_id>/<SYMBOL>/1789689600.response`

and a sibling secret-free metadata JSON containing operation ID, requested
symbol/expiry, request start/end UTC, capture UTC/KST, HTTP status,
content-type, byte count, SHA-256, timeout/retry counters, and the source URL
without copied cookies or headers. A manifest under the capture root records
planned, consumed, not-attempted, and retained scopes. Existing capture roots
are immutable; a failed run never deletes or overwrites prior valid evidence.

No Landing response is Normalized, Canonical, or Published by this pilot. The
only derived output is a review artifact under `artifacts/agent_runs/`, bound to
the Landing hashes.

## Parse, liquidity, and freshness gates

For each retained response:

- require HTTP 200, JSON content and the accepted UR-073 one-result/one-expiry
  schema, exact provider symbol, and exact requested expiry;
- run the accepted parser offline; duplicate contract/expiry identity,
  malformed numeric fields, and cross-symbol content fail closed;
- preserve every source null and require every eligible `REGULAR` contract's
  volume to be present, as the accepted parser contract already requires;
- require positive summed call volume and positive summed put volume; the only
  allowed value is that symbol's `put_volume / call_volume`;
- require at least one eligible call and one eligible put whose
  `lastTradeDate` falls on fixed latest-completed XNYS session 2026-08-19;
- preserve capture UTC/KST, underlying quote time, per-contract last-trade time,
  and latest contract-trade UTC/KST. Last trade is never called publication time;
- require replay time minus capture time to be at most six hours. Older evidence
  is `STALE` and hidden.

There is no cross-symbol sum, average, weighting, fallback, or market-wide P/C.
Open interest is retained only as provider evidence and never substituted for
volume. A failed/empty/malformed/stale/multiplier-unknown/liquidity-insufficient
scope produces a typed reason and cannot replace any prior valid research view.

## API-zero replay and completion

After the live attempt stops or exhausts the seven-call budget:

1. close and reread the manifest and every retained hash;
2. run the same command in retained replay mode with network construction
   disabled and require `api_calls=0`, `retries=0`;
3. require the replay to reproduce per-symbol status, counts, timestamps,
   reasons, and ratio bytes from the same Landing hashes;
4. write one checkpoint that reconciles planned/consumed/not-attempted scopes,
   failures, retry zero, API-zero replay, and all forbidden mutations;
5. leave scheduler/runtime registry, Dashboard canonical source map, Normalized,
   Published, canonical history, and Backtest unchanged.

A successful live response proves only one as-retrieved research snapshot for
one expiry. It does not establish endpoint stability, redistribution rights,
publication finality, historical completeness, or automation eligibility.
