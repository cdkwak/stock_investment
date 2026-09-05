# Yahoo Target-Price Consensus Check — 2026-09-05

The prior plain `query1` `quoteSummary?modules=financialData` collector was
nonfunctional: Yahoo returned HTTP 401 `Invalid Crumb`. A managed check verified
the standard session flow `fc.yahoo.com` A3 cookie -> `query2` `getcrumb` ->
`query2` `quoteSummary` with the crumb parameter.

The same flow returned full Korean consensus payloads for `005930.KS` (37
opinions) and `000660.KS` (38), both with `financialCurrency=KRW`. `SOXL`
returned HTTP 404 `No fundamentals data found`, establishing the typed
`NOT_APPLICABLE_ETF` outcome rather than a collection failure.

Implementation boundary: the run makes one captured cookie call, one captured
crumb call, then one captured request per exchange-resolved security with no
same-run retries. KOSPI maps to `.KS`, KOSDAQ to `.KQ`, and an unresolved generic
KRX identity remains a value-free legacy fallback. All results remain local,
personal, non-commercial, display-only, non-predictive, and non-redistributable.
