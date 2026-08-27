from __future__ import annotations

from copy import deepcopy

import pandas as pd
import pytest

from stock_data.contracts.us_option_pcr import ORATS_OPTION_CORE_OBSERVATION
from stock_data.providers.orats_options import (
    ORATS_OPTION_TICKERS,
    ORATSOptionsPayloadError,
    parse_cores_payload,
)


_CAPTURED_AT = "2026-08-19T01:03:04Z"
_LANDING_SHA256 = "a" * 64


def _parse(payload, *, provider_snapshot_at_utc=None):
    return parse_cores_payload(
        payload,
        captured_at_utc=_CAPTURED_AT,
        landing_sha256=_LANDING_SHA256,
        provider_snapshot_at_utc=provider_snapshot_at_utc,
    )


def _row(ticker: str, **overrides) -> dict[str, object]:
    row = {
        "ticker": ticker,
        "tradeDate": "2026-08-18",
        "updatedAt": "2026-08-19T01:02:03Z",
        "cVolu": 100,
        "pVolu": 120,
        "cOi": 1_000,
        "pOi": 1_100,
        "providerExtra": "ignored",
    }
    row.update(overrides)
    return row


def _payload() -> dict[str, object]:
    return {"data": [_row("SPX"), _row("NDX"), _row("QQQ")]}


def test_parse_cores_payload_maps_counts_and_sorts_allowlisted_rows():
    payload = _payload()
    payload["data"][0].update(cVolu=11, pVolu=12, cOi=13, pOi=14)

    rows = _parse(payload, provider_snapshot_at_utc="2026-08-19T01:02:30+00:00")

    assert tuple(row["provider_ticker"] for row in rows) == ORATS_OPTION_TICKERS
    assert rows[0] == {
        "trade_date": "2026-08-18",
        "provider_ticker": "SPX",
        "asset_type": "INDEX",
        "call_volume": 11,
        "put_volume": 12,
        "call_open_interest": 13,
        "put_open_interest": 14,
        "provider_updated_at_utc": "2026-08-19T01:02:03Z",
        "provider_snapshot_at_utc": "2026-08-19T01:02:30Z",
        "captured_at_utc": _CAPTURED_AT,
        "landing_sha256": _LANDING_SHA256,
        "observation_status": "OBSERVED",
        "source": "ORATS_DELAYED_CORES",
    }
    assert [row["asset_type"] for row in rows] == ["INDEX", "ETF", "INDEX"]
    assert tuple(pd.DataFrame(rows).columns) == ORATS_OPTION_CORE_OBSERVATION.column_names


def test_parse_cores_payload_is_deterministic_and_does_not_mutate_input():
    payload = _payload()
    before = deepcopy(payload)

    first = _parse(payload)
    second = _parse({"data": list(reversed(payload["data"]))})

    assert first == second
    assert payload == before
    assert all(row["provider_snapshot_at_utc"] is None for row in first)


def test_parse_cores_payload_requires_explicit_provenance():
    with pytest.raises(TypeError):
        parse_cores_payload(_payload())


@pytest.mark.parametrize(
    ("field", "invalid"),
    (
        ("captured_at_utc", "2026-08-19T01:03:04"),
        ("captured_at_utc", "not-a-timestamp"),
        ("provider_snapshot_at_utc", "2026-08-19T01:02:30"),
        ("provider_snapshot_at_utc", "not-a-timestamp"),
        ("landing_sha256", "A" * 64),
        ("landing_sha256", "a" * 63),
        ("landing_sha256", "g" * 64),
        ("landing_sha256", None),
    ),
)
def test_parse_cores_payload_rejects_invalid_provenance(field, invalid):
    kwargs = {
        "captured_at_utc": _CAPTURED_AT,
        "landing_sha256": _LANDING_SHA256,
        "provider_snapshot_at_utc": None,
    }
    kwargs[field] = invalid

    with pytest.raises(ORATSOptionsPayloadError):
        parse_cores_payload(_payload(), **kwargs)


@pytest.mark.parametrize("payload", (None, [], "{}", 1, {}, {"data": []}))
def test_parse_cores_payload_rejects_malformed_or_empty_payload(payload):
    with pytest.raises(ORATSOptionsPayloadError):
        _parse(payload)


@pytest.mark.parametrize(
    "payload",
    (
        {"error": "denied", "data": [_row("SPX"), _row("QQQ"), _row("NDX")]},
        {"errors": [], "data": [_row("SPX"), _row("QQQ"), _row("NDX")]},
    ),
)
def test_parse_cores_payload_rejects_error_envelopes(payload):
    with pytest.raises(ORATSOptionsPayloadError, match="error payload"):
        _parse(payload)


def test_parse_cores_payload_rejects_missing_duplicate_and_extra_tickers():
    missing = {"data": [_row("SPX"), _row("QQQ")]}
    duplicate = {"data": [_row("SPX"), _row("SPX"), _row("QQQ"), _row("NDX")]}
    extra = {"data": [_row("SPX"), _row("QQQ"), _row("NDX"), _row("AAPL")]}

    with pytest.raises(ORATSOptionsPayloadError, match="missing allowlisted"):
        _parse(missing)
    with pytest.raises(ORATSOptionsPayloadError, match="duplicate ticker"):
        _parse(duplicate)
    with pytest.raises(ORATSOptionsPayloadError, match="unapproved ticker"):
        _parse(extra)


def test_parse_cores_payload_rejects_missing_ticker_field_and_mixed_dates():
    missing_ticker = _payload()
    del missing_ticker["data"][0]["ticker"]
    mixed = _payload()
    mixed["data"][0]["tradeDate"] = "2026-08-17"

    with pytest.raises(ORATSOptionsPayloadError, match="missing required fields: ticker"):
        _parse(missing_ticker)
    with pytest.raises(ORATSOptionsPayloadError, match="same tradeDate"):
        _parse(mixed)


@pytest.mark.parametrize("field", ("cVolu", "pVolu", "cOi", "pOi"))
@pytest.mark.parametrize("invalid", (-1, 1.0, "1", True, None, 2**63))
def test_parse_cores_payload_rejects_negative_or_non_integer_counts(field, invalid):
    payload = _payload()
    payload["data"][0][field] = invalid

    with pytest.raises(ORATSOptionsPayloadError, match="non-negative JSON integer"):
        _parse(payload)


@pytest.mark.parametrize(
    ("field", "invalid"),
    (
        ("tradeDate", "2026-8-18"),
        ("tradeDate", "2026-02-30"),
        ("updatedAt", "2026-08-19T01:02:03"),
        ("updatedAt", "not-a-timestamp"),
    ),
)
def test_parse_cores_payload_rejects_noncanonical_dates_and_timestamps(field, invalid):
    payload = _payload()
    payload["data"][0][field] = invalid

    with pytest.raises(ORATSOptionsPayloadError):
        _parse(payload)
