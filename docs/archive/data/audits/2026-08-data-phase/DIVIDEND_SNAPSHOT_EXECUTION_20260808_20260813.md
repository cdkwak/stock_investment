# Dividend snapshot execution evidence: 2026-08-08 and 2026-08-13

Classification: **ARCHIVED_EXECUTION_EVIDENCE**

This record preserves the historical collection outcome separated from the current
[offline append operation](../../../../data/operations/DIVIDEND_OBSERVATION_APPEND.md).
It does not authorize another API request.

## Retained 2026-08-08 baseline

- The complete retained snapshot contains **71,652 source observations**.
- It remains the only non-empty dividend snapshot promoted to
  `kr_equity_dividend_source_observation`.
- Its source snapshot identity is not an announcement date, knowledge date,
  effective date, revision timestamp, or proof of historical PIT coverage.

## 2026-08-13 bounded attempt

- Exactly one request was made with retry zero for `basDt=20260813`.
- The source returned result `00`, `totalCount=0`, and zero items.
- The first page was retained before the former non-empty local assertion stopped
  the process; no second request was made.
- Because the process stopped before its ledger step, offline recovery records the
  exact call count, retained page hash, and
  `http_status_reconstructable=false`; it does not invent transport status.
- The checkpoint is terminal `VALID_EMPTY_STOP`, preventing a silent repeat.
- No `full_history.json` and no Normalized append were produced.

The runner was subsequently corrected to record exact source-success empty pages as
terminal evidence while still failing closed on inconsistent empty responses. Do
not retry 2026-08-13 merely to seek non-empty data. A future append requires a new,
independently captured, complete and non-empty Landing snapshot.

Neither capture establishes event supersession, announcement timing, revision
lineage, or predictive availability.
