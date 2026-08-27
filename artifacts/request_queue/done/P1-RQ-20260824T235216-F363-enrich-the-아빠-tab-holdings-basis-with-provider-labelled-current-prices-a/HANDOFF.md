updated_at: 2026-08-26T14:38:46+09:00
phase: completed
summary: Strict supplier-injected API-zero manual-account valuation cache and source-neutral GUI join are complete.
completed: Explicit exchange-qualified Yahoo symbol map validation; typed timestamp/currency/finality contract; Decimal section/account metrics; numeric-free unavailable states; partial aggregate suppression; basis digest binding; atomic write with prior-cache byte preservation; local fixture-only CLI and runbook/status routing.
next: none
files_touched: src/stock_data/contracts/manual_account_market_values.py; src/stock_data/providers/yahoo_account_prices.py; src/stock_data/orchestration/manual_account_market_values.py; src/stock_data/gui/manual_account_market_values.py; src/stock_data/gui/account_snapshot_service.py; scripts/maintenance/refresh_manual_account_market_values.py; docs/data/operations/FAMILY_ACCOUNT_HOLDING_CURRENT_PRICES.md; docs/data/DATA_STATUS.md; docs/gui/GUI_STATUS.md; focused tests and test package markers.
tests: 280 focused/owning tests passed; production modules py_compile passed; refresh CLI --help passed; request_queue doctor OK.
risks: Live provider transport and scheduler remain disabled; no production holding values, credentials, account identifiers, network calls, or persistent production cache writes were used.
new_discoveries: none
