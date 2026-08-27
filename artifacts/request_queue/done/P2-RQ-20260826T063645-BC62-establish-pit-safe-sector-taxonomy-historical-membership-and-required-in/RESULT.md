result: Fixed the cross-domain routing gap: Data and Backtest Status now directly share sector-input-feasibility/v1 and its numeric-not-ready state.
changed: docs/backtest/BACKTEST_STATUS.md: added direct Data-owned feasibility link and numeric consumer gate. docs/data/DATA_STATUS.md: labeled the existing link with the same contract ID/state and shared-boundary role.
verified: Matrix counts 6/20/20/10 OK; status boundary link/state OK; holdout sealed and results_reviewed=false; scoped links and whitespace OK; queue doctor OK.; independent review by lead: Independent review reproduced 10 roles x 2 markets, six identity rows, fail-closed PIT states, shared Data/Backtest route, sealed holdout, scoped whitespace check, and queue doctor OK.
completed_at: 2026-08-27T12:09:20+09:00
