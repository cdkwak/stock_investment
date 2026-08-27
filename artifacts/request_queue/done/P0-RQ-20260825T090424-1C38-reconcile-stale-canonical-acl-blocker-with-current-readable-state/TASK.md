# Reconcile stale canonical ACL blocker with current readable state

## Problem
Canonical recovery routing is stale: queue blockers still require an ACL mutation even though the authoritative Data Status says exact reads now succeed without one. Revalidate the exact current preconditions and transition, retarget, or supersede the stale queue blockers through the queue CLI.

## Evidence
Current Data Status: exact canonical price and state files are readable and earlier ACL blocker is no longer active. 6D1B and 716F still describe ACL grant as next action; 716F's evidence also says zero automation-enabled STALE while the current artifact has five.

## Scope
allow:
- Read exact canonical and breadth artifacts; use scripts/request_queue.py for queue transitions; update Data Status only if current facts materially change.

deny:
- No icacls or ACL mutation, no provider call, no scheduler execution, no data content write, no canonical promotion, and no direct manual edits to queue state.

## Done When
A read-only exact-path audit records current readability, hashes, canonical/breadth dates and breadth recovery-state presence; no ACL is changed; 6D1B is closed or retargeted according to evidence; 716F is unblocked or given its true remaining blocker with current Health counts; BOARD and Data Status agree on the next executable action.

## Verify
Read and hash only the exact paths named by 6D1B/716F under the current identity, validate state schemas/dates, run request_queue.py doctor, and compare BOARD routing with docs/data/DATA_STATUS.md.
