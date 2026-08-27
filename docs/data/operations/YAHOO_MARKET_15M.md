# Yahoo Market 15-Minute Operation — HISTORICAL VALIDATION EVIDENCE

This runbook preserves the successful retry-zero UR-022 Landing capture and the
UR-030 provider-native lane correction. Its exact 2026-08-18/19 receipts are
same-scope immutable evidence, not a current permission boundary. New current
windows, bounded provider-aware retries, and scheduler changes are authorized
by Data Status and the standing autonomous runbook; they must retain lane-local
identity/session validation, prior-valid preservation, and same-occurrence
API-zero replay. Current recurring collection belongs to
`GLOBAL_MARKET_CURRENT_60M.md` and `STOCK_DATA_YAHOO_MARKET_30M`.

The unified current operation treats the six contracted 30-minute continuous
futures routes (`ZT=F`, `ZN=F`, `ZB=F`, `NQ=F`, `GC=F`, and `CL=F`) specially
when Yahoo appends an irregular quote-time row. Only exact 30-minute grid starts
can be completed-bar candidates. If the newest eligible grid row has null,
partial, or non-finite OHLC, the collector does not fall back to an older bar
and does not substitute the quote-time row. An exact valid prior projection is
preserved with
`FAIL_COMPLETED_GRID_OHLC_UNAVAILABLE_PRIOR_VALUE_PRESERVED`; absence of such a
prior is also a typed terminal failure. Both outcomes fail the occurrence and
release gate because preservation does not prove successful freshness. No
comparison, session trace, history, or predictive dataset is written from the
rejected row.

## Exact scope

- Native interval: `15m`; no resampling, timestamp invention, or gap filling.
- Provider: Yahoo indicative/delayed chart observations, not licensed realtime.
- Every retained key is UTC. `source_timezone`, `display_timezone`, and the
  provider-local `market_date` remain explicit.
- Only bars with `bar_end <= retrieved_at` are eligible.
- Immutable Landing, retry zero, staged validation, atomic promotion, and
  pre-network exact-scope replay remain mandatory.

## Provider-native lanes

| Lane | Identities | Source timezone | Expected starts | Boundary and closed-session policy |
|---|---|---|---|---|
| `XNYS_MARKET_INDEX` | `NQ=F`, `^IXIC`, `^GSPC` | `America/New_York` | XNYS regular open inclusive to close exclusive, every 15 minutes | Provider close-boundary observations are excluded; XNYS-closed scope is API 0 |
| `CBOE_VIX` | `^VIX` | `America/Chicago` | XNYS-aligned regular open inclusive to close exclusive, every 15 minutes | The observed Cboe close-boundary row is explicitly excluded; shared U.S. holiday closure is API 0 |
| `YAHOO_TREASURY_QUOTE` | `^FVX`, `^TNX`, `^TYX` | `America/Chicago` | 08:20 through 13:50 local, every 15 minutes (23 starts) | XNYS-closed scope is API 0; early-close shape is unreviewed and fails before network |

The Treasury quote identities are provider-native quote levels. They are not
official FRED yields and cannot replace the daily FRED yield contracts.

## Retained UR-022 evidence

The one completed bounded scope is retained under
`data/landing/global_market_15m/global15m-20260819T184255Z-1be3fa8ccc5341dd81a8786053bf129a/`.
All seven calls returned native `15m` payloads with zero retries:

- `NQ=F`, `^IXIC`, and `^GSPC`: 26 accepted XNYS regular starts; the provider
  supplied an extra close-boundary row for `NQ=F`, excluded by the upper bound.
- `^VIX`: 26 accepted XNYS-aligned starts plus one provider close-boundary row,
  excluded by the upper bound; source timezone `America/Chicago`.
- `^FVX`, `^TNX`, and `^TYX`: 23 starts each, 13:20 through 18:50 UTC on
  2026-08-18, source timezone `America/Chicago`.

This retained evidence is sufficient for the offline contract correction. It
does not permit duplicating the same logical occurrence, but it does not block a
new current window or research call under standing authorization.

## Lane-local transaction

1. Select one lane and derive its reviewed completed session scope.
2. Before provider access, return `NOOP_MARKET_CLOSED` for an explicitly closed
   scope or `NOOP_ALREADY_ACCEPTED` only when that lane's checkpoint and exact
   retained expected starts both validate.
3. Make one retry-zero call per identity in the selected lane and retain every
   response in immutable Landing.
4. Validate identity, native interval, exact source timezone, completed-bar
   cutoff, schema/OHLC, and exact expected starts with no fills.
5. Merge exact overlaps, stage, reread, and atomically replace the candidate
   while holding the shared dataset lock.
6. Commit only that lane's checkpoint under `data/state/global_market_15m/`.
7. Re-run the same lane and require `NOOP_ALREADY_ACCEPTED / api_calls=0`.

A lane failure preserves the previously valid shared dataset and cannot remove
another lane's accepted observations or checkpoint. Lanes do not share an
expected timestamp grid or rollback decision.

## Selected 2026-08-19 bounded validation

The selected validation may start only after 2026-08-20 05:31 KST and only
after `reviewed_native_scope()` proves `session_date=2026-08-19` before any
provider access. It has one
attempt per identity, retry zero, and the following independent budgets:

- `XNYS_MARKET_INDEX`: 3 business calls (`NQ=F`, `^IXIC`, `^GSPC`).
- `CBOE_VIX`: 1 business call (`^VIX`).
- `YAHOO_TREASURY_QUOTE`: 3 business calls (`^FVX`, `^TNX`, `^TYX`).

The 05:20 XNYS attempt consumed its three-call budget after the internal
30-minute completion buffer selected 2026-08-18. Preserve that Landing, roll
back its unintended Normalized/state to the pre-run absent state, and do not
retry `XNYS_MARKET_INDEX` in this operation. The remaining call budget is only
the one Cboe call and three Treasury-quote calls.

For each lane: retain immutable Landing first, validate the exact native grid,
promote only that lane atomically, read it back, then immediately rerun the
same command and require `NOOP_ALREADY_ACCEPTED / api_calls=0`. A provider,
timezone, boundary, schema, completeness, Landing, promotion, read-back, or
replay failure leaves that lane disabled and does not stop review of the other
two lanes. Do not retry a failed identity in this selected scope.

Run one lane only per command and bind it to the exact selected date:

`python scripts/maintenance/run_global_market_15m.py --lane <LANE_ID> --session-date 2026-08-19 --project-root <repo>`

The command asserts that the reviewed calendar scope equals `--session-date`
before provider access. A mismatch is a zero-call failure; do not omit or infer
the exact date from wall-clock time.

Scheduler eligibility remains unclaimed until that lane separately passes the
selected live run and same-scope API-0 replay. GUI activation is also a separate
consumer acceptance step. No operation in this runbook changes official FRED
yield contracts or labels Yahoo Treasury quote indices as yields.

## Accepted 2026-08-19 lane results and scheduler scope

- `CBOE_VIX`: one retry-zero call, 26 accepted rows, atomic promotion and
  read-back passed; immediate replay returned `NOOP_ALREADY_ACCEPTED / api_calls=0`.
- `YAHOO_TREASURY_QUOTE`: three retry-zero calls, 69 accepted rows (23 per
  identity), atomic promotion and read-back passed; immediate replay returned
  `NOOP_ALREADY_ACCEPTED / api_calls=0`.
- `XNYS_MARKET_INDEX`: not accepted. Its pre-fix 05:20 command selected
  2026-08-18, consumed three calls, and was rolled back to the prior absent
  Normalized/checkpoint state. Preserve Landing and do not retry that scope.

The first two lanes are installed as separate daily Windows tasks at 06:45
and 06:50 KST respectively. These times remain after the XNYS regular close
plus 30 minutes under both U.S. daylight and standard time. Each task uses the runner's distinct typed
`--scheduled` mode, which derives and records one exact completed session only
after the 30-minute post-close gate. Manual runs continue to require an explicit
`--session-date`; the two modes are mutually exclusive. Each task uses the
repository virtual-environment Python and repository working directory, and
uses `MultipleInstances=IgnoreNew`. After installation, manually
trigger each task once and require exit result 0, the lane's existing retained
scope, and API 0. Do not install an XNYS 15-minute task. A future scheduler run
may call only for a newly planned exact completed session; a planning mismatch
must stop before network.
