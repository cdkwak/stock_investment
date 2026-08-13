# Stock Investment Rev1

Local, reproducible market-data layer for normalized source data, derived data,
and downstream published datasets. Data v1 is frozen: changes require explicit
scope and must preserve Dataset Contracts, Parquet data, and checkpoints.

## Environment

- Python: 3.11 or newer; use a project-local `.venv` (currently tested with
  Python 3.13.14)
- Dependencies: [`pyproject.toml`](pyproject.toml)

From PowerShell at the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[test]"
.\.venv\Scripts\python.exe -m pytest
```

Secrets belong only in `.env`; never commit or print them.
[`.env.example`](.env.example) contains variable names without values.

## Data layout

```text
data/
  landing/     lossless provider responses
  normalized/  contract-validated source Parquet
  derived/     reproducible calculations
  published/   canonical downstream datasets
  state/       JSON checkpoints
  quarantine/  rejected inputs, when present
```

Valid Parquet writes use temporary output, read-back validation, and atomic
replacement. Failed or empty responses must not replace valid data.

## Data v1 runner

The only supported CLI entry point is `scripts/run_data_v1.py`.

```powershell
.\.venv\Scripts\python.exe .\scripts\run_data_v1.py --status
.\.venv\Scripts\python.exe .\scripts\run_data_v1.py --no-live --skip-krx
```

Live collection requires the explicit `--live` flag and approved provider
access. KRX is skipped by default. Authenticated pykrx/KRX collection is allowed
only through an explicitly bounded, D-authorized manual run with one request
stream, a D-owned lock, Landing-first capture, and exact ledger/checkpoint gates.
Passing a feasibility pilot does not authorize a bulk backfill.

## Repository map

- [`src/stock_data/`](src/stock_data/) contains the data-layer implementation.
- [`scripts/run_data_v1.py`](scripts/run_data_v1.py) is the supported regular
  runner; [`scripts/manual/`](scripts/manual/) contains approval-gated
  diagnostics, pilots, migrations, and backfills.
- [`tests/`](tests/) contains the offline unit test suite and fixtures.
- [`docs/DATA_API_INVENTORY.md`](docs/DATA_API_INVENTORY.md) describes provider
  contracts, while [`docs/DATA_STATUS.md`](docs/DATA_STATUS.md) records verified
  coverage, blockers, and availability rules.
- [`docs/PROJECT_ROADMAP.md`](docs/PROJECT_ROADMAP.md) defines the long-term
  domain boundaries and development sequence.
- [`docs/runbooks/`](docs/runbooks/) contains reproducible, approval-gated
  operating procedures, including the
  [authenticated pykrx historical plan](docs/runbooks/PYKRX_AUTHENTICATED_HISTORICAL_PLAN.md).
