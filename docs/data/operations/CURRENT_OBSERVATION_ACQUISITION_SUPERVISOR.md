# Current-observation acquisition supervisor

Status: **IMPLEMENTED_TRANSPORT_FREE / STANDING_ROUTE_REGISTRATION_AVAILABLE**

This runbook documents the process-layer guard used by a bounded
current-observation operation. Data Status and the standing autonomous Data
runbook authorize public and existing-credential read-only routes without a
separate user approval. This supervisor does not define a route's identity,
contract, call budget, or promotion eligibility; the caller must bind them.

## Boundary

`CurrentObservationAcquisitionSupervisor` is provider-independent. Its caller
must supply all of the following for every route:

- `operation_id` and this operation/runbook reference;
- one exact `CurrentObservationRoute` identity and native interval;
- a `DueWindow` of exactly 30 or 60 minutes, plus any KST provider window;
- one request cap, timeout, provider-aware bounded retry/backoff policy,
  finality, and the required
  `display_only=True` / `pit_safe=False` flags;
- an injected source adapter. The supervisor never constructs transport,
  credentials, account/order paths, or a GUI object.

The caller-selected `CurrentObservationFileStore` remains the single atomic
observation/circuit/decision boundary. Failed promotion preserves a prior valid
observation; `replay()` reads that boundary with API 0. The process lock fails
closed when another process owns the exact lock path. Startup/manual/timer
ticks coalesce while one supervisor tick is active.

Before an injected adapter is invoked, the supervisor also atomically writes a
separate per-route durable attempt claim. It records the UTC attempt timestamp
and changes `CLAIMED` to `COMPLETED` only after the coordinator returns. The
next process uses this ledger—not in-memory state—for its 30/60-minute due
decision. A retained `CLAIMED` record after interruption is
`ORPHANED_DURABLE_CLAIM_NO_REPEAT`: it is numeric-free and blocks a repeat of
that exact occurrence until an evidence-backed recovery decision, rather than
guessing whether a provider request completed. An agent may make that decision
under standing authorization; it does not require user or Lead approval. When
completion cannot be proved, preserve the exact claim and use a genuinely new
due occurrence and idempotency key. Claim/complete write or readback failure is also
numeric-free before a new adapter call; a post-adapter completion failure keeps
the claim orphaned to prevent a repeat.

## Manifest priority and route-registration rules

The manifest sorts broker candidates according to the retained Source Registry
roles and never averages or silently substitutes values:

1. `Toss` exact provider observation;
2. `KB` current snapshot;
3. `LS` exact provider observation;
4. `FDR` only as an independently registered daily fallback route;
5. `YFINANCE` only as an independently registered route, never an implicit
   fallback.

An inactive route retains the implemented compatibility outcome
`INACTIVE_OR_UNAPPROVED`, where `UNAPPROVED` means no exact route registration
is present; it does not mean user or Lead approval is required. It invokes no
adapter and returns no number. A route outside its KST provider window returns
`PROVIDER_WINDOW_CLOSED`; a route inside the window but before the next 30/60
minute due point returns `CADENCE_NOT_DUE`. Neither condition makes a provider
call or changes retained observations.

The historical manifest snapshot had **zero activated routes**. That snapshot is
not a permission barrier. A current agent may register an exact bounded route
under the standing runbook after binding identity, semantics, cadence, call
budget, failure preservation, and display/PIT eligibility. Its prior entries were:

| Provider | Exact route/identity boundary | Interval | Activation gap |
|---|---|---:|---|
| KB | IVSA0070 scalar snapshot identities produced only after a verified provider UTC timestamp | snapshot | 2026-08-21 16:30–18:00 KST window was closed; no timestamp-valid current slice is retained. |
| LS | `ls_t8412_kospi200_constituent_15m_pilot / KOSPI / {000660,005930}` | 15m | The completed 2026-08-12 Raw occurrence remains immutable. A new date/symbol needs its own exact route entry and may collect Landing/Raw evidence while time-label and revision semantics remain unresolved. |
| Toss | `TOSS_MARKET_PRICE_SNAPSHOT / XKRX / KOSPI` | snapshot | The exact 2026-08-21 OAuth/transport occurrence is consumed. A new current occurrence may use a new claim and a provider-aware bounded retry policy; KOSDAQ requires its own verified identity entry. |
| FDR | exact `FDR_DAILY_ALLOWLIST` Naver/Yahoo daily identities | 1d | A bounded fallback route may be registered under standing authorization; retained source dates remain as-retrieved labels, not live availability timestamps. |
| yfinance | no accepted current numeric identity | provider-native only | No route was registered in this snapshot. A new route may be researched with an exact identity, provider-aware rate limits/retry, and Landing evidence; numeric promotion remains gated. |

## Execution requirements for a bounded route registration

1. Add one exact route entry; do not enable a broad provider family.
2. Name the operation/runbook, identity, native interval, due window, request
   cap, timeout, provider-aware bounded retry/backoff, finality, and display/PIT
   flags in that entry.
3. Keep Toss → KB → LS execution priority. Do not merge same-variable provider values.
4. Treat FDR and yfinance as independently activated fallback entries, never an
   implicit retry inside a broker adapter.
5. Use the supervisor `tick()` only for the exact registered operation scope
   under the standing Data authorization;
   use `replay()` for API-zero local readback.
6. Do not promote a current observation to Normalized, Canonical, Published, or
   Backtest data by inference.

## This implementation checkpoint

The owning tests use synthetic attempts only. Provider calls, OAuth, Landing,
project `data/state` writes, GUI calls, scheduler changes, canonical/history
promotion, Backtest access, and `.env` access are all zero.
