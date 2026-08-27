# Bind GUI artifact retention apply to reviewed dry-run and redact malformed references

## Problem
Retention apply is not bound to the independently reviewed dry-run plan and malformed drive/root/traversal references are not fail-closed/redacted.

## Evidence
Stable isolated pre-audit deleted a file introduced after dry-run and persisted a C:/Users-form suffix in missing_references; no live apply occurred.

## Scope
allow:
- Modify only exact retention script/tests and bounded owning docs; provider/data/runtime API zero.

deny:
- No live apply until independent PASS; no deletion outside artifacts/gui_validation; no unreviewed target deletion; no absolute/sensitive path persistence; no provider/data/scheduler/runtime changes.

## Done When
Apply consumes an exact dry-run manifest/plan digest, verifies exact per-file metadata and rejects inventory/reference/policy drift before deletion; all captured references must be validated canonical contained relative POSIX paths and malformed drive/root/traversal forms are rejected or bounded-redacted.

## Verify
Add permanent add-after-review, modified-after-review, reference-drift, policy-drift, drive/root/traversal privacy regressions; rerun owning suite, real junction probe, live dry-run only, and independent review before any apply.
