import json

import pandas as pd
import pytest

from scripts.manual.fred_alfred_revision_pilot_support import (
    FredAlfredPilotError, compare_current_to_retained, parse_metadata,
    parse_revision_observations,
)
from scripts.manual.pilot_fred_alfred_revision import finalize_failed_scope_offline
from scripts.manual.pilot_fred_alfred_bounded_realtime import (
    public_parameters, validate_bounded_response,
)


def _body(value):
    return json.dumps(value).encode()


def test_metadata_validates_frequency_and_units():
    metadata = parse_metadata(_body({"seriess": [{
        "id": "DGS10", "observation_start": "1962-01-02",
        "observation_end": "2026-08-06", "frequency": "Daily",
        "frequency_short": "D", "units": "Percent",
        "seasonal_adjustment": "Not Seasonally Adjusted",
        "last_updated": "2026-08-07 15:16:01-05",
    }]}))
    assert metadata.frequency_short == "D" and metadata.units == "Percent"


def test_observations_require_revision_fields_and_cap():
    rows = parse_revision_observations(_body({"observations": [{
        "realtime_start": "2026-08-07", "realtime_end": "9999-12-31",
        "date": "2026-08-06", "value": "4.21",
    }]}))
    assert rows[0]["numeric_value"] == 4.21
    with pytest.raises(FredAlfredPilotError, match="revision fields"):
        parse_revision_observations(_body({"observations": [{"date": "2026-08-06"}]}))


def test_compare_current_to_retained_does_not_persist_values(tmp_path):
    root = tmp_path / "normalized"
    root.mkdir()
    pd.DataFrame({"date": ["2026-08-06"], "dgs10": [4.21]}).to_parquet(root / "data.parquet")
    result = compare_current_to_retained(({
        "realtime_start": "2026-08-07", "realtime_end": "9999-12-31",
        "date": "2026-08-06", "value": "4.21", "numeric_value": 4.21,
    },), root)
    assert result["classifications"]["EXACT_MATCH"] == 1
    assert result["values_persisted"] is False
    bounded = compare_current_to_retained(({
        "realtime_start": "2026-08-07", "realtime_end": "2026-08-12",
        "date": "2026-08-06", "value": "4.21", "numeric_value": 4.21,
    },), root, terminal_realtime_end="2026-08-12")
    assert bounded["compared_rows"] == 1
    missing = compare_current_to_retained(({
        "realtime_start": "2026-08-07", "realtime_end": "2026-08-12",
        "date": "2026-08-06", "value": ".", "numeric_value": None,
    },), root, terminal_realtime_end="2026-08-12")
    assert missing["classifications"]["FRED_MISSING"] == 1


def test_retained_nan_and_fred_missing_are_both_missing(tmp_path):
    root = tmp_path / "normalized"
    root.mkdir()
    pd.DataFrame({"date": ["2026-07-03"], "dgs10": [float("nan")]}).to_parquet(root / "data.parquet")
    result = compare_current_to_retained(({
        "realtime_start": "2026-08-07", "realtime_end": "2026-08-12",
        "date": "2026-07-03", "value": ".", "numeric_value": None,
    },), root, terminal_realtime_end="2026-08-12")
    assert result["classifications"]["BOTH_MISSING"] == 1


def test_finalize_failed_scope_reconciles_two_retained_calls(tmp_path):
    from hashlib import sha256
    project = tmp_path
    run = project / "data/landing/diagnostics/fred_alfred_revision_pilot/run"
    metadata = _body({"seriess": [{
        "id": "DGS10", "observation_start": "1962-01-02",
        "observation_end": "2026-08-11", "frequency": "Daily",
        "frequency_short": "D", "units": "Percent",
        "seasonal_adjustment": "Not Seasonally Adjusted",
        "last_updated": "2026-08-12 15:16:23-05",
    }]})
    failure = _body({"error_code": 400, "error_message": "scope exceeds the maximum number of vintage dates allowed"})
    for stamp, operation, status, body in (
        ("1", "series", 200, metadata), ("2", "series_observations", 400, failure),
    ):
        call = run / operation / stamp
        call.mkdir(parents=True)
        (call / "response.body").write_bytes(body)
        (call / "call.json").write_text(json.dumps({
            "captured_at_utc": stamp, "operation": operation,
            "http_status": status, "landing_body_file": "response.body",
            "response_bytes": len(body), "response_body_sha256": sha256(body).hexdigest(),
        }), encoding="utf-8")
    result = finalize_failed_scope_offline(project, run)
    assert result["status"] == "PILOT_STOPPED_SCOPE_TOO_BROAD"
    assert result["network_requests_during_finalization"] == 0


def test_bounded_realtime_plan_is_one_small_exact_scope():
    params = public_parameters()
    assert params["output_type"] == "1"
    assert params["realtime_start"] == "2026-08-07"
    assert params["realtime_end"] == "2026-08-12"
    assert params["limit"] == "128"
    assert "api_key" not in params


def test_bounded_realtime_response_validates_exact_scope():
    payload = {
        **public_parameters(), "output_type": 1, "count": 1, "offset": 0,
        "limit": 128, "observations": [{
            "realtime_start": "2026-08-07", "realtime_end": "2026-08-12",
            "date": "2026-08-06", "value": "4.21",
        }],
    }
    rows = validate_bounded_response(_body(payload))
    assert rows[0]["numeric_value"] == 4.21
    payload["count"] = 2
    with pytest.raises(FredAlfredPilotError, match="incomplete"):
        validate_bounded_response(_body(payload))
