from __future__ import annotations

from datetime import datetime, timezone

import pytest

from stock_data.orchestration.current_observation import CurrentObservationCoordinator, CurrentObservationFileStore
from stock_data.providers.naver_domestic_index_current_observation import (
    NaverDomesticIndexObservationError,
    naver_domestic_index_row,
    naver_domestic_index_route,
)


RETRIEVED = datetime(2026, 8, 21, 4, 26, 45, tzinfo=timezone.utc)


def _row(code: str = "KOSPI") -> dict:
    return {"cd": code, "nv": "3,212.45", "dt": "20260821132615", "ms": "OPEN"}


def test_exact_allowlisted_open_row_maps_to_native_index_points() -> None:
    source = naver_domestic_index_row(_row("KPI200"), retrieved_at=RETRIEVED)

    assert source.value.identity.symbol == "KPI200"
    assert source.value.value == 3212.45
    assert source.value.unit == "index points"
    assert source.value.provider_timestamp_utc == "2026-08-21T04:26:15+00:00"
    assert source.value.display_only and not source.value.pit_safe


@pytest.mark.parametrize("field, value, message", [
    ("cd", "KOSPI200", "allowlist"),
    ("ms", "CLOSE", "OPEN"),
    ("dt", "20260821120000", "60-minute"),
    ("dt", "20260822092615", "not today"),
    ("nv", 0, "positive"),
])
def test_invalid_identity_state_time_or_scale_is_numeric_free(field: str, value: object, message: str) -> None:
    row = _row()
    row[field] = value
    with pytest.raises(NaverDomesticIndexObservationError, match=message):
        naver_domestic_index_row(row, retrieved_at=RETRIEVED)


def test_atomic_projection_and_api_zero_replay(tmp_path) -> None:
    route = naver_domestic_index_route("KOSDAQ")
    coordinator = CurrentObservationCoordinator(CurrentObservationFileStore(tmp_path / "current.json"))
    candidate = naver_domestic_index_row(_row("KOSDAQ"), retrieved_at=RETRIEVED)

    result = coordinator.refresh(
        route,
        primary_attempt=lambda: candidate,
        fallback_attempt=lambda: (_ for _ in ()).throw(AssertionError("fallback disabled")),
    )

    assert result.observation == candidate.value
    assert result.api_calls == 1
    assert coordinator.replay(route).observation == candidate.value
    assert coordinator.replay(route).api_calls == 0
