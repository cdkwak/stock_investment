result: Source Registry now states exact kr_index_daily coverage through 2026-08-25 with 158 retained 2026 rows per KOSPI/KOSDAQ partition and zero duplicate dates, consistent with state and Dataset Index.
changed: The exact SOURCE_REGISTRY reconciliation already existed before this claim, so it was preserved byte-identically and no redundant file mutation was made.
verified: Contract-read both exact retained partitions; 158 unique dates each through 2026-08-25, duplicate primary keys zero, exact market/symbol/source; state SUCCEEDED with finalized_market_date and retained_latest 2026-08-25; hashes unchanged; 8 tests passed; 10 local links valid; API zero; Doctor OK.
completed_at: 2026-08-26T20:09:56+09:00
