updated_at: 2026-08-26T20:19:56+09:00
phase: completed
summary: Aligned UR246 pointer runbook with lock-before-candidate-validation/read behavior and retained Windows spawned-process non-rewind plus os._exit crash-release regressions.
completed: Exact two-file scope changed; production script unchanged; separate older updater preserves newer pointer bytes after post-unlock re-read; crash holder exits abruptly and next process advances to exact valid newer receipt.
next: none
files_touched: docs/data/operations/TOSS_DOMESTIC_UR246_RECURRING_30M.md; tests/unit/orchestration/test_collect_toss_domestic_ur246_cli.py
tests: Owning CLI suite 31 passed on Windows; new spawned non-rewind and os._exit crash-release tests executed; py_compile passed; provider/API/data production mutation zero; Doctor OK.
risks: Windows-specific regressions skip on non-Windows by design; existing cross-platform process-shared test remains.
new_discoveries: None.
