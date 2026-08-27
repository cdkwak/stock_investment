# KRX VKOSPI daily incremental

This operation is limited to `kr_vkospi_daily` after its historical Raw and
Normalized datasetization. It never refreshes full history and never calls a
different KRX dataset.

The automated lane uses a bounded empirical finality policy: the requested date
must be a completed XKRX session and the run must occur after 18:30 KST. This is
evidence level `BOUNDED_EMPIRICAL`, not a predictive/PIT finality claim. Starting from the
state field `last_accepted_market_date`, request only the missing bounded date
window through official `MDCSTAT01201` parameters `indTpCd=1` and
`idxIndCd=300`. An in-progress current date is not eligible merely because the
source returns a row.

Use the shared D-owned KRX lock, a single stream, retry zero, and Landing-first
capture. Stop on HTTP 403/429, HTML/block content, source error, schema drift,
duplicate date, a date outside the frozen request window, or overlap that does
not exactly equal accepted rows. Validate Raw source strings and the
conservative Normalized projection separately. Promotion is offline and atomic
for Raw, Normalized, and state; it may append only previously missing finalized
trading dates. Do not impute source-empty fields or promote the dataset to
`PIT_SAFE`.

The reviewed 2026-08-18 exact-date run used one KRX business call and atomically
promoted Raw, Normalized, and state through that date. Immediate replay returned
API 0. `STOCK_DATA_VKOSPI_DAILY` is installed at 19:00 KST with
`MultipleInstances=IgnoreNew`; its actual manual Task Scheduler trigger returned
result 0 and its second execution was a pre-network no-op.
