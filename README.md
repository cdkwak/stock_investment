# Stock Investment Rev1

Local, reproducible market-data layer for normalized source data, derived data,
and downstream published datasets. Data v1 is frozen: changes require explicit
scope and must preserve Dataset Contracts, Parquet data, and checkpoints.

## Environment

- Python: project-local `.venv` (currently Python 3.13.14)
- Dependencies: `pyproject.toml`
- Install: `.\.venv\Scripts\python.exe -m pip install -e ".[test]"`
- Tests: `.\.venv\Scripts\python.exe -m pytest`

Secrets belong only in `.env`; never commit or print them. `.env.example`
contains variable names without values.

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
access. KRX is skipped by default. Automated `data.krx.co.kr` access and pykrx
historical automation are prohibited.

See `docs/DATA_API_INVENTORY.md` for provider contracts and
`docs/DATA_STATUS.md` for verified coverage, blockers, and availability rules.
