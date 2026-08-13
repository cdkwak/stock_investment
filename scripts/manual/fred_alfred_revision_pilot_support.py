"""Offline-safe parsing and comparison for the bounded FRED/ALFRED pilot."""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

import pandas as pd


SERIES_ID = "DGS10"
EXPECTED_FREQUENCY_SHORT = "D"
EXPECTED_UNITS = "Percent"
MAX_OBSERVATIONS = 128


class FredAlfredPilotError(RuntimeError):
    pass


@dataclass(frozen=True)
class SeriesMetadata:
    observation_start: str
    observation_end: str
    frequency: str
    frequency_short: str
    units: str
    seasonal_adjustment: str
    last_updated: str


def _object(body: bytes) -> dict[str, object]:
    if body.lstrip().startswith(b"<"):
        raise FredAlfredPilotError("HTML/XML response is not the requested JSON schema")
    try:
        value = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise FredAlfredPilotError("response is not valid JSON") from error
    if not isinstance(value, dict):
        raise FredAlfredPilotError("response root is not an object")
    if "error_code" in value or "error_message" in value:
        raise FredAlfredPilotError("FRED returned an API error payload")
    return value


def parse_metadata(body: bytes) -> SeriesMetadata:
    payload = _object(body)
    rows = payload.get("seriess")
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise FredAlfredPilotError("metadata must contain exactly one series")
    row = rows[0]
    if row.get("id") != SERIES_ID:
        raise FredAlfredPilotError("metadata series identity differs")
    required = (
        "observation_start", "observation_end", "frequency", "frequency_short",
        "units", "seasonal_adjustment", "last_updated",
    )
    if any(not isinstance(row.get(field), str) or not row[field] for field in required):
        raise FredAlfredPilotError("metadata is missing required semantic fields")
    if row["frequency_short"] != EXPECTED_FREQUENCY_SHORT:
        raise FredAlfredPilotError("DGS10 is not documented as daily in the live metadata")
    if row["units"] != EXPECTED_UNITS:
        raise FredAlfredPilotError("DGS10 live unit differs from Percent")
    return SeriesMetadata(**{field: row[field] for field in required})


def parse_revision_observations(body: bytes) -> tuple[dict[str, object], ...]:
    payload = _object(body)
    rows = payload.get("observations")
    if not isinstance(rows, list) or len(rows) > MAX_OBSERVATIONS:
        raise FredAlfredPilotError("observation response is missing or exceeds the pilot cap")
    parsed = []
    for row in rows:
        if not isinstance(row, dict):
            raise FredAlfredPilotError("observation row is not an object")
        required = ("realtime_start", "realtime_end", "date", "value")
        if any(not isinstance(row.get(field), str) for field in required):
            raise FredAlfredPilotError("observation row is missing revision fields")
        value = row["value"]
        numeric = None if value == "." else float(value)
        parsed.append({field: row[field] for field in required} | {"numeric_value": numeric})
    return tuple(parsed)


def compare_current_to_retained(
    observations: tuple[dict[str, object], ...], retained_root: Path,
    *, terminal_realtime_end: str = "9999-12-31",
) -> dict[str, object]:
    files = sorted(retained_root.rglob("data.parquet"))
    if not files:
        raise FredAlfredPilotError("retained Treasury artifact is missing")
    retained = pd.concat(
        [pd.read_parquet(path, columns=["date", "dgs10"]) for path in files],
        ignore_index=True,
    )
    dates = pd.to_datetime(retained["date"], errors="raise").dt.strftime("%Y-%m-%d")
    retained_values = dict(zip(dates, retained["dgs10"], strict=True))
    # output_type=2 returns one row per observation/vintage pair.  A realtime
    # interval ending 9999-12-31 is the current FRED value represented by that row.
    current = [row for row in observations if row["realtime_end"] == terminal_realtime_end]
    compared = []
    for row in current:
        expected = retained_values.get(str(row["date"]))
        actual = row["numeric_value"]
        if expected is None and actual is None:
            classification = "BOTH_MISSING"
        elif expected is None:
            classification = "RETAINED_MISSING"
        elif actual is None:
            classification = "FRED_MISSING"
        else:
            classification = "EXACT_MATCH" if float(expected) == float(actual) else "REVISED_OR_STALE"
        compared.append({"date": row["date"], "classification": classification})
    return {
        "current_rows": len(current),
        "compared_rows": len(compared),
        "classifications": {
            name: sum(item["classification"] == name for item in compared)
            for name in ("EXACT_MATCH", "REVISED_OR_STALE", "RETAINED_MISSING", "FRED_MISSING", "BOTH_MISSING")
        },
        "comparison_rows": compared,
        "values_persisted": False,
    }
