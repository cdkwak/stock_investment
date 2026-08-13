# H4 boundary market/metric parity diagnostic

Prepared and **unexecuted**; no live access is authorized.

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

Execution, only if separately authorized, requires all guards and scope
`20170519_20170522_investor_boundary_parity` on
`diagnose_a007_investor_h4_boundary_parity.py`.
