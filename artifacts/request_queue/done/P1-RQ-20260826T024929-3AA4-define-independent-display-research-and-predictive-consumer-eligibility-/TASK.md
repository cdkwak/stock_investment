# Define independent display, research, and predictive consumer eligibility for every dataset

## Problem
Consumer trust is not fully orthogonal: display and predictive fields exist, but research eligibility is inferred from operational/research cadence, and Health has no explicit display/research/predictive triad.

## Evidence
DatasetUniverseSpec exposes gui_use plus predictive_pit_status but no research eligibility; DatasetHealth exposes operational and predictive classifications only; the 80-row artifact and Health projector therefore cannot state all three consumer decisions independently.

## Scope
allow:
- Add typed consumer eligibility enums/fields/invariants and read-only projections; map existing evidenced semantics conservatively; update exact tests, generated inventory, Dataset Index, Data Status, and standing onboarding acceptance gate.

deny:
- No provider call, collector, scheduler mutation, GUI layout/runtime behavior, predictive promotion by inference, change to retained data, weakening of PIT/finality/operational gates, or broad documentation rewrite.

## Done When
Every one of the 80 universe rows has explicit independently validated display, research, and predictive eligibility plus bounded reason codes; blocked and research/static gaps are classified without making collection readiness imply display/research/predictive use; Health projection serializes the triad; the multiaxis artifact and current Data documentation agree; existing operational/automation gates remain unchanged.

## Verify
Run owning daily-operations and health-reconciliation tests; assert all 80 rows populate all three axes, invalid combinations fail closed, representative display-only/research-only/PIT-blocked/PIT-safe rows remain distinct, artifact values exactly match typed registry, and no scheduler/provider behavior changes.
