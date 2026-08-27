# Toss Read-Only Account Snapshot

## Status

`ACTIVE_READONLY / DAILY_SCHEDULER_INSTALLED / NATURAL_OCCURRENCE_TERMINAL_SUCCESS`

The current desktop implementation refreshes the user's own Toss account
balance and held assets after an Account-page click; startup reads the validated
local snapshot with provider calls 0. Standing authorization also permits an
off-GUI-thread periodic/application or OS-scheduled read-only refresh. The
tested daily route is installed for 07:00 KST. It does not
authorize another person's account or any order/correction/cancel/transfer/
withdrawal operation.

Data Status selects this exact read-only route. External execution still
requires runtime-provided credentials/account selection. Agents must not open
`.env` or print configuration values to determine whether that gate is satisfied.

## Official contract

Source of truth: Toss Securities OpenAPI `1.2.14`,
`https://openapi.tossinvest.com/openapi-docs/latest/openapi.json`.

- `GET /api/v1/accounts`: read-only account discovery, rate group `ACCOUNT`,
  maximum 1 TPS. The returned account number is never persisted. The
  `accountSeq` selector stays in runtime memory only.
- `GET /api/v1/holdings`: read-only KR/US stock holdings, rate group `ASSET`,
  maximum 5 TPS. It requires `X-Tossinvest-Account` in memory.
- `GET /api/v1/buying-power`: read-only cash buying power, rate group
  `ORDER_INFO`, maximum 5 TPS. The desktop route requests the exact `KRW` and
  `USD` currencies separately and requires both responses before promotion.
- The holdings contract includes currency-separated purchase amount, market
  value, after-cost value, unrealized P/L, daily P/L, position quantity/current
  price/average purchase price, commission, and tax.
- Provider `rate`, `rateAfterCost`, and daily `rate` values are decimal ratios
  (`0.1077 = 10.77%`). The local sanitized snapshot retains the provider-native
  ratio; the Account GUI converts it once to percentage points before adding a
  percent sign. KB and manual-account GUI projections already use percentage
  points, so the display contract is consistent across sources.
- Holdings does not include cash or realized P/L. The separate official
  buying-power response supplies only `cashBuyingPower`; it is not relabelled
  as settled cash balance or deposit balance. Realized P/L, overseas options,
  and bonds remain unavailable.
- KRW and USD are retained as separate currency buckets. No implicit FX merge
  or total-assets number is created.

## Runtime and persistence

Implementation:

- provider allowlist: `providers/tossinvest/client.py`
- identifier-free normalization: `providers/tossinvest/account.py`
- schema constants: `contracts/toss_account_snapshot.py`
- refresh/transaction coordinator: `orchestration/toss_account_snapshot.py`
- runtime and daily occurrence coordinator: `orchestration/toss_account_runtime.py`
- supported scheduler CLI: `scripts/maintenance/run_toss_account_snapshot.py`
- local GUI projection: `gui/account_snapshot_service.py`
- GUI background worker: `gui/main_window.py`

Credentials, token responses, authorization headers, account numbers,
`accountSeq`, and registered-person identity remain in memory and must never be
logged or persisted. A successful response is first validated and reconciled;
only the sanitized contract projection may enter:

- `data/landing/tossinvest/account_snapshot/*.json`
- `data/normalized/toss_account_snapshot/latest.json`
- `data/state/toss_account_snapshot.json`
- identifier-free transaction journals under
  `data/state/transactions/toss_account_snapshot/`
- one identifier-free daily occurrence receipt under
  `data/state/toss_account_snapshot_occurrences/YYYY-MM-DD.json`
- the sanitized scheduler last-result projection at
  `artifacts/scheduler_logs/STOCK_DATA_TOSS_ACCOUNT_DAILY_last.json`

Landing here is intentionally a sanitized immutable contract projection, not a
lossless sensitive provider response. Landing, Normalized, and state promote as
one rollback unit. An interrupted journal is recovered before the next refresh.
Any network, auth, token-expiry, ambiguous-account, partial-schema,
cross-currency, summary-reconciliation, or promotion failure preserves the last
valid snapshot.

## Refresh policy

- Token and account selector are cached only in the live process.
- The desktop button and scheduled route reuse the same process-shared
  single-flight/coalescing boundary before provider access. A concurrent second
  refresher returns immediately with API 0 and `NOOP_CONCURRENT_REFRESH`; it
  never waits for a failed first refresher and then issues another account cycle.
- Startup performs only the local validated snapshot read. The desktop still
  has no account timer; Windows Task Scheduler owns the single daily 07:00 KST
  occurrence through `STOCK_DATA_TOSS_ACCOUNT_DAILY`.
- Account discovery is cached after the first successful selection. Multiple
  brokerage accounts fail closed unless an explicit runtime-only selector is
  supplied.
- No order endpoint or GUI-thread network work is permitted. The scheduled
  operation performs no retry or pagination;
  account pagination or discovery still requires the verified one-account
  contract and must not persist identifiers.
- The desktop entry point reads exactly `TOSSINVEST_CLIENT_ID`,
  `TOSSINVEST_CLIENT_SECRET`, and `TOSSINVEST_ACCOUNT_SEQ` from the process
  environment or the project-root `.env`, with process values taking
  precedence. Only these three names are read; values are never printed,
  persisted in snapshots, or passed
  to the provider client's dotenv loader. A runtime base-URL override is not
  accepted.
- The default application injects the background refresher only when the three
  required values are present and the selector is a positive integer. Missing
  or invalid configuration constructs no client, performs API 0, and leaves
  Toss `NOT_AVAILABLE`.
- Complete runtime configuration enables the current manual-click refresh and
  tested scheduled trigger. One logical cycle may issue one OAuth
  if needed plus one holdings call and two currency-specific buying-power calls,
  with finite transient retry/backoff, without an account-discovery
  call. Lower-level bounded operations retain the separate
  one-account discovery contract when expressly invoked outside this app path.
- The daily CLI creates its date-keyed claim with exclusive-create semantics
  before client construction. Every later execution for a terminal occurrence
  is API zero. A caught in-process `BaseException` restores and reads back the
  exact pre-refresh projection before an identifier-free terminal failure
  receipt is written. If exact restoration or readback cannot be verified, it
  raises and replaces the claim with an identifier-free `RECOVERY_REQUIRED`
  receipt; the supported scheduler CLI atomically copies that exact strict
  receipt to its last-result projection and returns `RECOVERY_REQUIRED` with
  unknown (`null`) counts. Replay remains API zero but returns that explicit
  status rather than silently treating it as a completed occurrence. Because no refresher result
  was observed, its token/account counts are `null` (unknown), never guessed as
  a successful call budget. If state, Normalized, Landing, and the successful
  transaction journal all bind one post-refresh digest, the receipt records
  `SCHEDULE_INTERRUPTED_AFTER_COMMIT` while still rolling it back; otherwise it
  records the generic internal-failure reason. Terminal receipts contain only operation,
  clocks, call counts, sanitized paths/digests, status, and bounded reason.
- If another refresh or privacy operation owns the lifecycle lease, a claimed
  daily occurrence returns immediately with API 0 and a terminal
  `SCHEDULE_CONCURRENT_REFRESH` outcome; it does not construct a client,
  inspect a token, or restore/reconcile another occurrence's projection.
- The installed task has one daily 07:00 trigger, `StartWhenAvailable=true`,
  `IgnoreNew`, `PT5M`, battery-start support, and wake-to-run. Exact action,
  working directory, trigger, and settings are read back after registration.
- A missing or invalid local snapshot replaces the account view with
  `NOT_AVAILABLE`. It never leaves the previous number displayed as current.
- The Toss response is shown as separate KRW/USD valuation subtotals and
  currency-specific cash buying power. Cash balance, deposit balance, realized
  P/L, and a cross-currency total remain `N/A`.

## Read-only call safety gate

The first real read must not run until all of the following are true:

1. `DATA_STATUS` records this exact operation route (satisfied 2026-08-20); this
   is routing evidence, not a fresh activation requirement.
2. The three required named process variables above are present, including the
   explicit runtime-only account selector, without an agent inspecting `.env`.
3. The caller runs outside the GUI main thread.
4. The desktop logical call budget is one OAuth issuance if needed, one
   holdings call, and two buying-power calls; a finite transient retry/backoff
   may be added without changing those identities. The generic
   bounded operation may instead make at
   most one account-list call only when it was expressly invoked without the
   stricter desktop selector requirement.
5. Evidence records only status, counts, digests, sanitized paths, and rate
   groups—never account values or identifiers.

## Current operation evidence

Provider-free runtime, occurrence, failure-preservation, crash, replay,
valid-empty, coalescing, CLI-redaction, and scheduler dry-run tests pass. The
task definition installed and read back as `Ready` with the exact 07:00 KST,
daily, `IgnoreNew`, `PT5M`, `StartWhenAvailable`, and wake settings.

The bounded 2026-08-26 command-line occurrence was claimed before access and
ended `TERMINAL_FAILURE / FAILED_PRESERVED_PRIOR` after OAuth attempts 1 and
account calls 0. The prior valid 2026-08-25 snapshot remains unchanged. The
same occurrence is intentionally no-repeat/API-zero; no diagnostic retry or
identifier discovery is allowed. The natural 2026-08-27 07:00 KST occurrence
ended strict `TERMINAL_SUCCESS / SUCCEEDED` with token calls 1 and account
calls 3. Its scheduler last-result receipt is byte-identical to the date-keyed
occurrence receipt, and its `normalized_sha256` matches the current Normalized
snapshot. Observe the next date-keyed scheduled occurrence for the same
identifier-free terminal-receipt, digest-binding, and call-budget checks.
