result: PASS candidate: all four Data Status summary cards display their complete operational text at native Windows logical 1600x900 after one local-only reread.
changed: src/stock_data/gui/main_window.py: replace fixed 102px card height with shared native wrapped-text minimum and accessible descriptions; tests/unit/gui/test_gui_health.py: add 1600x900 viewport four-card fit/count regression.
verified: 18 GUI health tests passed; 5 focused Data Status integration tests passed; py_compile passed for all three owned modules; native Windows window=1600x900 viewport=1584x854 local-reread clicks=1 cards=4/4 fit body=36 required=36 card=106 horizontal-scroll-max=0; Queue Doctor OK.; independent review by fresh_gui_reviewer: Independent PASS: exact HEAD, two-file changed manifest plus unchanged owned test hash, evidence hashes, 18+5 focused tests, py_compile 3/3, Doctor, and native Windows 1600x900 one-click 4/4 card fit measurements reconciled; full suite explicitly unrun.
completed_at: 2026-08-30T03:39:08+09:00
review_generation: a20fe6d652177ad3038593269f0e8e9a
reviewed_by: fresh_gui_reviewer
