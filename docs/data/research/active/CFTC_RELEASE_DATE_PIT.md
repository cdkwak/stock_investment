# CFTC historical release-date PIT research

Status: `PREDICTIVE_USE_BLOCKED`.

Completed CFTC Historical Compressed Raw archives retain their native
report/position dates but no per-record publication timestamp. CFTC documents a
general Tuesday-position / Friday-release convention, including holiday and
revision exceptions, and does not publish a historical release-date list that
can safely reconstruct all retained rows.

Keep `release_date=null`; never infer it from a calendar rule. This question can
close only with authoritative, dated publication artifacts for the relevant
historical reports. Detailed backfill and documentation evidence is archived
under `docs/archive/data/evidence/2026-08-data-phase/cftc/`.
