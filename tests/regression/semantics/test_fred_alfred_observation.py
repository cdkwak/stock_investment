import json

import pandas as pd
import pytest

from scripts.manual.pilot.pilot_fred_alfred_historical_revision import (
    public_parameters, validate_response,
)
from stock_data.contracts.fred_alfred_observation import (
    FRED_ALFRED_SERIES_SOURCE_OBSERVATION as CONTRACT,
)
from stock_data.validation.fred_alfred_observation import (
    validate_fred_alfred_source_observation,
)


def _frame():
    return pd.DataFrame({
        "series_id": ["DGS10"], "observation_date": ["2008-09-12"],
        "realtime_start": ["2008-09-13"], "realtime_end": [None],
        "source_realtime_end": ["9999-12-31"],
        "value": [3.72], "source_value": ["3.72"], "units": ["Percent"],
        "frequency": ["Daily"], "seasonal_adjustment": ["Not Seasonally Adjusted"],
        "source_output_type": [1], "capture_id": ["capture"],
        "captured_at_utc": [pd.Timestamp("2026-08-13T12:00:00Z")],
        "landing_response_sha256": ["a" * 64], "source_row_ordinal": [0],
        "availability_precision": ["source_date_only"],
    })


def test_draft_contract_is_nonduplicate_source_observation_shape():
    assert CONTRACT.status == "draft"
    assert CONTRACT.name != "fred_treasury_yield_daily"
    assert CONTRACT.primary_key == ("capture_id", "source_row_ordinal")
    validate_fred_alfred_source_observation(_frame())


def test_contract_preserves_dot_as_null_and_rejects_inference():
    frame = _frame()
    frame.loc[0, ["value", "source_value"]] = [float("nan"), "."]
    validate_fred_alfred_source_observation(frame)
    frame.loc[0, "availability_precision"] = "timestamp_inferred"
    with pytest.raises(ValueError, match="date-only"):
        validate_fred_alfred_source_observation(frame)


def test_historical_plan_is_one_bounded_uncredentialed_scope():
    params = public_parameters()
    assert params["series_id"] == "DGS10" and params["output_type"] == "1"
    assert params["realtime_start"] == "2008-09-12"
    assert params["realtime_end"] == "2008-09-26"
    assert params["limit"] == "128" and "api_key" not in params


def test_historical_parser_requires_complete_exact_scope():
    params = public_parameters()
    payload = {
        **params, "output_type": 1, "count": 1, "offset": 0, "limit": 128,
        "observations": [{
            "realtime_start": "2008-09-12", "realtime_end": "2008-09-26",
            "date": "2008-09-11", "value": "3.66",
        }],
    }
    assert len(validate_response(json.dumps(payload).encode())) == 1
    payload["count"] = 2
    with pytest.raises(RuntimeError, match="incomplete"):
        validate_response(json.dumps(payload).encode())
