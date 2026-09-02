# FDR future-date display collector

## State

`OFFLINE_COLLECTOR_READY / SCOPE_MANIFEST_REQUIRED / STANDING_AUTHORIZATION`

UR-132 originally provided only a service and a safe validation/replay CLI. Its
provider-call count at that checkpoint was zero. A new bounded operation may
inject transport under the standing autonomous Data runbook after supplying a
scope manifest; no separate user or Lead approval is required.

## Scope manifest contract

The current JSON schema has: schema version 1, `activation_id`, this runbook
path, ISO `approved_on`, source date, unique exact identities, a global call cap,
`execution_authorized: true`, and explicit boolean `continue_after_orphan`.
`approved_on` and `execution_authorized` are compatibility names for the
agent's current, evidence-backed scope decision; they do not represent external
user approval. The historical rule requiring a source date strictly later than
an approval date is a superseded permission-era constraint. An agent may update
the implementation and schema for an exact current or historical evidence
window while preserving identity, call-budget, idempotency, and Landing gates.
It is rejected before transport construction when
missing, malformed, stale, over-budget, non-allowlisted, or previously used
with different content.

Only these route families are eligible: `NAVER:000660`, `NAVER:035420`, and
`YAHOO:^GSPC`, `YAHOO:^IXIC`, `YAHOO:SOXX`, `YAHOO:NQ=F`, `YAHOO:GC=F`,
`YAHOO:CL=F`. The consumed `NAVER:005930 / 2026-08-21` occurrence is
replay-only and is not a candidate under this allowlist. A later independent
date/route may be added after binding its exact identity, contract, and new
idempotency key.

The Normalized Yahoo daily registries now also contain EWY, `^SOX`, `^DJI`,
`ES=F`, `YM=F`, and `DX=F`, all
`REGISTERED_NOT_YET_COLLECTED`. They use the capture-first
`GLOBAL_ETF_DAILY`, `GLOBAL_INDEX_DAILY`, and `GLOBAL_COMMODITY_DAILY` lanes;
this FDR display-only allowlist is not their collection path and is not expanded
by that registration.

## Execution boundary

Any future invocation is serial and uses a manifest-bounded request budget,
timeout, rate limit, and provider-aware bounded retry/backoff policy. Successful
bodies are immutable Landing before route-specific Naver/Yahoo parsing and validation.
Each route has an independent circuit; any failure preserves the prior
display-only observation. Atomic UR-118 storage and API-zero replay are
required. Daily UTC-midnight labels remain source dates, not availability,
intraday, or final-EOD timestamps.

Unresolved availability, finality, or PIT meaning blocks only the affected
label, higher-layer promotion, and predictive use. Agents may continue official
documentation research, bounded comparison windows, and immutable Landing/Raw
evidence collection under the standing runbook.

Before a route transport is constructed, the collector acquires a process lock
and atomically records `ATTEMPTING` in its per-activation/per-route journal. A
terminal exact occurrence is replay-only. On a later resume an `ATTEMPTING`
route is converted to `ORPHANED / FDR_DISPLAY_ORPHAN_NO_REPEAT`; do not repeat
that occurrence unless retained evidence proves the request did not execute.
This does not block a genuinely new source date/window with a new activation ID
and idempotency key. Independent `PENDING` routes may run when the manifest sets
`continue_after_orphan: true`; an agent may set it after proving the routes have
independent call budgets, state, and promotion boundaries.

This collector does not itself own scheduler, GUI, canonical/history, Backtest,
account, or order behavior. A validated display route may be scheduled through
the standing runbook; it never gains canonical, predictive, account-mutation, or
order authority by doing so. Secrets and authentication material remain outside
its artifacts and logs.
