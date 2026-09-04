from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pandas as pd
import pytest

from stock_data.orchestration.cboe_daily_pcr import (
    CboeDailyPcrLaneError,
    run_cboe_daily_pcr_lane,
    validate_cboe_daily_pcr,
)
from stock_data.providers.cboe_daily_pcr import parse_daily_pcr


CSV = b"""date,scope,call_volume,put_volume,call_oi,put_oi
2026-09-04,TOTAL,100,125,200,100
2026-09-04,INDEX,20,30,40,60
2026-09-04,ETP,50,25,80,40
2026-09-04,EQUITY,40,60,90,135
2026-09-04,VIX,10,30,15,45
"""
NOW = datetime(2026, 9, 4, 22, 0, tzinfo=timezone.utc)


def _temp_root() -> Path:
    root = Path(__file__).parents[3] / ".tmp/agents/cboe_daily_pcr_tests_20260905" / uuid4().hex
    root.mkdir(parents=True)
    return root


def test_lane_is_landing_first_promotes_once_and_replays_api_zero() -> None:
    tmp_path = _temp_root()
    calls = []

    def transport(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(status_code=200, content=CSV, headers={"content-type": "text/csv"})

    first = run_cboe_daily_pcr_lane(
        tmp_path, now=NOW, observation_date=date(2026, 9, 4), transport=transport,
        source_url_template="https://example.test/{date}.csv",
        personal_mode=True, endpoint_verified=True,
    )
    replay = run_cboe_daily_pcr_lane(
        tmp_path, now=NOW, observation_date=date(2026, 9, 4),
        transport=lambda *_args, **_kwargs: pytest.fail("idempotent replay must be API zero"),
        source_url_template="https://example.test/{date}.csv",
        personal_mode=True, endpoint_verified=True,
    )

    assert first["status"] == "COMPLETE" and first["api_calls"] == 1
    assert len(calls) == 1
    assert (tmp_path / first["landing_file"]).read_bytes() == CSV
    assert len(list((tmp_path / "data/normalized/cboe_daily_pcr_daily").rglob("data.parquet"))) == 1
    assert replay["status"] == "NOOP_IDEMPOTENT" and replay["api_calls"] == 0
    receipt = pd.read_json(
        tmp_path / "artifacts/scheduler_logs/STOCK_DATA_CBOE_DAILY_PCR_last.json",
        typ="series",
    )
    assert receipt["task_name"] == "STOCK_DATA_CBOE_DAILY_PCR"


def test_lane_dry_run_is_api_zero_and_live_requires_personal_verified_mode() -> None:
    tmp_path = _temp_root()
    dry = run_cboe_daily_pcr_lane(tmp_path, now=NOW, dry_run=True)
    assert dry["status"] == "DRY_RUN_PASS" and dry["api_calls"] == 0
    assert not (tmp_path / "artifacts/scheduler_logs/STOCK_DATA_CBOE_DAILY_PCR_last.json").exists()
    with pytest.raises(CboeDailyPcrLaneError, match="personal_mode"):
        run_cboe_daily_pcr_lane(tmp_path, now=NOW, transport=lambda: None)
    with pytest.raises(CboeDailyPcrLaneError, match="endpoint_verified"):
        run_cboe_daily_pcr_lane(tmp_path, now=NOW, personal_mode=True, transport=lambda: None)


def test_schema_failure_retains_landing_and_prior_normalized_state() -> None:
    tmp_path = _temp_root()
    result = run_cboe_daily_pcr_lane(
        tmp_path, now=NOW, observation_date=date(2026, 9, 4),
        transport=lambda *_args, **_kwargs: SimpleNamespace(
            status_code=200, content=b"not,cboe\n1,2\n", headers={"content-type": "text/csv"},
        ),
        source_url_template="https://example.test/{date}.csv",
        personal_mode=True, endpoint_verified=True,
    )
    assert result["status"] == "SCHEMA_ERROR_LANDING_PRESERVED"
    assert (tmp_path / result["landing_file"]).exists()
    assert not (tmp_path / "data/normalized/cboe_daily_pcr_daily").exists()
    replay = run_cboe_daily_pcr_lane(
        tmp_path, now=NOW, observation_date=date(2026, 9, 4),
        transport=lambda *_args, **_kwargs: pytest.fail("one daily call was already consumed"),
        source_url_template="https://example.test/{date}.csv",
        personal_mode=True, endpoint_verified=True,
    )
    assert replay["status"] == "DAILY_CALL_ALREADY_CONSUMED_API_ZERO"
    assert replay["api_calls"] == 0


def test_validator_rejects_partial_date_and_invalid_open_interest() -> None:
    frame = pd.DataFrame(
        parse_daily_pcr(
            CSV, observation_date=date(2026, 9, 4), retrieved_at=NOW,
        )
    )
    with pytest.raises(CboeDailyPcrLaneError, match="misses required scopes"):
        validate_cboe_daily_pcr(frame.loc[frame["scope"] != "VIX"].copy())
    frame.loc[frame["scope"] == "VIX", "call_oi"] = -1
    with pytest.raises(CboeDailyPcrLaneError, match="call_oi is invalid"):
        validate_cboe_daily_pcr(frame)
