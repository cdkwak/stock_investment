updated_at: 2026-08-26T20:15:26+09:00
phase: completed
summary: Reclassified preserved newest-grid OHLC regressions as occurrence failures and removed release acceptance.
completed: Prior-present six-futures null/partial/nonfinite keeps all current-observation bytes unchanged but reports FAIL_*_PRIOR_VALUE_PRESERVED, PARTIAL_FAILURE, accepted11/failed6/preserved17; forged PASS rejected; docs reconciled.
next: none
files_touched: src/stock_data/orchestration/yahoo_market_current.py;src/stock_data/orchestration/release_readiness.py;tests/unit/orchestration/test_yahoo_market_current.py;tests/unit/orchestration/test_release_readiness.py;docs/data/operations/YAHOO_MARKET_15M.md;docs/data/DATA_STATUS.md
tests: 137 unit tests and 1 GUI integration release test passed
risks: Provider-free only; no production data/live API/history/canonical/backtest mutation.
new_discoveries: none
