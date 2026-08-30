# Establish Korean government-bond yield-curve Data contract and local projection

## Problem
The existing six-tenor BOK ECOS 817Y002 source-observation contract and offline policy cannot select an expected date or Dashboard route because publication time, provisional marker, revision identity and revision window remain undocumented; the missing work is finality observation, not a new dataset contract.

## Evidence
DATA_STATUS marks OFFLINE_CONTRACT_READY/PUBLICATION_FINALITY_UNKNOWN; the active v1 source-observation contract already defines 2Y/3Y/5Y/10Y/20Y/30Y percent yields; retained history has 29,674 rows through 2026-08-13; the review-required runbook mandates three consecutive provider-publication-day observations and prohibits promotion until a rule is reviewed.

## Scope
allow:
- After BB30 review and explicit Data Status activation, adapt the existing BOK pilot/policy and exact listed evidence/state/Landing paths for bounded finality observation only; update Data Status as domain owner.

deny:
- No historical repeat, normalized/canonical promotion, Dashboard code, scheduler, automatic expected-latest inference, XKRX/futures calendar proxy, more than six calls per batch, retry, key-bearing URL/log, provider substitution/merge, or claim that three consistent days prove permanent finality.

## Done When
After BB30 acceptance and an explicitly activated Data runbook, extend the existing supported pilot/policy only as needed for three predeclared consecutive provider-publication-day observation batches; each batch selects one provider-native date, makes at most six exact-tenor StatisticSearch calls with retry zero, writes immutable Landing first, records the official single-table UI p-marker separately, and performs next-provider-day byte/field comparison; a versioned ledger preserves capture/publication/revision evidence and API counts; evidence either supports a reviewed all-six-tenor availability/finality rule and exact next daily-route gate or truthfully keeps UNKNOWN; no normalized promotion occurs in this task; DATA_STATUS/runbook/source evidence replace stale facts.

## Verify
Use synthetic unit/regression fixtures for exact six-tenor scope, pre-network no-op, retry-zero budget, Landing-before-ledger, partial/duplicate/wrong-date rejection, marker separation, next-day comparison, atomic ledger/recovery and sanitization; for each separately authorized live window record exact calls/hashes and API-zero replay; run queue doctor. Do not infer a rule if three-day evidence is inconsistent or incomplete.
