# Define a secure scheduled daily Toss read-only account refresh

## Problem
The live-validated Toss read-only account route is manual-click-only, so the user's daily account freshness requirement can silently remain stale.

## Evidence
Data Status and TOSS_ACCOUNT_SNAPSHOT_READONLY select one sanitized live route but explicitly prohibit periodic or Windows-scheduled refresh; the Project Goal requires daily holdings and currency-separated buying-power visibility.

## Scope
allow:
- Extend the existing Toss snapshot/runtime boundary, add one supported maintenance CLI and exact scheduler target, retain sanitized receipts/state, and update current Data authority.

deny:
- No account/token/env value output or persistence, no raw provider response retention, no account-list discovery in the scheduled route, no retry/pagination beyond the reviewed bounded policy, no orders/corrections/cancellations/transfers/withdrawals, no cross-currency inferred total, no GUI-thread call, and no KB/family-account expansion.

## Done When
A supported noninteractive daily operation uses only the exact three named runtime settings without exposing values, requires one explicit in-memory account selector, durably claims each scheduled occurrence before calls, bounds one holdings and KRW/USD buying-power cycle, preserves prior-valid data on every failure, atomically writes only identifier-free projections plus a sanitized outcome-complete receipt, replays the same occurrence at API zero, and installs/enables an exact read-back Windows task with IgnoreNew and a bounded execution limit; no account discovery or financial mutation is reachable.

## Verify
Use injected provider-free tests for missing/invalid config API zero, exact call budget, valid-empty holdings, partial/auth/network/schema failure retention, crash recovery, occurrence replay, receipt redaction and atomic readback; validate scheduler dry-run/readback and one natural occurrence without printing configuration or account identifiers; run privacy scans and queue doctor.
