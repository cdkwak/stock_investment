result: Completed fail-closed per-account drill-down rework, including selected-source disappearance tombstone behavior.
changed: Refresh no longer silently resets a missing selected source to combined; it remains typed unavailable and numeric-free until explicit reselection. Added native masked disappearance regression.
verified: 21 account service and 23 AccountPage/worker/privacy regressions passed after fix; final 4 focused/native checks and py_compile passed; queue doctor OK.; independent review by lead: Independent read-only review PASS: selected-source disappearance retains numeric-free tombstone; sibling values require explicit reselection; focused tests and valuation continuity passed.
completed_at: 2026-08-26T22:31:08+09:00
