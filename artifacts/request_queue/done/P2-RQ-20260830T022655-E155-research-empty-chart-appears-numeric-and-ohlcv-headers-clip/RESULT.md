result: No-symbol Research chart is an explicit unavailable state; all six OHLCV meanings are readable at native logical 1600x900.
changed: main_window.py adds a chart state stack, typed unavailable/loading messages, Korean OHLCV headers, and a Windows-safe panel width; test_gui_backtest.py adds repeated geometry regression.
verified: Native/offscreen repeated 1600x900 renders; Windows sections 64-65px vs 54px max label; Research 8/8, candidates 14/14, native layout 2/2, py_compile 3/3, diff-check, Doctor OK.; independent review by fresh_gui_reviewer: Fresh independent Reviewer task_d4a2b1dd3a7e PASS: exact generation/HANDOFF/manifest matched; Research 8/8, candidates 14/14, native layout 2/2, py_compile 3/3, diff-check and Doctor passed; repeated qwindows 1600x900 showed no clipped headers or numeric no-symbol chart.
completed_at: 2026-08-30T05:11:48+09:00
review_generation: 6c8febea833b94d6595e7e92a7d921ca
reviewed_by: fresh_gui_reviewer
