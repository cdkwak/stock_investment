# Define a unified local GUI refresh-status and cadence projection

## Problem
No single typed read-only GUI projection consistently defines cadence kind, source as-of, last success, next eligible local refresh, in-progress, partial failure, retained-value staleness, and retry capability.

## Evidence
PROJECT_GOAL daily Dashboard criteria require cadence and lifecycle distinctions; current GUI/Project status and completed worker, watcher, preference, fit, and quiescence work cover components but not one per-surface semantic contract; 9DB8 is a release gate, not a user-facing projection.

## Scope
allow:
- Create the GUI-owned documentation contract and update only GUI_STATUS routing/current facts; use existing validated local metadata semantics; preserve independent surfaces and selections.

deny:
- No production code or tests, provider/external-AI calls, scheduler definitions, Data/account writes, invented next-run times, automatic authorization expansion, layout redesign, or implementation of completed worker/watcher/preference features.

## Done When
A documentation-only contract defines exact typed fields, cadence vocabulary, timestamp provenance, fail-closed state composition, retained-value behavior, independent component results, allowlisted local retry capability, and unresolved/unsupported states; GUI_STATUS links it as future contract evidence without authorizing runtime implementation.

## Verify
Check every Project Goal refresh-status requirement maps to one contract field/invariant; compare against 9DB8 and completed 54DC/0887/7C1A/50EB/CEEA/0290 to prove non-duplication; verify links and run request_queue doctor.
