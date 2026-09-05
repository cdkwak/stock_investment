from datetime import date, datetime, timezone
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pandas as pd
import pytest

from stock_data.orchestration.cboe_daily_pcr import (
    CboeDailyPcrLaneError,
    run_cboe_archive_backfill,
    run_cboe_daily_pcr_lane,
    validate_cboe_daily_pcr,
)
from stock_data.providers.cboe_daily_pcr import (
    ARCHIVE_PROVIDER,
    CBOE_ARCHIVE_FILES,
    CBOE_DAILY_PAGE_URL,
    PROVIDER,
    parse_daily_pcr,
)


FIXTURES = Path(__file__).parents[2] / "fixtures/cboe"
FLIGHT_HTML = (FIXTURES / "daily_page_flight_trimmed.html").read_bytes()
ARCHIVE_CSV = (FIXTURES / "totalpc_10_rows.csv").read_bytes()
NOW = datetime(2026, 9, 4, 22, 0, tzinfo=timezone.utc)


def _temp_root() -> Path:
    root = Path(__file__).parents[3] / ".tmp/agents/cboe_daily_pcr_tests_20260905" / uuid4().hex
    root.mkdir(parents=True)
    return root


def _response(body: bytes = FLIGHT_HTML) -> SimpleNamespace:
    return SimpleNamespace(
        status_code=200, content=body, headers={"content-type": "text/html; charset=utf-8"},
    )


def _normalized(root: Path) -> pd.DataFrame:
    paths = list((root / "data/normalized/cboe_daily_pcr_daily").rglob("data.parquet"))
    return pd.concat([pd.read_parquet(path) for path in paths], ignore_index=True)


def test_lane_lands_one_page_fetch_uses_selected_date_and_replays_api_zero() -> None:
    tmp_path = _temp_root()
    calls = []

    def transport(url, **kwargs):
        calls.append((url, kwargs))
        return _response()

    first = run_cboe_daily_pcr_lane(
        tmp_path, now=NOW, transport=transport, personal_mode=True,
    )
    replay = run_cboe_daily_pcr_lane(
        tmp_path, now=NOW,
        transport=lambda *_args, **_kwargs: pytest.fail("idempotent replay must be API zero"),
        personal_mode=True,
    )

    assert first["status"] == "COMPLETE" and first["api_calls"] == 1
    assert first["target_date"] == "2026-09-03"
    assert len(calls) == 1 and calls[0][0] == CBOE_DAILY_PAGE_URL
    landing_file = tmp_path / first["landing_file"]
    assert landing_file.read_bytes() == FLIGHT_HTML
    manifest = json.loads((landing_file.parent / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["sha256"] == hashlib.sha256(FLIGHT_HTML).hexdigest()
    assert set(_normalized(tmp_path)["provider"]) == {PROVIDER}
    assert replay["status"] == "NOOP_IDEMPOTENT" and replay["api_calls"] == 0
    receipt = pd.read_json(
        tmp_path / "artifacts/scheduler_logs/STOCK_DATA_CBOE_DAILY_PCR_last.json",
        typ="series",
    )
    assert receipt["task_name"] == "STOCK_DATA_CBOE_DAILY_PCR"


def test_lane_refuses_mismatching_explicit_date_after_preserving_landing() -> None:
    tmp_path = _temp_root()
    result = run_cboe_daily_pcr_lane(
        tmp_path, now=NOW, observation_date=date(2026, 9, 4),
        transport=lambda *_args, **_kwargs: _response(), personal_mode=True,
    )

    assert result["status"] == "SCHEMA_ERROR_LANDING_PRESERVED"
    assert result["api_calls"] == 1
    assert (tmp_path / result["landing_file"]).exists()
    assert not (tmp_path / "data/normalized/cboe_daily_pcr_daily").exists()
    replay = run_cboe_daily_pcr_lane(
        tmp_path, now=NOW, observation_date=date(2026, 9, 4),
        transport=lambda *_args, **_kwargs: pytest.fail("one daily call was already consumed"),
        personal_mode=True,
    )
    assert replay["status"] == "DAILY_CALL_ALREADY_CONSUMED_API_ZERO"


def test_lane_dry_run_is_api_zero_and_live_requires_personal_mode() -> None:
    tmp_path = _temp_root()
    dry = run_cboe_daily_pcr_lane(tmp_path, now=NOW, dry_run=True)
    assert dry["status"] == "DRY_RUN_PASS" and dry["api_calls"] == 0
    assert dry["source_url"] == CBOE_DAILY_PAGE_URL
    assert not (tmp_path / "artifacts/scheduler_logs/STOCK_DATA_CBOE_DAILY_PCR_last.json").exists()
    with pytest.raises(CboeDailyPcrLaneError, match="personal_mode"):
        run_cboe_daily_pcr_lane(tmp_path, now=NOW, transport=lambda: None)


def test_schema_failure_retains_landing_and_prior_normalized_state() -> None:
    tmp_path = _temp_root()
    result = run_cboe_daily_pcr_lane(
        tmp_path, now=NOW,
        transport=lambda *_args, **_kwargs: _response(b"<html>not cboe</html>"),
        personal_mode=True,
    )
    assert result["status"] == "SCHEMA_ERROR_LANDING_PRESERVED"
    assert (tmp_path / result["landing_file"]).exists()
    assert not (tmp_path / "data/normalized/cboe_daily_pcr_daily").exists()


def test_archive_backfill_appends_five_scopes_preserves_newer_and_is_idempotent() -> None:
    tmp_path = _temp_root()
    daily = run_cboe_daily_pcr_lane(
        tmp_path, now=NOW, transport=lambda *_args, **_kwargs: _response(), personal_mode=True,
    )
    assert daily["status"] == "COMPLETE"
    before_newer = _normalized(tmp_path).sort_values("scope").reset_index(drop=True)
    newer_partition = next(
        (tmp_path / "data/normalized/cboe_daily_pcr_daily/year=2026").rglob("data.parquet")
    )
    newer_partition_bytes = newer_partition.read_bytes()

    archive_dir = tmp_path / "coordinator_archives"
    archive_dir.mkdir()
    for filename in CBOE_ARCHIVE_FILES.values():
        (archive_dir / filename).write_bytes(ARCHIVE_CSV)
    first = run_cboe_archive_backfill(
        tmp_path, now=NOW, archive_dir=archive_dir, personal_mode=True,
    )
    replay = run_cboe_archive_backfill(
        tmp_path, now=NOW, archive_dir=archive_dir, personal_mode=True,
    )

    combined = _normalized(tmp_path)
    after_newer = combined.loc[
        pd.to_datetime(combined["date"]).dt.date == date(2026, 9, 3)
    ].sort_values("scope").reset_index(drop=True)
    pd.testing.assert_frame_equal(before_newer, after_newer, check_dtype=False)
    assert newer_partition.read_bytes() == newer_partition_bytes
    archive = combined.loc[combined["provider"] == ARCHIVE_PROVIDER]
    assert first["status"] == "COMPLETE" and first["rows_added"] == 50
    assert replay["status"] == "NOOP_IDEMPOTENT" and replay["rows_added"] == 0
    assert first["api_calls"] == replay["api_calls"] == 0
    assert len(archive) == 50
    assert set(archive["scope"]) == set(CBOE_ARCHIVE_FILES)
    assert archive[["call_oi", "put_oi", "oi_pcr"]].isna().all().all()


def test_live_archive_backfill_fetches_each_official_file_once() -> None:
    tmp_path = _temp_root()
    calls = []

    def transport(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(
            status_code=200, content=ARCHIVE_CSV, headers={"content-type": "text/csv"},
        )

    result = run_cboe_archive_backfill(
        tmp_path, now=NOW, transport=transport, confirm_live=True, personal_mode=True,
    )

    assert result["status"] == "COMPLETE" and result["api_calls"] == 5
    assert len(calls) == len(CBOE_ARCHIVE_FILES)
    assert {url.rsplit("/", 1)[-1] for url, _kwargs in calls} == set(CBOE_ARCHIVE_FILES.values())
    assert all(kwargs["headers"]["Accept"] == "text/csv" for _url, kwargs in calls)


def test_validator_rejects_partial_date_and_invalid_open_interest() -> None:
    frame = pd.DataFrame(parse_daily_pcr(FLIGHT_HTML, retrieved_at=NOW))
    with pytest.raises(CboeDailyPcrLaneError, match="misses required scopes"):
        validate_cboe_daily_pcr(frame.loc[frame["scope"] != "VIX"].copy())
    frame.loc[frame["scope"] == "VIX", "call_oi"] = -1
    with pytest.raises(CboeDailyPcrLaneError, match="call_oi is invalid"):
        validate_cboe_daily_pcr(frame)
