# Eliminate the Windows GUI negative QFont point-size diagnostic

## Problem
The native Windows 1600x900 full-page GUI traversal emits one invalid QFont negative point-size warning, polluting diagnostics even though the current render is readable.

## Evidence
Audit task_34b0b9e3ebde visited all nine top-level tabs, both account subviews and four account sources with real QTest clicks; .tmp/agents/task_34b0b9e3ebde/GUI_AUDIT_REPORT.md records the unique warning and no existing Queue coverage.

## Scope
allow:
- Normalize invalid inherited/default font sizes at the narrow owning boundary and add deterministic provider-free regressions within the exact write scope.

deny:
- Do not suppress the global Qt message stream, add dependencies, change financial semantics, call providers, inspect identifiers, mutate account state, or invoke any broker order/transfer action.

## Done When
The exact Windows Qt traversal constructs only positive point-size fonts and emits no QFont point-size warning across all nine top-level tabs and account subviews, while Korean text, 1600x900 fit, privacy, and existing font fallback behavior remain unchanged.

## Verify
Add a focused positive-size regression and full-page warning capture; run font-policy and 1600x900 GUI tests on the Windows Qt platform, py_compile, exact-scope diff check, Queue Doctor, and fresh independent review.
