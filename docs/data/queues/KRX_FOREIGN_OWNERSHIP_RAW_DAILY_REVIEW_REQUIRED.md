# KRX Foreign Ownership Raw Daily Incremental Candidate

역할: 이 문서는 비실행 검토 후보이며, source 의미·finality 권위는 [`MDCSTAT03701` source policy](../sources/krx/MDCSTAT03701_FOREIGN_OWNERSHIP_FINALITY.md)다.

Status: `REVIEW_REQUIRED / NOT_EXECUTABLE`

Dataset: `kr_equity_foreign_ownership_daily` Raw only

Provider operation: KRX `MDCSTAT03701` via pinned pykrx, `mktId=ALL`

Per-run budget: one exact-date full-market business call, retry zero

This file is a review boundary, not an active runbook. It does not authorize a
provider call, observation campaign, collection, promotion, state mutation,
Health update, or scheduler change. The retained historical baseline must not
be enumerated or reacquired.

## Current source gate

The official KRX screen and market-session hours are confirmed in
`../sources/krx/MDCSTAT03701_FOREIGN_OWNERSHIP_FINALITY.md`. No official endpoint
publication timestamp, correction window, or freeze is established. The typed
default source policy is therefore `execution_authorized=False`; free-form
finality text cannot open the gate.

## Preserved offline transaction boundary

- Before transport setup, a verified retained baseline or accepted incremental
  date returns an API-zero no-op after byte/hash validation.
- A new execution requires a source policy carrying a lead-reviewed typed
  `finality_evidence_id`, an exact completed date selected by `DATA_STATUS`, and
  a matching exact-date authorization.
- One response is written immutably to Landing before validation. Source,
  operation, ALL scope, requested date, required fields, non-empty rows, nulls,
  and date-symbol uniqueness fail closed.
- Only the hash-bound incremental Raw checkpoint may be atomically replaced.
  Normalized, Canonical, Dashboard, Backtest, and predictive promotion remain
  outside this boundary.
- A durable captured response or committed checkpoint is recovered without a
  provider call. An ambiguous journal with a possible call and no durable
  response requires review and is never retried automatically.

The offline implementation is
`stock_data.orchestration.pykrx_foreign_ownership_daily`.

## Required evidence before activation

1. Complete the source note's three-session, two-window observation campaign
   under separate authorization: one call per observation, retry zero, immutable
   Landing only.
2. Demonstrate the availability pattern and whether next-window bytes revise.
   Absence of observed revision alone is bounded empirical evidence, not an
   official freeze.
3. Have the lead adopt a typed publication/finality policy and evidence ID.
4. Have `DATA_STATUS` select one exact completed session and convert or replace
   this document with an active runbook.
5. Run one bounded capture, validate/read back the complete ALL response, and
   require same-date API-zero replay before scheduler consideration.

If timing remains non-deterministic, the operation stays manual/review-gated.
Scheduler eligibility requires an explicit retry/failure policy and repeated
bounded evidence; it is not implied by one successful capture.
