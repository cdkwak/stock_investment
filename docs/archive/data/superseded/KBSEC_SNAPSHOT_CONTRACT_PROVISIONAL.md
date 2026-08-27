# KB Securities provisional snapshot contract

Classification: **SUPERSEDED_PROVISIONAL_CONTRACT**

This document is retained as historical design evidence only. Its single common
`market_date` derived from `inq_dy_tm` is superseded by the current
[slice-specific snapshot contract](../../../data/research/active/KBSEC_SNAPSHOT_CONTRACT.md).
Do not use this document to normalize or publish another snapshot.

`IVSA0070` is a read-only intraday source. Its response is captured losslessly under
`data/landing/kbsec/IVSA0070/` and normalized into seven independent datasets:

- `kb_market_breadth_snapshot`
- `kb_program_trading_snapshot`
- `kb_investor_flow_snapshot`
- `kb_market_liquidity_snapshot`
- `kb_derivatives_summary_snapshot`
- `kb_domestic_index_snapshot`
- `kb_global_symbol_snapshot`

Every normalized row carries `snapshot_date`, `market_date`, `collected_at`, `source`,
`source_operation`, and `is_provisional`. `snapshot_date` is the collection date in
Asia/Seoul; `market_date` is derived only from the verified `inq_dy_tm` response field.
`collected_at` plus the dataset-specific identity fields form the primary key.

These datasets never feed rows into `kr_equity_price_daily`,
`kr_equity_market_cap_daily`, or `kr_equity_universe_daily`. Official next-day EOD
data remains authoritative. Cross-check logic can later join on `market_date` and the
market/instrument identity while retaining the KB observation timestamp.

Required environment variable names are `KBSEC_BASE_URL`, `KBSEC_APP_KEY`, and
`KBSEC_APP_SECRET`. Tokens are cached only in process memory. No account or order
endpoint is part of this pipeline.
