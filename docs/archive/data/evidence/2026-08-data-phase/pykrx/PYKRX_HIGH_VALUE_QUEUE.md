# pykrx/KRX high-value queue

Persistent execution checkpoint. The High-Value Historical Acquisition Batch is
closed: one range-native source was backfilled Raw and six date-snapshot sources
were stopped or deferred with retained evidence. Do not repeat retained pilots.

This file preserves per-candidate evidence, calls, and next gates. It does not set
current Data priority or authorize execution. Start with
[Data Status](../../../../../data/DATA_STATUS.md); if its routing conflicts with this retained queue,
Data Status controls.

## Queue

- [x] Short Investor
  - terminal: `BACKFILL_COMPLETE`
  - evidence: `data/normalized/kr_short_selling_investor_daily/`
  - coverage: 2017-05-22..2026-08-07; 45,200 rows
  - calls: 152 completed business scopes in the retained backfill
  - PIT status: usable from the observed source boundary; no pre-boundary inference
  - normalized status: complete
  - remaining gap: 2008..2017-05-19 unavailable from verified source behavior
  - next_gate: T+1 maintenance only

- [x] Index Fundamental
  - terminal: `RAW_BACKFILL_COMPLETE`
  - evidence: `data/landing/diagnostics/pykrx_fundamentals_pilot/20260815T015855Z_5c6ec1d853f445e4aabdb076e2700a73/`
  - coverage: KOSPI/KOSPI200/KOSDAQ 2000-01-04..2026-08-12; KOSDAQ150/KRX300 2010-01-04..2026-08-12; 27,855 Raw rows
  - calls: 70 business / 75 raw HTTP, retry 0; 14 <=730-day scopes per index
  - PIT status: publication/revision unresolved; KOSDAQ150/KRX300 backcast history is not PIT-safe
  - normalized status: forbidden
  - remaining gap: formal index identity/backcast and revision policy
  - next_gate: reviewed contract; predictive use must exclude unverified backcasts

- [x] Equity Fundamental
  - terminal: `RAW_BACKFILL_COMPLETE`
  - evidence: `data/state/pykrx_high_value_raw/kr_equity_fundamental_daily.json` and `data/landing/pykrx/high_value_raw/kr_equity_fundamental_daily/plan=c28d1be22342a7af22e0d8efd0db4d7ec9cce69c90934c19a83030addf5b0e30/`
  - coverage: 2008-01-03..2026-08-12; 4,589/4,589 planned dates and 9,925,137 validated Raw rows. The retained set includes three adopted pilots and three provider-duplicate groups.
  - calls: 4,586 completed new business calls plus 3 adopted pilots; retry 0. Provider duplicate observations are retained with source row ordinals; no date was retried.
  - PIT status: publication and historical revision timing unresolved
  - normalized status: forbidden
  - remaining gap: source identity/grain for duplicate short codes and publication/revision timing are unresolved
  - next_gate: reviewed duplicate-identity policy using preserved source fields; no Raw reacquisition

- [x] Foreign Ownership
  - terminal: `RAW_BACKFILL_COMPLETE`
  - evidence: `data/state/pykrx_high_value_raw/kr_equity_foreign_ownership_daily.json` and `data/landing/pykrx/high_value_raw/kr_equity_foreign_ownership_daily/plan=60c93f1e2cb027ed167d3fa1a9993bd8f08be173d5780c3a2a58af435d824a31/`
  - coverage: 2000-01-05..2026-08-12; 6,558/6,558 dates; 13,910,258 Raw rows
  - validation: discrepancy/duplicate/null = 0; checkpoint normal end
  - calls: 6,555 completed business calls plus 3 adopted retained pilots; retry 0
  - PIT status: publication/revision timing unresolved
  - normalized status: forbidden
  - historical backfill: closed; no Raw date gap within the frozen plan
  - daily maintenance: `DAILY_MANUAL_READY`; no schedule recorded
  - next_gate: reviewed contract/publication policy before any Normalized promotion

- [x] Index Constituents
  - terminal: `PIT_BLOCKED`
  - evidence: `data/landing/diagnostics/pykrx_fundamentals_pilot/20260815T012324Z_5e43157087b0407e8af498f37b90d914/`
  - coverage: KOSPI200 and KOSDAQ150 on 2020-01-02 and 2026-08-12; 700 Raw rows
  - calls: 4 business / 9 raw HTTP, retry 0
  - PIT status: historical-date response exists; effective-date and revision semantics unresolved
  - normalized status: forbidden
  - remaining gap: exact rebalance effective dates/change points are not established; daily brute force would require about 3,246 calls from 2020 and is expressly avoided
  - next_gate: official effective-date schedule or range/change-event source

- [x] Sector Classification
  - terminal: `PILOT_COMPLETE_WITH_LIMITS`
  - evidence: `data/landing/diagnostics/pykrx_fundamentals_pilot/20260815T020423Z_c50c589a398a4054bc2039ace71cba85/`
  - coverage: KOSPI 2020 (916), KOSDAQ 2020 (1,399), KOSDAQ recent (1,820), plus retained recent KOSPI (942)
  - calls: 4 business across two runs / 20 run-level raw HTTP, retry 0
  - PIT status: historical values are distinct, but exact classification effective dates remain unresolved
  - normalized status: forbidden
  - remaining gap: exact change dates and revision policy
  - next_gate: official effective-date/change-event evidence; no daily brute force

- [x] ETF
  - terminal: `RAW_BACKFILL_COMPLETE`
  - evidence: `data/state/pykrx_high_value_raw/{kr_etf_universe_daily,kr_etf_ohlcv_daily}.json` and `data/landing/pykrx/high_value_raw/kr_etf_universe_daily/plan=d63916061065081ba8927947a19374b2891808c397f36e1ed94035b68a7f7f61/`
  - coverage: 2008-01-02..2026-08-12; 4,590/4,590 date-specific full-market responses, 1,700,421 Raw rows. `kr_etf_ohlcv_daily` retains hash-bound references to these same bytes.
  - calls: Universe 4,588 business calls plus 2 adopted retained pilots; OHLCV 0 business calls, retry 0, and no copied bytes. Weekend valid-empty remains retained pilot evidence outside the business-date plan.
  - PIT status: historical market/date evidence exists; universe/delisting and revision semantics unresolved
  - normalized status: forbidden
  - remaining gap: publication/revision semantics and contract/PIT review; PDF excluded
  - next_gate: reviewed historical-universe, publication, revision, and contract policy; no Raw reacquisition

- [x] Credit / CD
  - terminal: `RAW_BACKFILL_COMPLETE`
  - evidence: `data/landing/diagnostics/pykrx_fundamentals_pilot/20260815T014619Z_1b5475a406fa43df9436237741c9c25f/`
  - coverage: AA- 3Y and BBB- 3Y 2002-01-04..2026-08-12 (6,218 rows each); CD91 same endpoints (6,111 rows); 18,547 Raw rows
  - calls: 42 successful business / 47 raw HTTP, retry 0; 14 non-overlapping <=730-day scopes per series. A prior stopped setup run made two business requests against one over-broad scope (one empty body retained, the second body not retained after a filename-collision stop) and was not resumed or used as evidence
  - PIT status: representative-yield methodology and publication/revision timing unresolved
  - normalized status: forbidden
  - remaining gap: formal unit/methodology and publication-vintage policy
  - next_gate: official semantics evidence and reviewed contract before Normalized; BOK remains primary for government tenors

Rules: one KRX stream, shared lock, retry 0, Landing-first. No commit or push.
