# UR-232 global 60m retained-Landing current-display recovery

This API-zero operation may read only the four immutable HTTP-200 bodies under
`data/landing/global_market_60m/global60m-20260821T121202Z-ee2361078a99446399486fb17359d2a5`.
It never invokes a provider transport and never reads or changes the failed
batch's normalized/history production target, scheduler, canonical data, GUI,
or CSV.

At an explicitly supplied timezone-aware audit clock, the recovery verifies each
call's exact global-60m series identity, `http_status=200`, body path, and
SHA-256 before reusing the existing Yahoo parser and 60-minute contract. It
selects only the latest bar whose `bar_end <= audit clock`, rejects a forming,
future, stale-over-60-minute, malformed, non-finite, identity, timezone, unit,
or semantics mismatch, and never treats `ZT=F`, `ZN=F`, or `ZB=F` as a yield.

Each accepted identity writes only its own atomic display-only/PIT-blocked
UR-118 envelope under `data/state/current_observations/global60m_ur232/`. Its
immutable provenance binds the exact Landing path, body SHA-256, run ID, source
bar endpoints, audit clock, and `RETAINED_LANDING_API_ZERO_RECOVERY` class. A
failure leaves that identity's prior bytes untouched; exact same-clock replay
does not replace bytes and makes API calls zero. The historical retained-overlap
failure is neither changed nor reinterpreted.
