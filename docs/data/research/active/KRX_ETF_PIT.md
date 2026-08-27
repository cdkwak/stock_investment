# KRX ETF historical PIT research

Status: `RAW_BACKFILL_COMPLETE / PREDICTIVE_USE_BLOCKED`.

The dated full-market KRX `MDCSTAT04301` Landing set is complete for
2008-01-02..2026-08-12: 4,590 dates and 1,700,421 Raw rows. The
`kr_etf_ohlcv_daily` checkpoint has hash-bound references to the same
date-specific bodies; it made no additional provider calls or byte copies.

This is the required historical-universe route. Do not use the current ETF list
or LS current-symbol endpoints to backproject membership, and do not reacquire
these completed Raw bodies.

Remaining questions are limited to source publication time, revision behaviour,
delisting interpretation, units/availability policy, and a DatasetContract.
Until they are resolved, there is no Normalized/Canonical artifact and no PIT or
predictive-use claim. Detailed pilot and acquisition evidence is archived under
`docs/archive/data/evidence/2026-08-data-phase/pykrx/`.
