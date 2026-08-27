# Reconcile stale canonical equity coverage in Source Registry

## Problem
The canonical equity and market breadth coverage in SOURCE_REGISTRY is stale relative to authoritative Data Status and exact retained accepted state.

## Evidence
Canonical price, cap, provider universe, canonical universe and breadth read back through 2026-08-19 with breadth COMPLETE, while SOURCE_REGISTRY rows still report 2026-08-13 and 2026-08-12.

## Scope
allow:
- Update the two stale Source Registry facts using already validated exact retained state.

deny:
- No provider call, data/state write, scheduler/ACL mutation, source/PIT/finality reinterpretation, or unrelated documentation rewrite.

## Done When
Only the affected SOURCE_REGISTRY coverage and accepted-operation facts match the verified 2026-08-19 boundary without changing source semantics, finality, PIT, fallback or authority.

## Verify
Re-read the two rows against exact accepted/breadth state, verify links, run request queue doctor, and confirm no data/provider/scheduler mutation.
