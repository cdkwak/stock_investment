# Close Toss account refresh atomic-receipt and privacy/concurrency races

## Problem
Toss account projection, terminal receipt, privacy removal, and overlapping refreshes do not share one fail-closed concurrency boundary.

## Evidence
Provider-free injected probes reproduced projection with CLAIMED receipt after BaseException, post-removal repopulation by an in-flight refresh, and a waiting contender making three calls after the first failed.

## Scope
allow:
- Implement a shared account-refresh/privacy lease or journal/recovery boundary, update the scoped runner and existing owning tests/runbook/status.

deny:
- No live provider call, order/transfer/broker mutation, scheduler change, real account identifier output, .env output, production account-file deletion, GUI redesign, or unrelated files.

## Done When
Projection and terminal occurrence evidence recover atomically after BaseException; privacy removal excludes/invalidate in-flight writers and cannot be repopulated by them; any contender returns immediately with a typed busy/API0 result before token/account access; prior valid snapshot and identifier-free logs remain preserved.

## Verify
Use isolated .tmp/agents roots and fake clients to inject BaseException after promotion, race removal against delayed promotion, race two refreshes through first failure, verify exact hashes/terminal recovery/nonblocking timing/API call counts, secret/account-ID scan, and all owning account/GUI-close regressions without a live call.
