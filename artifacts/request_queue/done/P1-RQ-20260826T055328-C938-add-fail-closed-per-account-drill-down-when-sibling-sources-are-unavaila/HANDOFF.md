updated_at: 2026-08-26T22:31:08+09:00
phase: completed
summary: Closed the review counterexample: a selected source removed by refresh remains an identifier-free unavailable tombstone instead of silently defaulting to combined.
completed: AccountPage retains selected source id/title across refresh disappearance, keeps selector on an explicit current-missing tombstone, renders typed numeric-free missing-source state even when a valid sibling remains, and changes scope only after explicit user selection. Native 1600x900 regression covers masking, disappearance, explicit reselection, and zero horizontal overflow.
next: none
files_touched: src/stock_data/gui/main_window.py; tests/unit/gui/test_gui_backtest.py (existing C938 changes remain in account_snapshot_service.py and test_gui_services.py)
tests: Reopen baseline 21 service + 23 widget passed; post-fix 21 service + 23 widget passed; final 4 focused/native passed; py_compile passed; queue doctor OK.
risks: Provider-free GUI-only change. No provider/account call, persistence, currency inference, or financial mutation.
new_discoveries: none
