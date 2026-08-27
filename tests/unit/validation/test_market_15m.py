from datetime import timedelta

import pandas as pd
import pytest

from stock_data.contracts.market_15m import MARKET_PRICE_15M_OBSERVATION
from stock_data.validation.market_15m import audit_market_15m_bars, validate_market_price_15m


def _rows() -> pd.DataFrame:
    start = pd.Timestamp("2026-08-19T13:30:00Z")
    row = {
        "market_date": start.tz_convert("America/New_York").date(),
        "market": "US_INDEX",
        "series_id": "^GSPC",
        "provider_symbol": "^GSPC",
        "instrument_type": "INDEX",
        "bar_start": start,
        "bar_end": start + timedelta(minutes=15),
        "source_timezone": "America/New_York",
        "display_timezone": "Asia/Seoul",
        "session": "REGULAR",
        "interval": "15m",
        "open": 100.0,
        "high": 101.0,
        "low": 99.0,
        "close": 100.5,
        "volume": 1_000,
        "provider": "yahoo_chart_api",
        "data_availability": "INDICATIVE_DELAYED_NOT_LICENSED_REALTIME",
        "retrieved_at": pd.Timestamp("2026-08-19T14:00:00Z"),
    }
    return pd.DataFrame([row], columns=MARKET_PRICE_15M_OBSERVATION.column_names)


def test_market_15m_validator_accepts_explicit_utc_and_display_timezone() -> None:
    validate_market_price_15m(_rows())


def test_market_15m_validator_rejects_live_forming_bar() -> None:
    rows = _rows()
    rows.loc[0, "retrieved_at"] = pd.Timestamp("2026-08-19T13:40:00Z")
    with pytest.raises(ValueError, match="live-forming"):
        validate_market_price_15m(rows)


def test_market_15m_validator_rejects_off_grid_bar_without_rounding() -> None:
    rows = _rows()
    rows.loc[0, "bar_start"] += timedelta(seconds=1)
    rows.loc[0, "bar_end"] += timedelta(seconds=1)
    with pytest.raises(ValueError, match="off-grid"):
        validate_market_price_15m(rows)


def test_market_15m_validator_accepts_distinct_treasury_quote_grid() -> None:
    rows = _rows()
    start = pd.Timestamp("2026-08-19T13:20:00Z")
    rows.loc[0, "market"] = "CBOE"
    rows.loc[0, "series_id"] = "^TNX"
    rows.loc[0, "provider_symbol"] = "^TNX"
    rows.loc[0, "instrument_type"] = "TREASURY_YIELD_INDEX"
    rows.loc[0, "bar_start"] = start
    rows.loc[0, "bar_end"] = start + timedelta(minutes=15)
    rows.loc[0, "source_timezone"] = "America/Chicago"
    rows.loc[0, "market_date"] = start.tz_convert("America/Chicago").date()

    validate_market_price_15m(rows)

    second = rows.copy()
    second.loc[0, "bar_start"] += timedelta(minutes=15)
    second.loc[0, "bar_end"] += timedelta(minutes=15)
    rows = pd.concat([rows, second], ignore_index=True)
    rows.loc[0, "bar_start"] += timedelta(minutes=1)
    rows.loc[0, "bar_end"] += timedelta(minutes=1)
    with pytest.raises(ValueError, match="off-grid"):
        validate_market_price_15m(rows)

    shifted = pd.concat([second, second.copy()], ignore_index=True)
    shifted.loc[1, "bar_start"] += timedelta(minutes=15)
    shifted.loc[1, "bar_end"] += timedelta(minutes=15)
    shifted["bar_start"] += timedelta(minutes=1)
    shifted["bar_end"] += timedelta(minutes=1)
    with pytest.raises(ValueError, match="off-grid"):
        validate_market_price_15m(shifted)


def test_market_15m_validator_rejects_duplicate_and_bad_ohlc() -> None:
    duplicate = pd.concat([_rows(), _rows()], ignore_index=True)
    with pytest.raises(ValueError, match="duplicate"):
        validate_market_price_15m(duplicate)
    rows = _rows()
    rows.loc[0, "high"] = 98.0
    with pytest.raises(ValueError, match="OHLC relationship"):
        validate_market_price_15m(rows)


def test_market_15m_audit_reports_missing_without_filling() -> None:
    first = pd.Timestamp("2026-08-19T13:30:00Z")
    second = pd.Timestamp("2026-08-19T13:45:00Z")
    result = audit_market_15m_bars((first,), (first, second))
    assert result.status == "INCOMPLETE"
    assert result.missing_bars == (second,)
