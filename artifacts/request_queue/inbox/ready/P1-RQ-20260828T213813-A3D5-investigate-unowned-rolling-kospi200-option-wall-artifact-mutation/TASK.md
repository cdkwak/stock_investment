# Investigate unowned rolling KOSPI200 option-wall artifact mutation

## Problem
The rolling KOSPI200 option-wall artifact has an unattributed one-row roll in the canonical worktree, creating an ownership and commit-provenance risk for a GUI-visible derived financial artifact.

## Evidence
The exact Git diff is one removed row and one added row while current Data authority identifies the artifact as an atomic output of the scheduled DERIVATIVES_PRICE_DAILY chain. Current bytes must be preserved; no newer Done receipt satisfies this provenance investigation.

## Scope
allow:
- Read-only Git/file metadata and sanitized scheduler-receipt inspection; copied-state provider-free/API-zero replay; narrowly scoped producer, scheduler-entrypoint, manual-rebuild, test and active-operation documentation hardening only when evidence proves an ownership defect.

deny:
- Any provider call, canonical artifact rewrite/restore/revert before attribution, scheduler registration change, unsupported semantic or PIT claim, unrelated file/data mutation, direct Queue edits, secret/account/broker access, or order/transfer/purchase action.

## Done When
The current artifact bytes are preserved and the one-row roll is attributed by deterministic local evidence to one exact supported producer occurrence, input generation and atomic publication boundary. The production scheduler has one explicit owner for the artifact, any competing or silent writer path is removed or fail-closed, and a copied-state API-zero replay reproduces the same bytes without rewriting canonical data. If attribution cannot be proven, the task remains incomplete and records the exact missing evidence rather than guessing ownership or altering the artifact.

## Verify
Capture the pre-investigation artifact digest and exact one-for-one Git diff; reconcile file metadata, retained derivative state and sanitized scheduler occurrence receipts; run the owning derivatives integration tests plus a copied-state API-zero replay; confirm the canonical artifact digest is unchanged, provider calls are zero, unrelated data is untouched, and the supported production path is the sole scheduler writer.
