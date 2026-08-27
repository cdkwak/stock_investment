updated_at: 2026-08-26T23:16:10+09:00
phase: completed
summary: On-demand 아빠-tab CSV import and local manual pension/ISA/general account CRUD are integrated into AccountPage with atomic privacy-validated storage and fail-closed purchase-basis presentation.
completed: Added strict manual registry and CSV parser, portfolio projection, local worker integration, add/edit/delete dialog controls, bounded CSV merge, tests, and GUI status update.
next: none
files_touched: docs/gui/GUI_STATUS.md; src/stock_data/gui/account_snapshot_service.py; src/stock_data/gui/google_sheet_account_import.py; src/stock_data/gui/main_window.py; src/stock_data/gui/manual_account_store.py; tests/unit/gui/test_google_sheet_account_import.py; tests/unit/gui/test_gui_backtest.py; tests/unit/gui/test_manual_account_store.py
tests: 256 focused GUI service/manual tests passed; 4 Account GUI selection/import/privacy/worker tests passed; py_compile passed. Full test_gui_backtest has a pre-existing unrelated dashboard health exact-dict failure caused by added display_* keys.
risks: Desktop imports a user-exported CSV only; no live Google connector or schedule. Manual basis does not include current price/market value and remains excluded from aggregate totals.
new_discoveries: none
