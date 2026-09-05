# BOK ECOS Korean daily market-rates result

Completed 2026-09-06 KST. The API credential was loaded only into process memory. It is absent from Landing responses/manifests/ledgers, state, Normalized data, source, tests, docs, and this receipt; every retained request route uses `<redacted>`.

## ECOS identity resolution

The one credentialed `StatisticItemList/.../721Y001` response returned 90 rows. Relevant exact public metadata was:

- `7020000` — `회사채(3년, AA-)` — cycles A/M/Q, unit `연%`
- `1010000` — `무담보콜금리(1일)` — cycles A/M/Q, unit `연%`
- `1020000` — `무담보콜금리 전체` — cycles A/M/Q, unit `연%`

`721Y001/D` returned `INFO-200` for bounded 1997 checks, so it cannot supply the requested daily dataset. Bounded ECOS `StatisticSearch` responses identified the actual daily table and exact series used for collection:

- `817Y002/D/010300000` — `회사채(3년, AA-)` — `CORP_BOND_3Y_AA_MINUS`
- `817Y002/D/010101000` — `콜금리(1일, 전체거래)` — `CALL_RATE_OVERNIGHT`

Both remain separate rows keyed by `(date, series)` and are never spliced into a Treasury series.

## Files changed

- `src/stock_data/providers/bok_ecos_market_rates_daily.py:1` — fail-closed provider; metadata evidence at line 45, exact daily series at line 51, parser at line 143, Landing-first capture at line 271.
- `src/stock_data/contracts/bok_ecos_market_rates.py:6` — v1 Normalized contract.
- `src/stock_data/contracts/registry.py:50` — contract import; registration at line 119 and duplicate-count guard at line 126.
- `scripts/manual/collect/backfill_bok_ecos_market_rates.py:51` — window planner; verification at line 179, resumable backfill at line 194, CLI at line 285.
- `src/stock_data/orchestration/dataset_universe.py:474` — daily classification; retained coverage at line 610, status-only/manual exclusion at lines 821/839, preserved-health reason at line 974.
- `src/stock_data/orchestration/daily_operations.py:2307` — typed daily-universe count only; no operation or lane added.
- `tests/unit/providers/test_bok_ecos_market_rates_daily.py:41` — synthetic-only parse, rejection, Landing/redaction, and valid-empty tests.
- `tests/unit/contracts/test_bok_ecos_market_rates.py:30` — registry/schema/key validation.
- `tests/unit/contracts/test_contract_registry.py:31` — updated contract count.
- `tests/unit/orchestration/test_bok_ecos_market_rates_backfill.py:8` — bounded contiguous window planning tests.
- `tests/unit/orchestration/test_daily_operations.py:272` — 95-row manual universe counts; daily-count test at line 609.
- `tests/unit/orchestration/test_reconcile_daily_health_artifact.py:99` — 95-row projection and preserved/static coverage counts.
- `tests/unit/gui/test_gui_health.py:90` — 95-row/74-daily health count assertions.
- `tests/unit/web/test_data_page.py:366` — `kpi_total=95`.
- `docs/data/SOURCE_REGISTRY.md:67` — source row.
- `docs/data/DATASET_INDEX.md:143` — dataset row.
- `docs/data/operations/BOK_ECOS_MARKET_RATES.md:1` — source identity, backfill, availability, and semantic boundary runbook.
- `artifacts/results/bok_ecos_market_rates_result.md:1` — this receipt.

Generated data/state:

- `data/landing/bok_ecos/kr_market_rates_daily/` — 74 immutable responses, manifests, and redacted call ledgers.
- `data/normalized/bok_ecos_kr_market_rate_daily/year=1995/` through `year=2026/` — 32 Hive-style year partitions.
- `data/state/bok_ecos_kr_market_rate_daily_backfill.json` — 74 completed window keys and per-series summary.

## Backfill result

| Series | First date | Last date | Rows |
|---|---:|---:|---:|
| `CORP_BOND_3Y_AA_MINUS` | 1995-01-03 | 2026-09-04 | 8,042 |
| `CALL_RATE_OVERNIGHT` | 1995-01-03 | 2026-09-03 | 8,041 |

Total: 16,083 rows. Initial execution used 37 windows per series and 74 API calls with at least 0.55 seconds between calls. Immediate same-range replay used 0 API calls and skipped all 74 checkpointed windows. Independent read-back found 32 partitions, 74 Landing responses, 74 checkpoint keys, both required series, valid contract types/order/keys, and no credential bytes.

## Health/operations registration

Registration was done only as `MANUAL_ONLY / MANUAL_GATE / PRESERVED`, with status-only GUI visibility. It was intentionally excluded from the 50-row executable operations registry. No `LaneReadiness`, `AUTO_READY`, scheduler lane/task, `_READY_WITH_LIMITS_IDS`, or `_AUTO_ENABLED_IDS` entry was added; the existing automation-enabled count remains 52.

## Tests

- `.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests/unit/providers/test_bok_ecos_market_rates_daily.py tests/unit/contracts/test_bok_ecos_market_rates.py tests/unit/contracts/test_contract_registry.py tests/unit/orchestration/test_bok_ecos_market_rates_backfill.py` — 25 passed.
- `.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests/unit/orchestration/test_daily_operations.py tests/unit/orchestration/test_reconcile_daily_health_artifact.py tests/unit/gui/test_gui_health.py tests/unit/web/test_data_page.py` — 140 passed, 7 existing dependency deprecation warnings.
- `.venv\Scripts\python.exe scripts/manual/collect/backfill_bok_ecos_market_rates.py --project-root . --start 1987-01-01 --verify-only` — verification passed.
- `git diff --check` — passed (line-ending conversion notices only).

## Open questions

- ECOS metadata/search responses do not expose a publication clock, revision freeze, or vintage timestamp; predictive/PIT use remains blocked.
- A historical BOK footnote says the corporate benchmark used A+ unsecured bonds through 2000-09 and AA- unsecured bonds from 2000-10, while current daily API rows label the entire 1995-present series `회사채(3년, AA-)`. The pre-2000 methodology boundary must remain explicit and should be resolved before treating the series as homogeneous AA- history.
- Literal `721Y001/D` support would require a provider-side metadata change; as of this run it returns no daily data. The collected dataset therefore uses the verified daily table `817Y002`.
