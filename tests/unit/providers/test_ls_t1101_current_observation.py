from __future__ import annotations

from datetime import datetime, timezone

import pytest

from stock_data.providers.ls_t1101_current_observation import (
    LST1101CurrentObservationError,
    t1101_current_quote,
)


def _payload(hotime: str = "13200000") -> dict[str, object]:
    return {
        "rsp_cd": "00000",
        "t1101OutBlock": {"shcode": "005930", "price": "70000", "hotime": hotime},
    }


def test_composes_source_time_with_the_same_regular_xkrx_session_label() -> None:
    source = t1101_current_quote(
        _payload(), retrieved_at=datetime(2026, 8, 21, 4, 25, tzinfo=timezone.utc),
    )
    assert source.value.provider_timestamp_utc == "2026-08-21T04:20:00+00:00"
    assert source.value.retrieved_at_utc == "2026-08-21T04:25:00+00:00"
    assert source.value.unit == "provider_native_price"


@pytest.mark.parametrize(
    ("hotime", "retrieved_at", "message"),
    [
        ("24600000", datetime(2026, 8, 21, 4, 25, tzinfo=timezone.utc), "hotime must"),
        ("13300000", datetime(2026, 8, 21, 4, 25, tzinfo=timezone.utc), "future"),
        ("12000000", datetime(2026, 8, 21, 4, 25, tzinfo=timezone.utc), "60-minute"),
        ("10000000", datetime(2026, 8, 22, 1, 5, tzinfo=timezone.utc), "outside an XKRX"),
    ],
)
def test_rejects_invalid_future_stale_or_non_session_time_composition(
    hotime: str, retrieved_at: datetime, message: str,
) -> None:
    with pytest.raises(LST1101CurrentObservationError, match=message):
        t1101_current_quote(_payload(hotime), retrieved_at=retrieved_at)
