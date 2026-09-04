# Repository Map

This is the location and ownership map for Stock Investment Rev1. It answers
"where does this belong?" It does not replace [Project Status](PROJECT_STATUS.md),
[Data Status](../data/DATA_STATUS.md), Dataset Contracts, checkpoints, or active runbooks.

## Major tree

```text
stock_investment_rev1/
|-- AGENTS.md                         agent entry rules and evidence priority
|-- README.md                         setup and control-document links
|-- pyproject.toml                    Python package and test configuration
|-- app.py                            retired PySide6 entry-point stub (unsupported)
|-- .agents/skills/                   repository-local agent procedures
|-- artifacts/
|   |-- request_queue/                canonical file-backed work queue and Board
|   `-- ...                           bounded analysis, GUI, benchmark, and validation outputs
|-- src/stock_data/                   current Data implementation plus local GUI
|   |-- contracts/                    dataset schema, key, layer, source
|   |-- providers/                    provider-specific clients and parsing
|   |-- pipelines/                    Landing-to-Normalized workflows
|   |-- derived/                      deterministic calculated datasets
|   |-- published/                    downstream publication builders
|   |-- storage/                      atomic persistence primitives
|   |-- validation/                   data and provenance validation
|   |-- orchestration/                bounded workflow coordination
|   |-- audit/                        read-only evidence builders
|   |-- migrations/                   explicit schema/data migrations
|   `-- gui/                          retained web-imported read-only services
|-- src/market_features/              deterministic PIT feature contracts/builders
|-- src/market_backtest/              offline labels, splits, signals, diagnostics
|-- src/runtime_diagnostics/          strict local GUI/Backtest diagnostic events/store
|-- scripts/
|   |-- run_data_v1.py                supported regular Data v1 entry point
|   |-- manual/                       bounded operator/research tools
|   |   |-- audit/                    read-only evidence and retained-state audits
|   |   |-- backfill/                 bounded historical/manual backfills
|   |   |-- build/                    offline deterministic builders and promotions
|   |   |-- collect/                  bounded source captures and daily entry points
|   |   |-- diagnostic/               bounded source/access diagnostics and smoke tools
|   |   |-- migration/                explicit legacy/schema migrations
|   |   |-- pilot/                    retained bounded source pilots
|   |   |-- repair/                   deterministic retained-data repair/rebuild tools
|   |   `-- research/                 non-operational analysis and source probes
|   `-- maintenance/                  repository maintenance/read-only inventory
|-- tests/                            offline tests and retained small fixtures
|   |-- fixtures/                     reusable sanitized test inputs
|   |-- historical/                   historical acquisition and migration behavior
|   |-- integration/{pipelines,daily_operations}/ workflow-boundary tests
|   |-- regression/{provider,semantics,data}/ retained failure and evidence regressions
|   `-- unit/{contracts,providers,orchestration,storage,validation,derived,gui,features,backtest}/ focused unit tests
|-- docs/
|   |-- README.md                     bounded documentation router
|   |-- project/                      user goal, project status, scheduler status, roadmap, maps
|   |-- data/                         Data entrypoints, active research, and authorized operations
|   |-- backtest/                     Backtest status and Phase-owned documents
|   |-- gui/                          GUI status and Dashboard source/runtime decisions
|   `-- archive/                      Domain-owned historical evidence; never active
`-- data/                             local artifacts; ignored by Git
    |-- landing/                      lossless provider capture
    |-- raw/                          optional contract-shaped lossless source-field projection
    |-- normalized/                   contract-validated source datasets
    |-- derived/                      reproducible calculations
    |-- published/                    canonical downstream interfaces
    |-- state/                        checkpoints, locks, ledgers, audit snapshots
    |-- staging/                      incomplete/pre-publication work
    |-- quarantine/                   rejected or blocked artifacts
    `-- smoke*/test_tmp*/             generated diagnostic/test output, when present
```

Hidden/generated roots such as `.git/`, `.venv/`, `.pytest_cache/`, `__pycache__/`,
and `.worktrees/` are deliberately omitted from the working tree above.

## Ownership and modification boundaries

| Location | Role | Producer | Consumer | Modification rule | Class | Primary references |
|---|---|---|---|---|---|---|
| `AGENTS.md` | Agent routing, safety, evidence order | Lead/user | Every agent | Edit deliberately; user instruction remains highest | ACTIVE CONTROL | [Project Status](PROJECT_STATUS.md) |
| `.agents/skills/request-queue/` | Canonical queue lifecycle procedure | Lead/user | Queue-backed workers and reviewers | Use before reading or changing `artifacts/request_queue/`; queue state changes only through the manager script | ACTIVE INSTRUCTION | [Queue Board](../../artifacts/request_queue/BOARD.md) |
| `.agents/skills/goal-inbox-planner/` | Goal-to-Status comparison and planning-only Inbox discovery | Lead/user | Designated planning agent | User owns Goal; skill may create only deduplicated `inbox/new` discoveries through the canonical queue manager | ACTIVE INSTRUCTION | [Project Goal](PROJECT_GOAL.md) |
| `artifacts/request_queue/` | File-backed Inbox, work states, review submissions, and compacted Done history | Queue manager | Agents and user | Follow the request-queue skill; do not edit state files by hand | ACTIVE OPERATIONAL STATE | [Queue Board](../../artifacts/request_queue/BOARD.md) |
| `docs/README.md` | Bounded documentation entry route and document-role guide | Lead | Humans and agents | Keep concise; it routes to Status and never duplicates domain state | ACTIVE ROUTER | [Documentation Router](../README.md) |
| `docs/project/` | User-owned goal, cross-project status, scheduler inventory, roadmap, and repository routing | User / lead | Humans and agents | Keep this root compact; Domain facts and detailed evidence stay with their Domain or archive owner | ACTIVE CONTROL / REFERENCE | [Project Goal](PROJECT_GOAL.md), [Project Status](PROJECT_STATUS.md), [Roadmap](PROJECT_ROADMAP.md) |
| `docs/project/SCHEDULER_STATUS.md` | Current Windows task inventory, definition drift, and consolidation target | Lead / maintainers | Humans and agents | Record installed scheduler structure only; GUI refresh projection and Data outcome truth remain with their owning contracts/status | CURRENT STATUS | [Project Status](PROJECT_STATUS.md), [GUI refresh contract](../gui/GUI_REFRESH_STATUS_CONTRACT.md) |
| `docs/backtest/` | Current Backtest state and future Backtest-owned documents | Backtest owner | Humans and agents | Backtest Status owns current Backtest routing | ACTIVE CONTROL | [Backtest Status](../backtest/BACKTEST_STATUS.md) |
| `docs/gui/` | GUI implementation status, Dashboard data map, coverage audit, and daily source routing | GUI owner / lead | Humans and current GUI services | GUI Status owns GUI-domain state; reference documents select sources but never authorize provider calls, Data mutation, or predictive use | ACTIVE CONTROL / REFERENCE | [GUI Status](../gui/GUI_STATUS.md) |
| `docs/gui/DAILY_MARKET_SUMMARY_CONTRACT.md` | Deterministic Korean daily summary and compact Telegram projection | GUI owner | Future local summary consumer | Closed documentation contract; registry revision 1 is `NO_OUTPUT` until exact `MARKET_STATE` binding; no runtime/provider/account/trading authority | REFERENCE CONTRACT | [GUI Status](../gui/GUI_STATUS.md) |
| `docs/data/{DATA_STATUS,DATASET_INDEX,SOURCE_REGISTRY,SOURCE_FALLBACK_POLICY}.md` | Routine Data routing, dataset navigation, provider roles, and fallback gate | Data owner | Humans and agents | Read in the order selected by Data Status; only Status authorizes routing | ACTIVE CONTROL / INDEX | [Data Status](../data/DATA_STATUS.md) |
| `docs/data/SCHEDULER_DATA_MAP.md` | Stable Windows task to logical lane to dataset relationship, including automation-disabled dispositions | Data owner | Humans, agents, scheduler maintainers | Reference map only; live task state remains in Scheduler Status and dataset eligibility remains in Data Status/contracts | ACTIVE REFERENCE MAP | [Data Status](../data/DATA_STATUS.md), [Scheduler Status](SCHEDULER_STATUS.md) |
| `docs/data/SOVEREIGN_YIELD_BOND_ETF_SEMANTICS.md` | Yield/price, curve, equity-linkage, and bond-ETF semantic boundary | Data owner | Data research, GUI, Backtest | Preserve distinctions; no source promotion, predictive use, or execution authority by itself | REFERENCE CONTRACT | [Data Status](../data/DATA_STATUS.md) |
| `docs/data/research/active/` | Unresolved semantic, PIT, entitlement, and source questions | Data investigations | Contracts/status/runbooks | Keep `REVIEW_REQUIRED` work here; it is not an operation instruction | ACTIVE RESEARCH | [Data Status](../data/DATA_STATUS.md) |
| `docs/data/queues/` | Retained candidate, blocked, or promotion-gated operation designs | Data planning | Future Status decisions | Reference only; presence never authorizes execution | REFERENCE / NON-EXECUTABLE | [Candidate queue index](../data/queues/README.md) |
| `docs/data/operations/` | Reusable current procedures and exact runtime identity documents | Domain owner | Operator/agent | Follow only after Data Status selects the task; a runtime identity document is not a scheduler job | ACTIVE INSTRUCTION | [Data Status](../data/DATA_STATUS.md) |
| `docs/archive/data/evidence/` | Closed audits, pilots, backfills, source samples, queues, and immutable inventories | Completed work | Audit/history | Preserve; never use as routing or execution authority | EVIDENCE | [Data Status](../data/DATA_STATUS.md) |
| `docs/archive/<domain>/**/superseded/` | Replaced Domain documents | Historical owner | Evidence only | Never execute; use the replacing status/queue/collector | SUPERSEDED | [Project Status](PROJECT_STATUS.md) |
| `docs/archive/<domain>/` | Completed audits, handoffs, operations, and historical evidence | Completed work | Audit/history | Read-only by default; never an active instruction | ARCHIVE | [Project Status](PROJECT_STATUS.md) |
| `docs/archive/<domain>/status_history/` | Pre-compaction Status snapshots | Lead/domain owner | Targeted historical audit only | Evidence only; preserved relative links may not resolve from the archive | ARCHIVED STATUS | [Documentation Router](../README.md) |
| `docs/archive/project/audits/repository_usage_20260815/` | Dated static repository usage audit and CSV detail | Repository audit | Cleanup work requiring historical reference evidence | Consult before destructive cleanup; it is evidence only and never current authority | ARCHIVED AUDIT | [Project Status](PROJECT_STATUS.md) |
| `src/stock_data/contracts/` | Dataset meaning, schema, key, layer | Data-domain code | Storage, validation, pipelines | Schema changes require explicit scope and tests | ACTIVE SOURCE | [Registry](../../src/stock_data/contracts/registry.py) |
| `src/stock_data/providers/` | Provider transport and parsing | Provider adapters | Pipelines | Do not leak provider formats downstream | ACTIVE SOURCE | [Source Registry](../data/SOURCE_REGISTRY.md) |
| `src/stock_data/pipelines/` | Collection/normalization workflows | Application code | Scripts and tests | Preserve Landing-first and atomic-write rules | ACTIVE SOURCE | [Data Status](../data/DATA_STATUS.md) |
| `src/stock_data/{derived,published}/` | Calculated and downstream datasets | Builders | Research, GUI, and future Backtest inputs | Deterministic inputs and contracts required | ACTIVE SOURCE | [Dataset Index](../data/DATASET_INDEX.md) |
| `src/market_features/` | Versioned PIT feature definitions and deterministic builders | Backtest/feature owner | Offline research and Backtest | Frozen validated local inputs only; no provider, promotion, or GUI behavior | ACTIVE SOURCE | [Feature Contract](../backtest/FEATURE_CONTRACT.md) |
| `src/market_backtest/` | Labels, purged walk-forward, models, historical fills, portfolio simulation, reporting, and diagnostics | Backtest owner | Result artifacts and typed services | Network-free and PIT-safe; version new model/fill/accounting boundaries and never reach broker mutation endpoints | ACTIVE SOURCE | [Backtest Architecture](../backtest/BACKTEST_ARCHITECTURE.md) |
| `src/runtime_diagnostics/`, `scripts/maintenance/inspect_runtime_failures.py` | Strict versioned application events, bounded atomic local store, and read-only inspection | Project/GUI/Backtest owners | Agents and local operators | No Data UpdateEvent dependency, raw exception text, private values, external telemetry, or workflow-outcome changes | ACTIVE TOOLING | [Runtime log contract](../../artifacts/runtime_logs/README.md) |
| `src/stock_data/{storage,validation,audit}/` | Shared persistence and verification | Data infrastructure | All Data workflows | Focused changes; broad regression risk | ACTIVE SOURCE | [Data Status](../data/DATA_STATUS.md) |
| `src/stock_data/orchestration/{daily_operations,dataset_universe}.py` | 37-row executable health/operations registry and 80-row orthogonal multi-axis, non-executable full-universe catalog | Data orchestration | Health, scheduler planning, inventory reconciliation, future GUI filters | Universe membership never grants operation authority; deprecated `primary_classification` is compatibility-only and automation remains an explicit separate gate | ACTIVE SOURCE | [Dataset Index](../data/DATASET_INDEX.md) |
| `src/stock_data/gui/` | Historical package boundary for web-imported read-only query/services | GUI owner | `src/stock_web` | Keep surviving service imports stable; no provider calls or Data mutation | ACTIVE SOURCE | [GUI Status](../gui/GUI_STATUS.md) |
| `app.py` | Retired PySide6 entry-point stub | None | Historical reference only | Unsupported; do not restore Qt dependencies | RETIRED SOURCE | [GUI Status](../gui/GUI_STATUS.md) |
| `scripts/run_data_v1.py` | Supported regular Data entry point | Maintainer/operator | Data pipeline | Normal public/existing-credential Data operations use standing authorization and the current contract/runbook | ACTIVE ENTRYPOINT | [README](../../README.md) |
| `scripts/run_overnight_ml.py`, `src/market_backtest/overnight_ml.py` | At-most-eight-hour, resumable development-only ML study | Backtest owner | Local research operator | Frozen input only; source sliced before holdout; no provider, GUI, portfolio, account, or order authority | ACTIVE BACKTEST TOOLING | [Overnight ML Runbook](../backtest/OVERNIGHT_ML_RUNBOOK.md) |
| `scripts/maintenance/run_release_readiness_smoke.py` | Supported offline daily release-readiness entry point | Maintainer/operator | FastAPI web routes, typed Health, scheduler status, release report | Read-only by default; optional report only under `artifacts/release_readiness/`; no providers or scheduler mutation | ACTIVE TOOLING | [README](../../README.md) |
| `scripts/maintenance/run_toss_account_snapshot.py` | Supported noninteractive daily Toss read-only account snapshot entry point | Data owner/operator | Windows Task Scheduler and local operator | May mutate only the sanitized account Landing/Normalized/state/occurrence and last-result paths selected by its active runbook; exact selector stays in memory; no discovery, raw response, identifier, cross-currency total, order, transfer, or broker mutation | ACTIVE DATA ENTRYPOINT | [Toss account runbook](../data/operations/TOSS_ACCOUNT_SNAPSHOT_READONLY.md) |
| `scripts/maintenance/telegram_agent_bridge.py`, `.codex/hooks.json` | Sanitized Telegram agent-stop alerts, allowlisted status, agent-interpreted Inbox discovery, and sourced market briefs | Local maintainer | User-owned private Telegram chat | Intake/report agents are read-only and ephemeral; intake may call only canonical queue `discover`; reports use current web research without Data/provider/canonical writes; no arbitrary execution, triage/claim, or workflow-outcome changes | ACTIVE TOOLING | [Codex non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode) |
| `scripts/manual/` | Diagnostics, pilots, migrations, backfills | Authorized operator | Retained evidence/data | Tools may run or be modernized under current Status/runbook; filenames and old approval gates are not authority | BOUNDED TOOLING | [Active runbooks](../data/operations/) |
| `scripts/maintenance/` | Repository-level bounded tooling | Maintainer | Humans/CI | Must not assign semantic status or mutate datasets | ACTIVE TOOLING | `generate_repo_inventory.py` |
| `scripts/maintenance/prune_gui_validation_artifacts.py` | Reference-aware retention for generated GUI acceptance screenshots and diagnostics | GUI maintainer | Humans/agents | Dry-run by default; apply requires the exact reviewed plan digest, rejects drift/malformed paths, uses same-root quarantine and a committed manifest before exact purge, and preserves each successful transaction under the inventory-excluded immutable receipt root | ACTIVE TOOLING | [GUI Status](../gui/GUI_STATUS.md) |
| `tests/` | Offline behavior and regression evidence | Developers | CI/developers | No live API calls in normal tests | ACTIVE TESTS | `pyproject.toml` |
| `data/landing/` | Lossless source captures | Collectors/manual adoption | Normalizers/audits | Append/immutable semantics; never casually edit | RETAINED DATA | [Data flow](../../AGENTS.md) |
| `data/raw/` | Optional contract-shaped lossless projection of source fields with provenance; currently VKOSPI | Explicit dataset promotion | Normalizers/validation; not direct Backtest input | Preserve the dataset-specific Raw contract and Landing reference | RETAINED DATA | [Dataset Index](../data/DATASET_INDEX.md) |
| `data/normalized/` | Stable source-schema Parquet | Pipelines/builders | Derived/published/research | Contract-governed atomic writes only | RETAINED DATA | [Dataset Index](../data/DATASET_INDEX.md) |
| `data/derived/` | Reproducible calculations | Derived builders | Published/research | Rebuild from declared inputs; no source mixing | RETAINED DATA | [Data Status](../data/DATA_STATUS.md) |
| `data/published/` | Canonical downstream datasets | Publication builders | Research, GUI, and future Backtest | Stable interface; atomic publication | RETAINED DATA | [Data Status](../data/DATA_STATUS.md) |
| `data/state/` | Checkpoints, ledgers, locks, immutable audits | Pipelines/audits | Resume, verification, operators | Follow owner-specific atomic/locking rules | OPERATIONAL STATE | [Data Status](../data/DATA_STATUS.md) |
| `data/{staging,quarantine}/` | Unpublished or rejected material | Pipelines/validation | Recovery/audit | Not valid research input | NON-CURRENT DATA | [Data Status](../data/DATA_STATUS.md) |
| `.venv/`, caches, smoke/test temp roots | Reproducible local by-products | Python/tests | Local tooling only | Generated; not project truth | GENERATED | `.gitignore` |
| `artifacts/` | Bounded analysis, GUI review, benchmark, and semantic-validation outputs | Analysis/GUI validation tasks | Humans and targeted review | Not canonical Data or current status; preserve producer-specific meaning | GENERATED / REVIEW OUTPUT | [Project Status](PROJECT_STATUS.md) |
| `artifacts/data_inventory/` | Row-level full Dataset Universe reconciliation snapshots | Bounded offline inventory | Data Status, Dataset Index, maintainers | Generated detail only; typed registry and current Status own meaning | GENERATED / REVIEW OUTPUT | [Dataset Index](../data/DATASET_INDEX.md) |

## Source-of-truth routing

| Question | Current authority |
|---|---|
| What durable outcome has the user selected? | [Project Goal](PROJECT_GOAL.md) |
| What is the project doing now? | [Project Status](PROJECT_STATUS.md), then the routed domain status |
| What queue-backed work exists or is claimed? | [Request Queue Board](../../artifacts/request_queue/BOARD.md), operated only through the request-queue skill and manager |
| What scheduler definitions exist and where are the gaps? | [Scheduler Status](SCHEDULER_STATUS.md); use live Task Scheduler readback and owning receipts for current truth |
| Which scheduler task and lane owns each dataset? | [Scheduler Data Map](../data/SCHEDULER_DATA_MAP.md); confirm eligibility in Data Status and live state in Scheduler Status |
| What does the GUI show about freshness and next refresh? | [GUI refresh-status contract](../gui/GUI_REFRESH_STATUS_CONTRACT.md), then [GUI Status](../gui/GUI_STATUS.md) |
| Where does a component belong? | This Repository Map |
| Which datasets exist and how are they reached? | [Dataset Index](../data/DATASET_INDEX.md) |
| What does a dataset mean? | Its Dataset Contract in `src/stock_data/contracts/` |
| What exact local artifacts were observed? | Archive evidence inventory and its linked immutable snapshot; never use it as current routing |
| What is the current GUI state and source routing? | [GUI Status](../gui/GUI_STATUS.md), then only its linked Dashboard reference needed by the task |
| May an operation be run now? | Current status plus an applicable file in `docs/data/operations/` |
| Does a file under `docs/data/queues/` authorize work? | No; it is a non-executable candidate until Data Status selects a current operation |

Archive, closed audits, old handoffs, blocked queues, superseded runbooks, manual scripts, and retained
checkpoints can explain history. None of them independently authorizes current work or
overrides a current status document.

## Generated location inventory

For a bounded names-only view, run:

```powershell
.\.venv\Scripts\python.exe .\scripts\maintenance\generate_repo_inventory.py
```

The command prints to stdout, stops Data traversal after the layer directories, does
not read data payloads, and labels its output `GENERATED_LOCATION_INVENTORY_NOT_STATUS`.
It does not update this document or any human-authored meaning/status.
