# Diagnose managed-health stale rows and propagate decision holds without provider calls

## Problem
Diagnose three stale managed-health rows and explicitly hold dependent GUI decisions without external calls.

## Evidence
Quant audit REPORT.md; release gate 36/39; three rows observed 2026-08-27 expected 2026-08-28.

## Scope
allow:
- Local retained-state diagnosis, typed dependency map, truthful Data status routing.

deny:
- No provider call, canonical data rewrite, numeric substitution, broker/account action, protected CSV.

## Done When
Provider-free diagnosis is bounded; dependency mapping holds affected conclusions numeric-free; existing Dashboard suppression is preserved; regression covers it.

## Verify
Provider-free health/readiness fixtures and GUI dependency tests; independent review.
