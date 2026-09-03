# KB Securities Read-Only Account Snapshot

## Status

`ACTIVE_READONLY / LIVE_VALIDATED_20260826 / AUTONOMOUS_REFRESH_IMPLEMENTATION_ALLOWED`

This is the current authority for the domestic KB Securities account snapshot
operation. Standing user/Data authorization permits bounded read-only manual,
periodic, and scheduled cycles after the offline gates pass. The current desktop
implementation is manual; agents may add finite transient retry/backoff and a
single-flight scheduler without fresh approval. Redirects, unverified
pagination/fallback, identifier persistence, and every financial mutation
remain disabled.

The official sample and workbook are retained in Git commit `f9cee29` as
`docs/kbsec/official/samples/SSQM2952.json` (blob
`9f4d1e051daee2d9551c6cf88e3fba4df3cdf2d4`) and
`docs/kbsec/official/current_b2c_all_api.xlsx`. The official workbook identifies
`REST (POST)` and exact path `/api/v1/ssqm2952`; its input table contains only
`excg_mktpr_ccd` (`A`, `K`, or `N`) and identifies App Key/Token authorization.
There is no account-number/header/body selector. Therefore this application
requests exact value `A` and treats the account as the one already bound to the
App Key/Token permission; it never discovers, guesses, transmits, or persists a
separate account selector.

## Evidence matrix

| Claim | Current evidence | Decision |
|---|---|---|
| Operation identity and successful process code | Git-retained official `SSQM2952.json`; archived connection result | Verified contract evidence; archive is not execution authority |
| Request envelope and `excg_mktpr_ccd` body key | Git-retained official sample input | Verified |
| Domestic summaries, `Record1`, and `processTime` | Git-retained official sample output | Verified |
| Identifier-free projection and exact reconciliation | Current strict normalizer and owning tests | Implemented offline |
| Generic envelope, token cache, redaction, `(3.05, 10.0)` timeout, redirect-zero, and ambient-environment isolation | Current generic KB client | Active for the exact manual account route only; production session `trust_env=false` |
| Exact SSQM2952 URL and HTTP method | Official retained workbook: `REST (POST)`, `/api/v1/ssqm2952` | Verified exact allowlist |
| Runtime account-selection mechanism | Official input has only `excg_mktpr_ccd`; authorization is App Key/Token | No runtime account selector exists or is persisted |
| Exact desktop account environment names | Existing generic client contract | `KBSEC_BASE_URL`, `KBSEC_APP_KEY`, `KBSEC_APP_SECRET` only |
| KB account client operation | Exact body `{"excg_mktpr_ccd":"A"}`, process code `0011` | Implemented and offline validated |
| Sanitized snapshot coordinator | Current injected-supplier implementation, tests, and 2026-08-26 bounded result | Live validated; one business call, sanitized atomic projection retained |

## Verified contract boundary

The retained official request sample proves only this envelope shape:

- `dataHeader` contains exactly `ipAddr` and `macAddr` placeholders.
- `dataBody` contains the exact key `excg_mktpr_ccd`.
- No account number or account selector appears in the retained sample.

The successful response envelope requires `dataHeader.resultCode=200`,
`dataHeader.processCode=0011`, and a 17-digit `processTime`. The current strict
identifier-free projection in
[`providers/kbsec/account.py`](../../../src/stock_data/providers/kbsec/account.py)
accepts these verified domestic fields:

- body counts: `grid_cnt1`, `tl_data_cnt`;
- summaries: `nt_asts_val_amt`, `scrts_nt_val_amt`, `byng_amt_sum`,
  `val_amt_sum`, `val_pl_sum`;
- positions in `Record1`: `is_cd`, `is_nm`, `clsf`, `ec_q_p6`,
  `ordr_psbl_q_p6`, `byng_avr_prc`, `now_prc`, `byng_amt`, `val_amt`, and
  `val_pl`.

All numeric provider fields must be finite decimal strings. Both count fields
must equal the exact number of position rows, position identity
`is_cd|clsf` must be unique, quantities must be non-negative, and purchase,
valuation, and unrealized-P/L row sums must exactly equal their corresponding
summary fields. Partial or duplicate rows fail closed.

A valid-empty result has `grid_cnt1=0`, `tl_data_cnt=0`, `Record1=[]`, and zero
`byng_amt_sum`, `val_amt_sum`, and `val_pl_sum`. Provider total-assets and
securities summaries remain separately typed values; their difference is never
invented as cash. Empty is not an error and is never replaced with a prior
position list.

The sanitized schema is owned by
[`contracts/kbsec_account_snapshot.py`](../../../src/stock_data/contracts/kbsec_account_snapshot.py).
It is domestic KRW only. Cash balance, buying power, realized P/L, and overseas
positions remain `N/A`; no FX conversion or cross-provider total is allowed.

## Runtime and persistence boundary

The existing generic KB client in
[`providers/kbsec/client.py`](../../../src/stock_data/providers/kbsec/client.py)
uses a 3.05-second connect timeout and 10-second read timeout, sets
`allow_redirects=false` on token and business POSTs, disables ambient Requests
environment/`.netrc`/proxy inheritance with `trust_env=false` on the production
session, and redacts known credentials, tokens, bearer headers, and bounded
response diagnostics. That transport behavior is reused by the exact SSQM2952
account operation.

The logical operation ceiling is one OAuth issuance when no valid in-memory
token exists plus one SSQM2952 business request. A finite transient retry/backoff
may be implemented without changing that identity; redirect, unverified
pagination, and fallback remain zero.

Only the identifier-free normalized projection may ever be persisted. The
offline coordinator in
[`orchestration/kb_account_snapshot.py`](../../../src/stock_data/orchestration/kb_account_snapshot.py)
owns these paths:

- sanitized immutable Landing under `data/landing/kbsec/account_snapshot/`;
- latest local projection at `data/local/account_snapshots/kb_self.json`;
- one latest-wins file per KST day under
  `data/local/account_positions_history/kb_self/YYYY-MM-DD.json`;
- a value-free receipt at `data/state/kbsec_account_snapshot.json` and
  identifier-free transaction journals under
  `data/state/transactions/kbsec_account_snapshot/`.

The coordinator has no URL, credential, environment, or generic KB client
dependency. Its response supplier is injected, invoked at most once while the
KB transaction lock is held, and is covered only by API-zero tests. It
normalizes before writing, atomically promotes sanitized Landing, local latest,
and a value-free state receipt, and rolls back or recovers interrupted promotion
without replacing the last valid snapshot. A valid unfinished promotion journal
is a fail-closed barrier: if its retained backup cannot yet be restored, no newer
supplier call or promotion may begin. Corrupt journals and paths outside
the exact KB-owned roots authorize no recovery mutation.

Tokens, credentials, account identifiers, registered person identity, raw full
responses, request headers, private diagnostics, environment values, and
exception text must remain in memory and must never enter any retained file or
log. `orchestration/kbsec_account_runtime.py` loads only the three named values
injected by `app.py`; construction and desktop startup make provider calls 0.
The Account-page button currently supplies `MANUAL` off the GUI thread. A tested
scheduled trigger may use the same runtime/coordinator boundary and must remain
single-flight and read-only.

The daily positions history is written atomically inside the same account
lifecycle lease immediately after a successful promotion. A later successful
run on the same KST day replaces that day's file; a failed refresh writes no
history. Its top level is only `schema_version`, `source_id`, `observed_at`, and
`positions`; each position is only `symbol`, security `name`, `currency`,
`classification`, `quantity`, and `average_purchase_price`. It contains no
cash, balances, totals, account identifiers, current prices, market values,
purchase amounts, or P&L fields. The existing user privacy removal action
deletes this history. Existing retained sanitized Landing can be backfilled
provider-free with `scripts/maintenance/backfill_positions_history.py`.

## Active read-only call gate

Each bounded manual or scheduled call may run only while all items remain true:

1. Exact `POST /api/v1/ssqm2952` and body
   `{"excg_mktpr_ccd":"A"}` remain unchanged.
2. Account selection remains solely the App Key/Token permission boundary; no
   runtime selector, discovery call, or persisted identifier is allowed.
3. Only `KBSEC_BASE_URL`, `KBSEC_APP_KEY`, and `KBSEC_APP_SECRET` may be loaded.
4. Offline client/runtime/GUI validation must pass before the call. The current
   provider/runtime/coordinator plus full owning GUI run passes 264 tests with
   one intentional skip, including exact redirect-zero, production-session
   ambient-environment isolation, coalescing, and clean-close regressions.
   Account-thread retirement is identity-bound and rechecks transient Qt
   `isRunning()` state before the stopped event-loop boundary; a deterministic
   adversarial regression and ten fresh-process race groups (50 checks) finish
   with zero running QThreads.
5. Data Status records the bounded read-only route; it is not a fresh approval
   requirement for each cycle.
6. Any GUI worker or scheduled runner must remain off the GUI thread,
   single-flight/coalesced, and unable to reach a mutation endpoint.

The 2026-08-26 15:02 KST bounded validation completed `SUCCEEDED` with one
SSQM2952 supplier call and a sanitized snapshot path. Offline readback verified
schema version 1, provider `kbsec_open_api`, operation `SSQM2952`, and zero
forbidden account/authentication key names. No response values, positions,
credentials, token, or account identifier were emitted to the operation log.

Orders, corrections, cancellations, transfers, withdrawals, account mutation,
and automated trading remain prohibited. Read-only scheduler registration is
authorized after the call-safety and privacy tests above pass.
