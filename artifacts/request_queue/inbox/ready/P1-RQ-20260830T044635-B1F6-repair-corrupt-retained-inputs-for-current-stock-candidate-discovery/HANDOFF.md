updated_at: 2026-08-30T04:46:35+09:00
phase: discovered
summary: Provider-free real current-candidate scan returns LOCAL_CANDIDATE_INPUT_CORRUPT after the GUI now distinguishes typed retained-input failures.
completed: evidence captured
next: Coordinator triage
files_touched: none
tests: Without provider refresh, run the supported current-candidate local scan against the retained inputs and observe typed LOCAL_CANDIDATE_INPUT_CORRUPT; validate both local daily datasets to identify the corrupt input and prove safe regeneration plus valid-empty distinction.
risks: untriaged
new_discoveries: none
