updated_at: 2026-08-26T18:29:40+09:00
phase: completed
summary: SOURCE_REGISTRY kr_index_daily row still ends current coverage at 2026-08-19 while retained index partitions and state are finalized through 2026-08-25.
completed: evidence captured
next: none
files_touched: none
tests: Read both kr_index_daily 2026 partitions and exact state, then compare max date/count/duplicate keys with SOURCE_REGISTRY kr_index_daily coverage and verify links.
risks: untriaged
new_discoveries: none
