# UR-098: one-use Yahoo option requests-fallback pilot

## Authorization and boundary

This is the sole active procedure for the user-directed UR-098 rework. It is
an as-retrieved access-method pilot, not an operational Yahoo collector or a
claim about Yahoo Finance option support generally. It supersedes no retained
route result: UR-094's `SPY` query1 HTTP-401 result stays route-limited and
UR-104's `SPY` yfinance/curl_cffi support-flow rate limit stays route-limited.

The user authorized one materially different unauthenticated `requests`
fallback attempt. There is no authorization for a browser, copied state,
alternate host/path, automatic retry, account access, numeric Dashboard display,
automation, Normalized/Derived/Published/canonical promotion, redistribution,
or Backtest use.

## Frozen live scope

| Item | Fixed value |
|---|---|
| Underlying | `QQQ` only; liquid ETF option underlying, not a completed external call in UR-094, and distinct from `SPY`/`AAPL` |
| Transport | Isolated standard Python `requests.Session()` only; no yfinance runtime import, curl_cffi, browser, support/bootstrap request, or POST |
| Route | yfinance 1.6.0 documented query2 option route; query1 is forbidden |
| Call 1 | One unauthenticated GET for the expiration list |
| Call 2 | Only if call 1 is HTTP 200 and validates: one unauthenticated GET for the nearest listed expiry |
| Maximum | Two business GETs; zero support GETs; timeout 10 seconds; retry count zero |
| Stop | First transport error, non-200, malformed/empty root, invalid expiration list, or chain schema failure stops globally; no fallback, host, symbol, date, or repeat |
| Landing | Successful HTTP-200 JSON only: immutable unique capture directory under `data/landing/yahoo_option_requests_fallback/`; atomic response-plus-secret-free metadata commit and readback/hash validation before parsing. Non-200/auth-like bodies and all response headers are neither retained nor summarized. |
| Evidence | Sanitized checkpoint `artifacts/agent_runs/ur098_yahoo_option_requests_fallback.json`; successful capture has API-zero hash/schema replay only |

The runner is
`scripts/manual/pilot/pilot_yahoo_option_requests_fallback.py`. Its fixed
target and budget are not CLI-selectable. The operation does not load `.env` or
any runtime authentication material.

## Completed route result

At `2026-08-20T14:40:34Z`, the one-use operation issued the first `QQQ`
expiration-list request and received the typed result
`TESTED_ROUTE_RATE_LIMITED`. It consumed exactly one of the two permitted
business GETs; support GETs, POSTs, retries, nearest-expiry calls, and all
authentication handling were zero. The non-success response body and response
headers were neither retained nor summarized, so no Landing capture or
API-zero replay exists for this route. The sanitized ledger is
`artifacts/agent_runs/ur098_yahoo_option_requests_fallback.json`.

This completes only the bounded unauthenticated requests-route check. It does
not establish a Yahoo- or yfinance-wide limit, nor that option functionality is
unsupported. The same exact route, `QQQ`, host, and path must not be retried
under UR-098.

## Acceptance checks on a successful two-call capture

The second response must contain exactly one matching `QQQ` root and the exact
nearest expiry from the first response. The schema validator verifies:

- nonempty expiration list and a selected future nearest expiry;
- one populated calls list and one populated puts list;
- every calls/puts row explicitly carries an integral epoch `expirationDate`
  exactly equal to the selected nearest expiry, a positive integer last-trade
  time no later than its retained
  capture time, finite non-negative `strike`, `bid`, `ask`, and
  implied-volatility fields, and non-crossed `bid <= ask`;
- nullable-but-valid non-negative integer `volume` and open-interest fields;
- a present quote currency and exchange timezone.

This verifies only an as-retrieved provider snapshot. No prices, strikes,
volumes, open-interest values, implied volatilities, expiry values, currency
values, or timezone values are displayed or promoted. A schema-valid response
is recorded as `AS_RETRIEVED_SCHEMA_VALID_NUMERIC_DATA_NOT_ACCEPTED` until
separate rights, multiplier, finality, freshness, retention, and Dashboard
authorization are accepted.

Successful capture replay first hash-verifies both response bodies and both
immutable secret-free metadata files. It reads the original
`captured_at_utc` from that verified metadata for expiration and last-trade
validation; it never uses replay wall-clock time. The focused offline test
monkeypatches time beyond the test expiry and still passes at API calls zero,
while metadata tampering fails the readback hash gate. This deterministic path
is not available for the retained rate-limited QQQ result because that run
intentionally retained no response body or Landing metadata.

## Route-limited failures and preservation

The runner records only one typed route result such as
`TESTED_ROUTE_UNAUTHORIZED`, `TESTED_ROUTE_RATE_LIMITED`,
`TESTED_ROUTE_TRANSPORT_FAILURE`, or a named schema gate. It never expresses a
provider-wide or feature-wide unsupported conclusion. Existing valid data is
untouched; the pilot has no mutable normal-data target and never schedules a
retry.

## Static yfinance Auth design — not executable

Static inspection of the retained isolated yfinance 1.6.0 installation and the
user-identified official documentation confirms the `Ticker.options`,
`Ticker.option_chain()`, and `Auth.set_login_cookies()` API surface. That
surface does not authorize an authentication attempt here.

If the user later grants a separate authentication-validation approval, the
design must use a Windows local secure-storage adapter (Credential Manager
through an OS-backed library, with DPAPI-protected local fallback only where
the account/user scope is explicit). The adapter may retrieve opaque material
only in process memory at the final call boundary, pass it directly to the
documented Auth API, then discard references. It must not return it to callers
or store it in application configuration, `.env`, source code, artifacts,
queue records, docs, tests, exceptions, logs, Landing metadata, or GUI state.
No boolean/presence marker for either opaque login component is persisted.

Before production acceptance, the route must record the exact symbol and
budget, retention/redistribution terms, secure-secret implementation, expiry/
chain schema and timing checks, automatic-retry behavior, prior-valid-data
preservation, and Landing redaction rules. Standing Data authorization permits
research calls and implementation with existing credentials; no separate
permission-only approval is required.
Authentication failure, expiry, rate limit, empty response, or schema failure
must preserve prior valid data and stop without an automatic retry.

## Rights and product boundary

Any later accepted as-retrieved option snapshot may be considered only for the
user's personal local Dashboard research view, with source/as-of/freshness and
restriction truth preserved. This is independent of any data redistribution,
bulk retention, derived-product, automation, canonical-history, or Backtest
right; none is granted by this pilot or by the static authentication design.
