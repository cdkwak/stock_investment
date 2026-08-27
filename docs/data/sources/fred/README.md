# FRED / ALFRED

## Status

- Project status: `ACTIVE` for current FRED observations; `PILOT` for ALFRED vintage work.
- Accepted scopes: U.S. Treasury yields, USD/KRW H.10, VIX, and derived 10Y-2Y.
- Current provider uses the FRED graph CSV route. Official API-key pilots are separate.

## Official reference

- [FRED API observations](https://fred.stlouisfed.org/docs/api/fred/series_observations.html)
- [FRED API keys](https://fred.stlouisfed.org/docs/api/api_key.html)

Official observations endpoint:
`https://api.stlouisfed.org/fred/series/observations`.

## Authentication

- Official JSON API pilot: `FRED_API_KEY`
- Current graph CSV provider: no project credential

Never print the key, full request URL containing it, or response headers.

## Safe read example

Use the existing bounded pilot for vintage-sensitive work:

```powershell
.\.venv\Scripts\python.exe .\scripts\manual\pilot\pilot_fred_alfred_bounded_realtime.py --help
```

Minimal parameter shape for the official endpoint:

```python
import os
import requests

API_URL = "https://api.stlouisfed.org/fred/series/observations"
params = {
    "series_id": "DGS10",
    "api_key": os.environ["FRED_API_KEY"],
    "file_type": "json",
    "observation_start": "2026-08-01",
    "observation_end": "2026-08-19",
}
response = requests.get(API_URL, params=params, timeout=(3.05, 10))
response.raise_for_status()
payload = response.json()
observations = payload.get("observations")
if not isinstance(observations, list):
    raise ValueError("FRED observations are missing")
```

Do not log `response.url`; it contains the key.

## Project route

- Provider: `src/stock_data/providers/fred.py`
- Collector example: `scripts/manual/collect/collect_fred_vix.py`
- ALFRED pilots: `scripts/manual/pilot/pilot_fred_alfred_*.py`
- Availability rule: [FRED Observation Availability](../../operations/FRED_OBSERVATION_AVAILABILITY.md)

## Boundaries

- Current-as-retrieved FRED values are not proof of the originally published vintage.
- `realtime_start` and `realtime_end` must be explicit for PIT/vintage research.
- Missing observations remain missing; do not interpolate source data.
