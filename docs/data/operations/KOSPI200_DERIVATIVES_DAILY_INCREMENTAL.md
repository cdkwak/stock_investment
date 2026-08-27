# KOSPI200 derivatives daily incremental

Status: `AUTOMATION_ACTIVE / CURRENT_20260825 / T_PLUS_1_EXPECTED_LAG / RETAINED_RECOVERY_API0_REPLAY`.

The production chain is current through the latest conservatively eligible
observation, 2026-08-25. The 2026-08-26 XKRX session is the completed successor
that makes 2026-08-25 eligible; 2026-08-26 itself must not be requested until a
later XKRX session has completed. The Dashboard and Health projection therefore
report `latest=expected=2026-08-25 / EXPECTED_LAG`, not stale.

This operation is limited to the accepted data.go.kr
`GetDerivativeProductInfoService` KOSPI200 regular-session futures and options
exact-date endpoints. No alternate provider, retained Raw fallback, silent
merge, or current snapshot append is permitted.

## Current production evidence

- Source futures: 11,382 rows; Source options: 2,722,082 rows.
- Provider Bridge futures/options: 38,643 / 3,812,160 rows.
- Nearest-listed Basis: 6,544 rows.
- Persisted PCR: 4,233 rows, including 1,626 rows in the modern recovery
  segment.
- Recent Option Wall review: 250 rows.
- All seven outputs end at 2026-08-25, have zero primary-key/null-key and
  partition-year violations, and pass their runtime contract probes.
- The checkpoint contains 2026-08-19, 20, 21, 24, and 25 exactly once. No
  transaction journal or derivatives staging transaction remains.
- A 2026-08-25 replay returns `NOOP_IDEMPOTENT / api_calls=0` before credential
  or provider construction.

Basis units remain provider-native and unverified. Volume/OI PCR and maximum-OI
Wall are descriptive; price PCR and Active/Gamma Wall claims remain unavailable.
This operational T+1 acceptance does not establish provider revision finality
or upgrade predictive PIT eligibility.

## Eligibility and bounded catch-up

- Observation calendar: XKRX regular session.
- A target becomes eligible only after its immediate successor XKRX session
  completes and is present in the retained project calendar.
- Selection is oldest-missing-first and never skips an unresolved session.
- Each date permits one futures call plus one options call, retry zero.
- One scheduler occurrence permits at most three sessions, six source calls,
  and 600 elapsed seconds. The first failure stops the occurrence.
- Every provider row must carry the exact requested `basDt`.

## Atomic transaction boundary

Both immutable Landing responses are retained before candidate validation.
Source futures/options, provider Bridges, nearest-listed Basis, volume/OI PCR,
Option Wall, related state, and the completion checkpoint then commit as one
rollback unit. Missing responses, valid-empty responses, schema/date mismatch,
an invalid successor decision, promotion/read-back failure, or interruption
preserves all prior valid production. Persistent writes are atomic and a
successful same-date replay is API zero.

## Retained-Landing ACL recovery history

The historical 2026-08-19 transaction and replay completed through the reviewed
API-zero recovery path. On 2026-08-26, the next 2026-08-20 occurrence consumed
exactly two retry-zero calls and retained both exact Landing responses, then
failed before promotion while capturing the protected Bridge ACL. Production
rolled back through 2026-08-19 and no later date was attempted.

The first 2026-08-20 retained-Landing recovery also used API zero and rolled
back when target ACL restoration failed. A separately recorded, SHA-256-bound,
single-use reviewed retry accepted only that exact failed attempt/recovery pair.
After the ACL repair it atomically completed 2026-08-20 with API zero; repeating
the same command returned `NOOP_IDEMPOTENT / api_calls=0`. Ordinary failures,
different messages/phases/hashes, or a second recovery retry remain rejected.
The original failed attempt and first failed recovery are retained and are
never rewritten.

Windows directory replacement made `ChangdaeNote\k4545` the owner of these
three protected roots:

- `data/published/c007_kospi200_derivatives_bridge`
- `data/derived/kr_kospi200_futures_nearest_listed_daily`
- `data/derived/kr_kospi200_option_pcr_daily`

Protected inheritance remains enabled and both `ChangdaeNote\k4545` and
`ChangdaeNote\CodexSandboxOffline` have inheritable `Modify` on exactly those
roots. This lets the scheduled user and local agents perform the same bounded
atomic replacement without granting access outside the three targets. ACL
capture/reapply remains part of transaction read-back and any regression fails
closed.

## Scheduler and replay gate

`DERIVATIVES_PRICE_DAILY` is active in the 20:30 KST Korean market bundle. New
receipts use `lane_contract_version=2` and the exact eight-lane bundle. The
immutable 2026-08-26 20:30 legacy seven-lane receipt is accepted only at or
before the bounded cutover; later versionless receipts fail closed. The
installed task runs as `k4545` and its action/trigger/readiness policy has been
read back successfully.

After any advancement, verify all seven latest dates, checkpoint agreement,
absence of a transaction journal, Health `VALIDATED`, and an API-zero replay.
Do not manually repeat a provider call recorded by a terminal failed attempt;
use a reviewed retained-Landing recovery only when the runner recognizes its
exact fail-closed identity.
