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

## Dashboard

The primary display is the local FastAPI web app (`src/stock_web`) with 홈,
시장, 종목, 내 계좌, and 데이터 pages. The always-on Windows task is
`STOCK_WEB_DASHBOARD`; open <http://127.0.0.1:8787> and restart it with:

```powershell
.\scripts\restart_web.cmd
```

Runtime logs are under `artifacts/runtime_logs/web/`; settings are stored in
`artifacts/local_user/web_settings.json`.

The former PySide6/PyQtGraph desktop GUI is retired; `src/stock_web` is the
only supported display runtime. Shared read-only services remain under
`src/stock_data/gui/` for compatibility with the web app.

Display-layer code does not promote market data; provider transport and
canonical promotion remain Data-owned. Each card retains its own source,
market-date/freshness, and semantic/PIT status.

## Daily offline release smoke

Use the supported provider-free smoke after installation or an update. It
creates the FastAPI app in-process, probes `/api/home`, `/api/market`,
`/api/account`, `/data`, and `/research`, checks retained schema/freshness and
read-only scheduler state, and verifies user data was not changed.

```powershell
.\.venv\Scripts\python.exe .\scripts\maintenance\run_release_readiness_smoke.py --output artifacts\release_readiness\release_readiness_latest.json
```

The JSON report records each route's status code, payload size and elapsed time,
plus exact code and retained-input identities. Exit status is
`0` for `PASS`, `2` for `DEGRADED`, and `1` for `FAIL`. `EXPECTED_LAG` is listed
separately; stale, unknown, blocked, unavailable, or unverified scheduler state
is never reported as a clean pass. The command never loads `.env`, calls a
provider, changes a scheduler definition, or updates market/account data.

## Overnight development ML

The supported ML research entry point runs for at most eight hours over the
verified frozen development slice, keeps the final holdout untouched, and stores
resumable trials in local SQLite:

```powershell
.\.venv\Scripts\python.exe .\scripts\run_overnight_ml.py --duration-hours 8 --keep-awake
.\.venv\Scripts\python.exe .\scripts\run_overnight_ml.py --status
```

Results are development candidates only. See the
[Overnight ML Runbook](docs/backtest/OVERNIGHT_ML_RUNBOOK.md).

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

The only supported Data v1 collection CLI entry point is `scripts/run_data_v1.py`.

```powershell
.\.venv\Scripts\python.exe .\scripts\run_data_v1.py --status
.\.venv\Scripts\python.exe .\scripts\run_data_v1.py --no-live --skip-krx
```

Live collection keeps the explicit `--live` flag as an operator mistake guard;
Project/Data Status provide standing authorization for public and existing-
credential provider access. KRX is skipped by default unless selected by the
operation. Use provider-aware concurrency/rate limits, the Data-owned lock,
Landing-first capture, and durable ledger/checkpoint gates. A successful pilot
may be expanded or automated when current identity, schema, rights, finality,
PIT, idempotency, and prior-valid-data protections support the larger scope; it
does not require a new permission-only approval.

## Repository map

- [Documentation Router — bounded agent reading](docs/README.md)
- [Project Goal — user-owned durable outcome](docs/project/PROJECT_GOAL.md)
- [Project Status — session routing entry point](docs/project/PROJECT_STATUS.md)
- [Scheduler Status — installed tasks, gaps, and consolidation](docs/project/SCHEDULER_STATUS.md)
- [Data Status — Data-layer entry point](docs/data/DATA_STATUS.md)
- [Backtest Status — Backtest-layer entry point](docs/backtest/BACKTEST_STATUS.md)
- [GUI Status — Dashboard and local-runtime entry point](docs/gui/GUI_STATUS.md)
- [Request Queue Board — queue-backed work view](artifacts/request_queue/BOARD.md)
- [Project Roadmap — long-term sequencing only](docs/project/PROJECT_ROADMAP.md)
- [Repository Map](docs/project/REPOSITORY_MAP.md)
- [Dataset Index](docs/data/DATASET_INDEX.md)
