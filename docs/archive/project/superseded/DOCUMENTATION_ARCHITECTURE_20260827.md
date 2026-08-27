# Documentation Architecture

Status: **SUPERSEDED 2026-08-27**. Current routing and ownership are defined by
`AGENTS.md` and `docs/project/REPOSITORY_MAP.md`; this file is retained only as
migration history.

This is the reference classification and migration map for repository documents.
It describes ownership and placement; it is not a current project or domain status.

## Official routing tree

```text
AGENTS.md
  -> docs/project/PROJECT_STATUS.md
       -> docs/data/DATA_STATUS.md           (Data task)
       -> docs/backtest/BACKTEST_STATUS.md   (Backtest task)
       -> docs/gui/GUI_STATUS.md              (GUI preparation or implementation task)
```

After the domain status selects a task, read only its linked contract,
checkpoint/state, evidence, and applicable runbook. Roadmaps, repository maps,
indexes, audits, provider guides, queues, review-required documents, and archives
do not independently own current state or authorize execution.

Do not recursively read all documentation at task start. Do not read
`docs/archive/**` by default; open an archived document only when an active
authority directly references it or a specific historical-evidence question
requires it.

## Domain-first physical architecture v1

- The top-level document domains are `project/`, `data/`, `backtest/`, `gui/`, and
  `archive/`. The GUI Domain owns its implemented runtime state, source-selection
  references, and future GUI work; Project Status alone decides whether GUI is the
  selected project domain.
- Classify a document by domain first, then by role inside that domain. Provider,
  runbook, audit, and architecture are roles, not repository-wide top-level domains.
- A Domain STATUS owns whether a procedure is currently executable; directory
  placement alone never authorizes execution.
- Every executable procedure under `docs/data/operations/` must be linked
  directly from `docs/data/DATA_STATUS.md`, and every procedure linked for
  execution by that status must exist under its Domain operations directory.
  The only non-routed placement exceptions are the two code-path-bound terminal
  readiness manifests and the accepted account privacy boundary named below;
  none is executable authority.
- Documents under `docs/archive/**` are evidence only and cannot act as current
  instructions.
- Unapproved candidate work belongs in `docs/data/queues/`, not in runbooks.
- Completed procedures move to
  `docs/archive/data/operations/2026-08-data-phase/completed/`; procedures replaced
  by a newer one move to the adjacent `superseded/` directory.
- Move documents only for a functional ownership or routing need, never merely
  for visual reorganization.
- This structure is frozen after the v1 migration. Reopen physical architecture
  only when a new Domain or durable responsibility actually exists.

## Classification

| Class | Included documents | Rule |
|---|---|---|
| `ACTIVE` | `PROJECT_STATUS.md`, Domain STATUS files, and status-routed Domain operations | Current authority or executable procedure |
| `REFERENCE` | `PROJECT_ROADMAP.md`, `REPOSITORY_MAP.md`, `docs/data/DATASET_INDEX.md`, inventories, contracts, provider/data audits, API guides, official samples, examples, closed queues | Detail or evidence; never current routing authority |
| `ARCHIVED` | `docs/archive/`, including Domain `audits/`, `handoffs/`, and completed `operations/` | Completed handoff, closed record, or historical procedure; read-only by default |
| `SUPERSEDED` | Domain paths under `docs/archive/**/superseded/` | Replaced by a named current authority/procedure; never use for current work |
| `TEMPORARY` | None currently accepted under `docs/` | Temporary output belongs outside authoritative docs and must name its owner/expiry |
| `REVIEW_REQUIRED` | Status-routed review gates or retained candidates under `docs/data/queues/` | The filename is not authority; an unselected candidate must not execute |

Directory-level coverage:

| Path | Classification | Owner / authority |
|---|---|---|
| `docs/project/PROJECT_STATUS.md` | ACTIVE | Lead only |
| `docs/project/PROJECT_GOAL.md` | REFERENCE | User-owned durable objective and goal-to-Inbox planning contract; never execution authority |
| `docs/data/DATA_STATUS.md` | ACTIVE | Data domain current state and execution routing |
| `docs/backtest/BACKTEST_STATUS.md` | ACTIVE | Backtest domain current state and execution routing |
| `docs/gui/GUI_STATUS.md` | ACTIVE | Implemented GUI state, runtime boundaries, and future GUI-domain routing |
| `docs/gui/` reference documents | REFERENCE | Dashboard source maps, coverage audits, and routing decisions; never collector execution authority |
| `docs/project/PROJECT_ROADMAP.md` | REFERENCE | Long-term architecture/prioritization only |
| `docs/project/REPOSITORY_MAP.md` | REFERENCE | Location and ownership map |
| `docs/project/PROJECT_OPERATIONS_MAP.md` | REFERENCE | Visual scheduler/runtime relationship map and live-status command hub; never current authority |
| `docs/project/DOCUMENTATION_ARCHITECTURE.md` | REFERENCE | Frozen documentation structure and ownership |
| `docs/data/DATASET_INDEX.md` | REFERENCE | Dataset/source/path/collector index |
| `docs/data/inventory/` | REFERENCE | Point-in-time inventory definitions/evidence |
| `docs/data/sources/` | REFERENCE | Provider/API guides, official samples, and source evidence |
| `docs/data/research/` | REFERENCE | Current Data investigations and analysis |
| `docs/data/queues/` | REFERENCE / NON-EXECUTABLE | Candidate, blocked, or promotion-gated operation designs; activate only through Data Status and a current operation |
| `docs/data/operations/` | ACTIVE | Procedures directly routed by Data Status only |
| `docs/archive/data/operations/**/completed/` | ARCHIVED | Completed or one-off procedures |
| `docs/archive/data/operations/**/superseded/` | SUPERSEDED | Procedures replaced by newer controls |
| `docs/archive/<domain>/handoffs/` | ARCHIVED | Completed Domain handoffs |
| `docs/archive/<domain>/superseded/` | SUPERSEDED | Replaced controls/status/backlogs |

## Final physical migration map

| Previous path | Current path | Class | Reason |
|---|---|---|---|
| `docs/project/DATA_STATUS.md` | `docs/data/DATA_STATUS.md` | ACTIVE | Data Domain root owns current Data state |
| `docs/project/BACKTEST_STATUS.md` | `docs/backtest/BACKTEST_STATUS.md` | ACTIVE | Backtest Domain root owns current Backtest state |
| `docs/providers/`, `docs/kbsec/`, `docs/api_guides/`, `docs/krx_data/` | `docs/data/sources/<provider>/` | REFERENCE | Provider evidence belongs to Data |
| `docs/runbooks/data/` | `docs/data/operations/` | ACTIVE | Data Status owns execution authority |
| `docs/runbooks/review_required/DIVIDEND_OBSERVATION_APPEND.md` | `docs/data/operations/DIVIDEND_OBSERVATION_APPEND.md` | ACTIVE | Reusable offline append procedure is directly routed by Data Status; dated results moved to archive evidence |
| `docs/data/operations/KRX_*_RAW_DAILY*_REVIEW_REQUIRED.md` candidates | `docs/data/queues/` | REFERENCE / NON-EXECUTABLE | Three unselected KRX Raw incremental designs are indexed outside active operations |
| `docs/data/operations/LOCAL_DATA_BACKUP_RESTORE.md` | `docs/data/queues/LOCAL_DATA_BACKUP_RESTORE.md` | REFERENCE / NON-EXECUTABLE | Production backup selection and restore promotion remain gated |
| `docs/data/operations/NAVER_EQUITY_UR199_NEXT_SESSION.md` | `docs/archive/data/operations/2026-08-data-phase/completed/NAVER_EQUITY_UR199_NEXT_SESSION.md` | ARCHIVED | The exact 2026-08-24 window is expired terminal evidence |
| `docs/kbsec/token_access_pilot.md` | `docs/archive/data/operations/2026-08-data-phase/superseded/KBSEC_TOKEN_ACCESS_PILOT.md` | SUPERSEDED | Retired flat-envelope failure preserved as evidence; corrected nested OAuth is current |
| `docs/kbsec/snapshot_contract.md` | `docs/archive/data/superseded/KBSEC_SNAPSHOT_CONTRACT_PROVISIONAL.md` | SUPERSEDED | Common-date provisional contract replaced by the per-slice current semantic boundary |
| `docs/data/audits/` | `docs/data/research/` | REFERENCE | Current investigations remain Data references |
| Completed/superseded runbooks and audits | `docs/archive/data/` by role | ARCHIVED / SUPERSEDED | Historical evidence is Domain-owned |
| `docs/architecture/DOCUMENTATION_ARCHITECTURE.md` | `docs/project/DOCUMENTATION_ARCHITECTURE.md` | REFERENCE | Cross-project architecture belongs to Project |

## Intentionally retained classifications

- Provider audits, official binary guides, KB JSON samples, and KRX manual CSVs
  are retained without content changes under `docs/data/sources/`.
- The current KB semantic boundary is
  `docs/data/sources/kb/SNAPSHOT_CONTRACT.md`. The former common-date contract and
  flat-envelope token pilot are preserved only as superseded evidence.
- Review-required candidate designs are indexed by
  `docs/data/queues/README.md` and are excluded from normal agent startup.
- `docs/data/operations/DASHBOARD_CURRENT_READINESS_UR233.md` and
  `DASHBOARD_CURRENT_READINESS_UR242.md` are consumed terminal manifests whose
  exact paths remain runtime identity inputs. Moving them would break retained
  replay identity, so they stay in place but are never current instructions.
- `docs/data/operations/ACCOUNT_LOCAL_PRIVACY_BOUNDARY.md` is an accepted
  non-executable boundary retained in place until an Account domain or durable
  cross-domain owner is selected. It grants no refresh, order, or transfer
  authority and is excluded from normal startup.
- `docs/gui/GUI_STATUS.md` is the single GUI Domain entry point. It records the
  implemented local read-only Dashboard/Index MVP and routes the Data Map,
  Provider Coverage, and Daily Source Routing references without authorizing
  provider calls or Data mutation.
- No Data technical state, contract, artifact, Raw, checkpoint, or ledger was
  changed by this documentation migration.
