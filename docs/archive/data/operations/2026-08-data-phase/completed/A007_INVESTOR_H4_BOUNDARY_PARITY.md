# H4 boundary market/metric parity diagnostic

Status: **EXECUTED / PAUSED_ACCESS_SAFETY**. Run
`20260813T112742Z_f6827bb1340c4170b33a51f2ae8debaa` stopped on the first
planned scope, KOSPI trading value. Its business response was HTTP 403
restriction HTML, retained Landing-first as a 2,613-byte body with SHA-256
`2c860edd6d3458284e3b7f2f727385462a5e2c59d3f32ec4244da90780c0dfa9`
plus provenance and ledger. Five authentication responses preceded the one
business response. There was no retry, and planned calls 2/3 (KOSDAQ volume and
KOSDAQ trading value) were not made. Do not execute this diagnostic again or
make another recovery probe while access is paused.

One authenticated session makes exactly three sequential `MDCSTAT30301` calls
for `2017-05-19..2017-05-22`: KOSPI trading value, KOSDAQ volume, then KOSDAQ
trading value. Both retained market calendars contain exactly those two dates.
Caps are three business/eight raw calls (five authentication plus three
business), retry zero, parallelism one, and the shared D lock. Every response is
written Landing-first with its own provenance before classification; there are
no checkpoint or Normalized writes.

Only three sole-positive-`2017-05-22` results produce
`SHARED_BOUNDARY_SHAPED_CONFIRMED`. Any exact two-date result produces
`METRIC_OR_MARKET_SPECIFIC_WINDOW_EFFECT`; other shapes stop immediately.
Neither result establishes availability beyond these market/metric probes.

The historical scope was `20170519_20170522_investor_boundary_parity`. It
produced no market/metric parity classification: KOSDAQ volume and both
trading-value parity conclusions remain unknown. This retained access failure
does not alter the earlier KOSPI-volume boundary evidence or authorize Investor
production resume.
