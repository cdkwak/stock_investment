from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from stock_data.providers.cboe_daily_pcr import (
    ARCHIVE_PROVIDER,
    CBOE_DAILY_PAGE_URL,
    CboeDailyPcrError,
    download_daily_pcr,
    parse_archive_pcr,
    parse_daily_pcr,
)


FIXTURES = Path(__file__).parents[2] / "fixtures/cboe"
FLIGHT_HTML = (FIXTURES / "daily_page_flight_trimmed.html").read_bytes()
ARCHIVE_CSV = (FIXTURES / "totalpc_10_rows.csv").read_bytes()
NOW = datetime(2026, 9, 5, 1, 45, tzinfo=timezone.utc)


def test_parser_extracts_split_flight_json_scopes_ratios_and_selected_date() -> None:
    rows = parse_daily_pcr(FLIGHT_HTML, retrieved_at=NOW)

    assert [row["scope"] for row in rows] == [
        "TOTAL", "INDEX", "ETP", "EQUITY", "VIX", "SPX_SPXW",
    ]
    assert {row["date"] for row in rows} == {date(2026, 9, 3)}
    assert rows[0]["volume_pcr"] == pytest.approx(6011424 / 7924751)
    assert rows[0]["oi_pcr"] == pytest.approx(286459001 / 377122646)
    assert rows[-1]["call_volume"] == 2762278
    assert rows[-1]["put_oi"] == 13908229


def test_parser_fails_closed_for_published_ratio_or_requested_date_mismatch() -> None:
    with pytest.raises(CboeDailyPcrError, match="published volume ratio mismatch"):
        parse_daily_pcr(FLIGHT_HTML.replace(b'0.76', b'0.77', 1), retrieved_at=NOW)
    with pytest.raises(CboeDailyPcrError, match="differs from requested date"):
        parse_daily_pcr(
            FLIGHT_HTML, observation_date=date(2026, 9, 4), retrieved_at=NOW,
        )


def test_parser_requires_all_scopes_and_nulls_a_zero_denominator() -> None:
    missing_vix = FLIGHT_HTML.replace(
        b'CBOE VOLATILITY INDEX (VIX)\\\":[', b'UNMAPPED VIX\\\":[', 1,
    )
    with pytest.raises(CboeDailyPcrError, match="required scopes"):
        parse_daily_pcr(missing_vix, retrieved_at=NOW)

    zero_call = (
        FLIGHT_HTML
        .replace(b'7924751', b'0', 1)
        .replace(b'6011424', b'5', 1)
        .replace(b'13936175', b'5', 1)
        .replace(b'0.76', b'0.00', 1)
    )
    total = parse_daily_pcr(zero_call, retrieved_at=NOW)[0]
    assert total["call_volume"] == 0
    assert total["put_volume"] == 5
    assert total["volume_pcr"] is None


def test_download_makes_one_browser_like_html_call_without_parsing() -> None:
    calls = []

    def transport(url, **kwargs):
        calls.append((url, kwargs))
        return SimpleNamespace(
            status_code=200, content=b"unparsed",
            headers={"content-type": "text/html; charset=utf-8"},
        )

    result = download_daily_pcr(transport=transport, retrieved_at=NOW)

    assert len(calls) == 1
    assert calls[0][0] == CBOE_DAILY_PAGE_URL
    assert calls[0][1]["headers"]["Accept"] == "text/html"
    assert calls[0][1]["headers"]["User-Agent"].startswith("Mozilla/5.0")
    assert result.body == b"unparsed"


def test_archive_parser_handles_preamble_and_space_padded_rows() -> None:
    rows = parse_archive_pcr(ARCHIVE_CSV, scope="TOTAL", retrieved_at=NOW)

    assert len(rows) == 10
    assert rows[0]["date"] == date(2006, 11, 1)
    assert rows[-1]["date"] == date(2019, 10, 4)
    assert rows[-1]["call_volume"] == 2175006
    assert rows[-1]["volume_pcr"] == pytest.approx(2289715 / 2175006)
    assert rows[-1]["call_oi"] is None and rows[-1]["oi_pcr"] is None
    assert {row["provider"] for row in rows} == {ARCHIVE_PROVIDER}
