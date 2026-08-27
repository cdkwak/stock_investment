from datetime import date, timedelta

import pandas as pd
import pytest

from stock_data.contracts.market_60m import MARKET_PRICE_60M_OBSERVATION
from stock_data.validation.market_60m import (
    audit_session_bars,
    select_complete_session_provider,
    validate_market_price_60m,
)


def _rows(provider: str, starts: tuple[str, ...]) -> pd.DataFrame:
    rows = []
    for index, start_value in enumerate(starts):
        start = pd.Timestamp(start_value)
        rows.append({
            "market_date": date(2026, 8, 17),
            "market": "US",
            "symbol": "SPY",
            "asset_type": "ETF",
            "bar_start": start,
            "bar_end": start + timedelta(minutes=60),
            "timezone": "America/New_York",
            "session": "REGULAR",
            "interval": "60m",
            "actual_duration_minutes": 60,
            "open": 100.0 + index,
            "high": 101.0 + index,
            "low": 99.0 + index,
            "close": 100.5 + index,
            "volume": 1_000 + index,
            "provider": provider,
            "provider_symbol": "SPY",
            "adjustment_status": "PROVIDER_UNADJUSTED",
            "retrieved_at": pd.Timestamp("2026-08-18T10:00:00Z"),
            "fallback_used": False,
            "fallback_reason": None,
        })
    return pd.DataFrame(rows, columns=MARKET_PRICE_60M_OBSERVATION.column_names)


def test_market_60m_validator_accepts_complete_regular_session_rows() -> None:
    validate_market_price_60m(_rows("PRIMARY", ("2026-08-17T13:30:00Z", "2026-08-17T14:30:00Z")))


def test_session_selection_uses_one_complete_fallback_provider_without_stitching() -> None:
    starts = ("2026-08-17T13:30:00Z", "2026-08-17T14:30:00Z")
    observations = pd.concat([
        _rows("PRIMARY", starts[:1]),
        _rows("FALLBACK", starts),
    ], ignore_index=True)
    selected = select_complete_session_provider(
        observations,
        expected_bar_starts={("SPY", date(2026, 8, 17)): tuple(map(pd.Timestamp, starts))},
        provider_priority=("PRIMARY", "FALLBACK"),
    )
    assert set(selected["provider"]) == {"FALLBACK"}
    assert selected["fallback_used"].all()
    assert set(selected["fallback_reason"]) == {"PRIMARY_SESSION_INCOMPLETE"}


def test_session_selection_fails_closed_instead_of_stitching_partial_providers() -> None:
    first = _rows("PRIMARY", ("2026-08-17T13:30:00Z",))
    second = _rows("FALLBACK", ("2026-08-17T14:30:00Z",))
    with pytest.raises(ValueError, match="no complete 60m provider session"):
        select_complete_session_provider(
            pd.concat([first, second], ignore_index=True),
            expected_bar_starts={
                ("SPY", date(2026, 8, 17)): (
                    pd.Timestamp("2026-08-17T13:30:00Z"),
                    pd.Timestamp("2026-08-17T14:30:00Z"),
                )
            },
            provider_priority=("PRIMARY", "FALLBACK"),
        )


def test_market_60m_validator_rejects_declared_duration_mismatch() -> None:
    rows = _rows("PRIMARY", ("2026-08-17T13:30:00Z",))
    rows.loc[0, "actual_duration_minutes"] = 59
    with pytest.raises(ValueError, match="duration differs"):
        validate_market_price_60m(rows)


def test_market_60m_validator_rejects_unknown_timezone() -> None:
    rows = _rows("PRIMARY", ("2026-08-17T13:30:00Z",))
    rows.loc[0, "timezone"] = "Not/AZone"

    with pytest.raises(ValueError, match="valid IANA zone"):
        validate_market_price_60m(rows)


def test_market_60m_validator_rejects_market_date_not_derived_in_declared_zone() -> None:
    rows = _rows("PRIMARY", ("2026-08-17T03:30:00Z",))
    rows.loc[0, "market_date"] = date(2026, 8, 17)

    with pytest.raises(ValueError, match="differs from local bar start"):
        validate_market_price_60m(rows)


def test_market_60m_validator_rejects_bar_ending_after_retrieval() -> None:
    rows = _rows("PRIMARY", ("2026-08-17T13:30:00Z",))
    rows.loc[0, "retrieved_at"] = pd.Timestamp("2026-08-17T13:59:59Z")

    with pytest.raises(ValueError, match="exceeds retrieval time"):
        validate_market_price_60m(rows)


def test_session_audit_reports_missing_duplicate_and_unexpected_bars() -> None:
    first = pd.Timestamp("2026-08-17T13:30:00Z")
    second = pd.Timestamp("2026-08-17T14:30:00Z")
    extra = pd.Timestamp("2026-08-17T15:30:00Z")
    result = audit_session_bars((first, first, extra), (first, second))
    assert result.status == "INCOMPLETE"
    assert result.missing_bars == (second,)
    assert result.duplicate_bars == (first,)
    assert result.unexpected_bars == (extra,)
