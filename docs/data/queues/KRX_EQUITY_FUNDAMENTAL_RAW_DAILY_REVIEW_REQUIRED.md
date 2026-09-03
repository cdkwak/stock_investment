# KRX Equity Fundamental Raw Daily Incremental Candidate

역할: 이 문서는 비실행 검토 후보이며, source 의미·finality 권위는 [`MDCSTAT03501` source policy](../sources/krx/MDCSTAT03501_EQUITY_FUNDAMENTAL_FINALITY.md)다.

Status: `REVIEW_REQUIRED / NOT_EXECUTABLE`

Dataset: `kr_equity_fundamental_daily` Raw only

Provider operation: KRX `MDCSTAT03501` via pinned pykrx, `mktId=ALL`

Per-run budget: one exact-date full-market business call, retry zero

This file is a review boundary, not an active runbook. It does not authorize a
provider call, observation campaign, collection, promotion, state mutation,
Health update, or scheduler change. The retained historical baseline must not
be enumerated or reacquired.

## Current source gate

The official KRX screen and unresolved semantics are recorded in
`../sources/krx/MDCSTAT03501_EQUITY_FUNDAMENTAL_FINALITY.md`. There is no official
endpoint publication clock, historical-value revision policy, correction
window, or freeze. The typed source policy therefore defaults to
`execution_authorized=False`; free-form finality text cannot activate capture.

## Duplicate and missing-value boundary

- Every provider row is retained. Repeated issue codes are represented by their
  1-based response-local source ordinal and explicit exact/conflicting metadata.
- No duplicate row is collapsed, selected, averaged, filled, or promoted as a
  unique date-symbol observation.
- Literal `-` is retained as provider missing text. No valuation value is
  invented, forward-filled, or converted by this Raw boundary.
- The retained historical duplicate regression remains evidence; the complete
  historical dataset is not enumerated during an incremental operation.

## Preserved offline transaction boundary

- Before transport setup, a verified retained baseline or accepted incremental
  date returns an API-zero no-op after byte/hash and duplicate-metadata checks.
- A new execution requires a lead-reviewed typed `finality_evidence_id`, an
  exact completed date selected by `DATA_STATUS`, and matching authorization.
- One response is written immutably to Landing before source, operation, ALL
  scope, date, schema, non-empty response, numeric range, missing-token, and
  duplicate-group validation.
- Only the baseline-hash-bound Raw overlay checkpoint may be atomically
  replaced. Normalized, Canonical, valuation, GUI, Backtest, and predictive use
  remain outside this boundary.
- Durable capture/checkpoint interruptions recover without another provider
  call. An ambiguous possible call without durable response requires review and
  is never retried automatically.

The offline implementation is
`stock_data.orchestration.pykrx_equity_fundamental_daily`.

## Required evidence before activation

1. Separately authorize and complete the source note's three-session,
   three-window Landing-only observation campaign: one call/retry zero per
   observation, maximum nine calls.
2. Review first availability, short-delay and five-session response changes,
   missing-token movement, and duplicate-group behavior.
3. Adopt an explicit empirical revision policy and typed finality evidence ID.
4. Have `DATA_STATUS` select one exact completed session and replace or convert
   this candidate into an active runbook.
5. Run one bounded capture, validate/read back the complete response, and
   require same-date API-zero replay before scheduler consideration.

If publication or revision behavior remains non-deterministic, keep the route
manual/review-gated. One successful response does not make a scheduler eligible.
