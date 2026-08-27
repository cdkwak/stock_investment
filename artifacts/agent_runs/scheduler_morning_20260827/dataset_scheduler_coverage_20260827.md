# Dataset scheduler and fallback coverage — 2026-08-27

Status: `CURRENT_AUDIT / MORNING_NATURAL_RUNS_PENDING`

“Daily update” means one bounded attempt at the dataset's own eligible market or
provider-publication occurrence. It does not mean calling every dataset on every
calendar day. Holidays, weekly publications, event-driven datasets, valid-empty
provider lag, static retained segments, and unavailable accounts remain distinct.

## Universe disposition

| Current disposition | Count | Handling |
|---|---:|---|
| Automation enabled | 31 | All map to 13 existing provider/dependency lanes; unrouted lanes 0 |
| Implementation/finality candidates | 7 | Keep inside existing bundles after their own gates close; do not create seven Windows tasks |
| Manual/research/event/account snapshot | 20 | Five are event-driven, seven separate KB market-snapshot rows remain behind semantic/recovery gates, and the rest retain manual or research boundaries |
| Blocked contract/semantics | 8 | Continue bounded evidence work; do not promote unsupported values |
| Not applicable/static/superseded | 14 | No refresh; includes nine static-complete segments and five intentional aliases/superseded rows |
| **Total** | **80** | |

KB account data is outside this 80-row market-dataset universe. A configured KB
account does exist: the three approved runtime settings are present, provider-
free construction reports `ENABLED`, and an identifier-free `SSQM2952` snapshot
was live-validated on 2026-08-26. Its separate read-only daily route is installed
for 07:10; it is not one of the 80 market rows. The first natural occurrence is
pending.
The seven `kb_*_snapshot` rows inside the 80 are separate broker market
snapshots, not holdings, balances, orders, or another KB account dataset. The
disabled legacy KB market task also remains distinct from the account route.

The 31st managed row is `us_treasury_spread_daily`. It was already rebuilt
atomically as a dependency of the 06:00 `FRED_DAILY` lane and named in that
lane's receipt, but the typed universe incorrectly marked it disabled. The
metadata, retained inventory row, Health projection, GUI regression, and
release-readiness view now agree.

`core_data_20260818.json` intentionally remains the immutable 33-row historical
input. Its four `core_operation_missing` IDs are the four later registry
additions, not unrouted scheduler lanes; current typed metadata plus runtime
coverage projects them into the 80-row Health view. Overwriting the historical
core would erase provenance, so it was not changed.

The seven nearest candidates are:

- `kr_market_liquidity_daily`, `kr_credit_balance_daily`: implementation ready;
  the existing Korean bundle already captures bounded finality observations.
- `kr_short_selling_balance_daily`, `kr_short_selling_investor_daily`,
  `ls_t8462_daily_raw`: their individual finality/valid-empty gates remain open.
- `bok_ecos_kr_treasury_yield_source_observation`, `kr_treasury_yield_daily`:
  source-specific evidence only; BOK and Toss yields are cross-checks, not
  interchangeable fallback values.

## Source fallback coverage

Automatic fallback is fail-closed and source-specific:

```text
primary attempt 1
  -> typed eligible failure
  -> fallback attempt 1 (only an equivalence-approved route)
  -> atomic promotion with selected provider lineage
  -> preserve prior valid value if both paths fail
```

Providers are never averaged, stitched, rescaled, or silently relabelled.

| Route | Current state | Boundary |
|---|---|---|
| `fred_vix_daily:VIXCLS` | `ACTIVE` | Direct FRED parser may fall back only on `SCHEMA_ERROR` to FinanceDataReader reading the same FRED upstream; timeout/HTTP/auth/rate-limit do not cascade |
| `KR_EQUITY_REGULAR_CLOSE:XKRX:{000660,005930}` | `IMPLEMENTED_TESTED_NOT_SCHEDULED` | Display-only pykrx technical-failure fallback to FDR/Naver for the exact symbol/date; never canonical history or Backtest input |
| Every other current dataset route | `NO_AUTOMATIC_FALLBACK` | A second source remains a cross-check until economic meaning, universe, session, date, unit, PIT/finality, license, and atomic recovery all pass |

## Current verification

- 03:32 Yahoo natural occurrence: 17 accepted, 0 failed, 0 retries, 0 history
  writes; the isolated 03:02 S&P 500 invariant did not repeat.
- Health: 80 rows, 31 managed, all 31 `CURRENT` or `EXPECTED_LAG`, actionable
  incidents 0; Health body and API-0 projection receipt are reconciled.
- Release smoke at 04:12 KST: scheduler definitions pass 13/13. Eight of ten due
  groups are complete; Toss and KB account outcomes await their first 07:00 and
  07:10 natural task occurrences.
- Consolidated scheduler/universe/Health/GUI/release/fallback validation plus
  the exact bundle-routing CLI suite: 306 passed. The separate fallback
  integration bundle passed 47 with 1 optional integration skipped.
- Focused KB account/runtime/scheduler/release regression: 212 passed; native
  GUI release integration: 1 passed.
- Agent-initiated provider calls, canonical/history writes, and broker mutations
  for this audit: 0. Scheduler mutations: one new read-only KB definition.

Next evidence comes from the 06:00–09:10 natural sequence, especially the 07:00
Toss account, 07:10 KB account, and 09:10 liquidity/credit and PER/PBR children.
