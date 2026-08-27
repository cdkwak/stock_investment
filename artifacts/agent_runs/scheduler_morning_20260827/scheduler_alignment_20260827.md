# Scheduler alignment validation — 2026-08-27

Status: `PARTIAL_PENDING_MORNING_NATURAL_RUNS`

The confirmed scheduler coverage defect was inside the existing 20:30 Korean
market bundle, not a missing Windows task. `KOSPI200_BREADTH_DAILY` is now the
second child, immediately after `CANONICAL_EQUITY_DAILY`. The current bundle
receipt contract is version 4: it preserves breadth and adds the descriptive,
Landing-only `KR_EQUITY_FUNDAMENTAL_CURRENT_OBSERVATION` after 09:10 index
valuation. Bounded older receipt compatibility remains time-limited.

No Windows task was added for breadth. One Windows definition was repaired:
`STOCK_PROJECT_ISSUE_STATE_SYNC` now matches the registration contract for
battery start, battery continuation, wake-to-run, overlap, time limit, action,
working directory, and 06:45 trigger. Only definition hashes are retained;
principal identity is not recorded.

## Coverage and validation

- All 31 automation-enabled datasets map to 13 scheduler lanes; unrouted lanes: 0.
  `us_treasury_spread_daily` was already rebuilt and receipted by `FRED_DAILY`;
  its stale typed-universe disabled flag is now corrected.
- Release-readiness definition coverage increased from 10 to all 12 active Data
  definitions, including the evidence-only BOK task and Toss read-only account task.
- Live definition check: 12/12 matched, missing 0, disabled 0, definition
  mismatches 0. Yahoo's natural 03:32 result returned to 0.
- Retained result check: passed after the 03:32 Yahoo recovery.
- Latest due-occurrence check: 8/9 task groups passed. The prior Toss account
  outcome is the only remaining failure pending its first 07:00 natural run.
- Earlier combined scheduler/orchestration/Issue-State regression: 266 passed.
- Current Yahoo plus release-readiness regression: 151 passed.
- Current scheduler/universe/release regression: 206 passed.
- Current GUI/Health/release regression: 133 passed.
- Current fallback regression: 47 passed and 1 optional integration skipped.
- Consolidated scheduler/universe/Health/GUI/release/fallback regression: 253
  passed.
- Exact provider-scheduler CLI and bundle-routing regression: 53 passed.
- Issue-State tests: 88 passed.
- KR bundle registration dry-run: passed; the three Windows actions remain unchanged.
- Agent-initiated provider calls and broker mutations: 0. The natural 03:02
  scheduler occurrence made its contracted 17 Yahoo calls and retained Landing.

The gate includes an exact ninth Toss account outcome and does not let a Yahoo
failure double-count as a KR failure. The 03:38 KST provider-free rerun passed
Health body/receipt reconciliation, GUI worker quiescence, all ten page
contracts, 31/31 managed Health rows, and protected-data identity. It failed
only the due-outcome subgate because the prior Toss account occurrence remains
terminal pending the 07:00 natural run.

The 03:02 Yahoo receipt has 16 successful routes and one
`SP500_CURRENT_60M` fallback invariant. All 17 immutable Landing responses are
present, the prior S&P 500 value was preserved, and replay of that exact response
against a copied state passed. A bounded invariant reason code was added for a
repeat, but the 03:32 natural occurrence accepted all 17 routes with retry 0 and
history writes 0; the invariant did not recur.

The exact active automatic fallback set remains intentionally narrow. FRED
`VIXCLS` may use the FinanceDataReader parser only for a typed direct-parser
`SCHEMA_ERROR` against the same FRED upstream. Two Korean regular-close routes
(`000660`, `005930`) are implemented and tested display-only but are not
scheduler-activated. Every other source pair remains no-fallback until the
economic meaning, universe, session, date, units, PIT/finality, license, and
atomic recovery gates pass. See `dataset_scheduler_coverage_20260827.md`.

## Pending observation

The natural 06:00–09:10 occurrences still require monitoring. The first
contract-v4 09:10 occurrence and later 20:30 occurrence are not yet observed.
