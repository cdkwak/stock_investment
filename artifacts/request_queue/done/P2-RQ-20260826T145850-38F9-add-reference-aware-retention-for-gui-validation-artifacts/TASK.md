# Add reference-aware retention for GUI validation artifacts

## Problem
GUI validation screenshots and diagnostics accumulate indefinitely, while active documentation directly references acceptance evidence that blind cleanup could destroy.

## Evidence
The discovery counted 40 files and 4,432,782 bytes across 2026-08-20..26, with multiple direct GUI Status references and no retention owner.

## Scope
allow:
- Add one reusable maintenance entry point, its owning tests, bounded GUI documentation/map updates, and remove only verified unreferenced eligible GUI validation artifacts through that entry point.

deny:
- No blind recursive delete, no deletion outside artifacts/gui_validation, no active/reference/current-bundle removal, no provider/API/data/scheduler/GUI runtime change, no secrets or absolute user paths in manifest, and no unrelated cleanup.

## Done When
A reusable reference-aware maintenance command defaults to dry-run, protects every path referenced by active docs/code/tests plus the current acceptance bundle, keeps the 20 newest otherwise-unreferenced files deterministically, atomically writes a no-sensitive-path manifest, and deletes only explicitly listed eligible files under artifacts/gui_validation when --apply is supplied.

## Verify
Use isolated fixture trees for reference parsing, path containment, deterministic ordering, protected/current bundles, symlinks/reparse points, dry-run/apply idempotency and manifest counts; inspect REPOSITORY_MAP and usage audit; run live dry-run first and verify every removed target is unreferenced and contained before apply.
