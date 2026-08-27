# Family Manual Account Holding Current Prices

Status: `OFFLINE_SUPPLIER_INJECTED_API_ZERO / LIVE_AND_SCHEDULER_DISABLED`

## Scope

This operation enriches only the accepted dated `아빠` manual holdings basis.
It does not own or alter acquisition facts and does not authenticate a family
account. The current queue task permits injected, local price evidence only;
the standing API authorization does not override that narrower task boundary.

The operation accepts a strict manual snapshot dated `2026-02-03`, an explicit
`(section, six-digit ticker) -> (Yahoo exchange-qualified symbol, exchange,
KRW)` map, and one injected supplier result. It never guesses a suffix, averages
providers, falls back to another provider, converts currencies, calls a provider
from the GUI thread, or stores account identifiers.

## Cache contract

The sole supported cache schema is `manual-account-market-values/v1`, represented
by `src/stock_data/contracts/manual_account_market_values.py`. The default local
path is:

`data/local/manual_account_market_values/latest.json`

The cache contains no holding name, quantity, average cost, purchase total,
account identifier, credential, or raw provider response. It binds to the exact
normalized acquisition basis with a SHA-256 digest and retains only:

- section and ticker identity;
- explicit provider symbol, provider, exchange, `KRW_PER_SHARE`, aware provider
  `as_of`, aware `captured_at`, and finality for accepted prices;
- market value, section/currency denominator weight, unrealized P/L and return
  when the acquisition basis permits them; and
- a sanitized numeric-free reason for unsupported or unavailable symbols.

Weights use only accepted prices within the same section and currency. Section
summaries report accepted/total counts and are incomplete when any row is
unavailable. Cross-currency totals do not exist.

The read-only GUI join does not trust retained derived numbers merely because
the cache schema parses. It recomputes `quantity * price`, unrealized P/L,
return, and each section/currency weight from the bound acquisition basis and
rejects any difference. Provider, symbol, exchange, unit and finality are
allowlisted, and clocks must satisfy `as_of <= captured_at <= generated_at`.
The same six-digit holding may appear in both sections and reuse its one exact
Yahoo symbol; one provider symbol may never stand for two different tickers.

## Failure and persistence

`refresh_manual_account_market_values()` invokes the injected supplier at most
once with only explicitly mapped symbols. A missing map or missing/typed-failed
result becomes a numeric-free unavailable row. An exception, unexpected
identity, wrong symbol/exchange/currency/unit, naive or reversed timestamp,
nonpositive price, unrequested result, malformed cache, or atomic write failure
rejects the whole refresh. No rejected refresh replaces the prior valid cache.

Accepted persistence uses same-directory temporary creation, flush/fsync and
atomic replace. The API-zero CLI
`scripts/maintenance/refresh_manual_account_market_values.py` accepts only local
basis, symbol-map and observation fixtures and prints sanitized counts. Its
output is restricted under the default cache directory.

## GUI boundary

The GUI remains provider-free. It may join a parsed cache only when its basis
digest and ordered `(section, ticker)` identities match the revalidated manual
snapshot. Acquisition quantity, average cost and purchase total are copied from
that snapshot without rewriting. Unsupported rows stay numeric-free; incomplete
section aggregates are suppressed. Provider and aware as-of labels come from
the cache.

## Activation gate

No live Yahoo/FDR call and no scheduled task is active. Live activation requires
a later queue scope that explicitly selects the real symbol map and provider
transport, verifies local-use terms and timestamp/session/finality semantics,
preserves the first accepted response in immutable Landing, and satisfies the
standing onboarding runbook. Normal tests remain network-free.
