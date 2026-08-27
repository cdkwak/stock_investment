# Reconcile canonical equity and breadth coverage in Dataset Index

## Problem
The Dataset Index navigation rows for canonical equity and market breadth are stale and are not owned by the existing Data Status or Source Registry reconciliation tasks.

## Evidence
Current exact state already proves a shared 2026-08-19 boundary while Dataset Index reports 2026-08-13/14; 0E3A may advance this further at the next natural catch-up.

## Scope
allow:
- Update only the affected Dataset Index coverage and current operation wording from validated local evidence after 0E3A.

deny:
- No provider call, data/state write, schedule/ACL mutation, source/finality/PIT reinterpretation, or unrelated documentation rewrite.

## Done When
Only the affected Dataset Index rows match the final exact retained canonical price/cap/provider-universe/canonical-universe/breadth boundary after 0E3A, retain source/finality/PIT semantics, and agree with Data Status and Source Registry.

## Verify
Read exact accepted/breadth states and runtime coverage after catch-up, compare all five families, verify links, run queue doctor, and confirm no data/provider/scheduler mutation.
