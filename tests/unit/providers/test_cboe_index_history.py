from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json

import pandas as pd
import pytest

from stock_data.providers.cboe_index_history import (
    CboeIndexHistoryError,
    fetch_cboe_index_history,
    parse_cboe_index_history_csv,
)


OHLC_CSV = (
    b"\xef\xbb\xbfDATE,OPEN,HIGH,LOW,CLOSE\n"
    b"09/02/2026,17.100000,17.600000,16.900000,17.400000\n"
    b"09/03/2026,17.660000,17.800000,17.340000,17.420000\n"
)
SINGLE_VALUE_CSV = (
    b"Trade Date,SKEW\n"
    b"09/02/2026,142.310000\n"
    b"09/03/2026,143.090000\n"
)


def test_parse_cboe_ohlc_csv_handles_bom_and_contract_shape() -> None:
    frame = parse_cboe_index_history_csv(OHLC_CSV, "VIX9D")

    assert frame.columns.tolist() == [
        "date", "symbol", "source_ticker", "open", "high", "low", "close", "volume",
    ]
    assert frame.iloc[-1].to_dict() == {
        "date": "2026-09-03", "symbol": "VIX9D", "source_ticker": "VIX9D",
        "open": 17.66, "high": 17.8, "low": 17.34, "close": 17.42,
        "volume": None,
    }
    assert str(frame["volume"].dtype) == "Int64"
    assert frame.attrs["provider_gap_dates"] == ()


def test_parse_cboe_single_value_csv_fills_ohlc_from_value() -> None:
    frame = parse_cboe_index_history_csv(SINGLE_VALUE_CSV, "SKEW")

    assert frame["open"].tolist() == [142.31, 143.09]
    assert frame[["open", "high", "low", "close"]].nunique(axis=1).eq(1).all()
    assert frame["source_ticker"].eq("SKEW").all()


@pytest.mark.parametrize(
    "content, message",
    [
        (b"DATE,SKEW\n09/03/2026,143\n09/02/2026,142\n", "strictly monotonic"),
        (b"DATE,SKEW\n09/03/2026,\n", "close contains missing"),
    ],
)
def test_parse_cboe_csv_rejects_invalid_dates_or_close(content: bytes, message: str) -> None:
    with pytest.raises(CboeIndexHistoryError, match=message):
        parse_cboe_index_history_csv(content, "SKEW")


def test_fetch_cboe_history_captures_exact_csv_and_hash_before_parsing(tmp_path) -> None:
    class Response:
        status_code = 200
        content = OHLC_CSV
        headers = {"Content-Type": "text/csv"}

        @staticmethod
        def raise_for_status() -> None:
            return None

    class Session:
        calls = []

        @classmethod
        def get(cls, url, **kwargs):
            cls.calls.append((url, kwargs))
            return Response()

    frame = fetch_cboe_index_history(
        "VIX9D", session=Session, capture_root=tmp_path,
        now=lambda: datetime(2026, 9, 4, 13, 10, tzinfo=timezone.utc),
    )

    body = (tmp_path / "VIX9D.csv").read_bytes()
    receipt = json.loads((tmp_path / "VIX9D.json").read_text(encoding="utf-8"))
    assert body == OHLC_CSV
    assert receipt["provider"] == "cboe_index_history_csv"
    assert receipt["request_parameters"] == {"symbol": "VIX9D"}
    assert receipt["response_body_sha256"] == hashlib.sha256(body).hexdigest()
    assert receipt["response_bytes"] == len(body) == frame.attrs["response_bytes"]
    assert Session.calls[0][1]["headers"]["User-Agent"] == "stock-investment-rev1/0.1"
    assert "params" not in Session.calls[0][1]


def test_fetch_cboe_history_retains_invalid_body_before_parser_failure(tmp_path) -> None:
    class Response:
        status_code = 200
        content = b"not,the,documented,shape\n"
        headers = {"Content-Type": "text/csv"}

        @staticmethod
        def raise_for_status() -> None:
            return None

    class Session:
        @staticmethod
        def get(*_args, **_kwargs):
            return Response()

    with pytest.raises(CboeIndexHistoryError):
        fetch_cboe_index_history("VIX3M", session=Session, capture_root=tmp_path)
    assert (tmp_path / "VIX3M.csv").read_bytes() == Response.content


def test_parse_cboe_csv_rejects_unregistered_symbol_before_work() -> None:
    with pytest.raises(ValueError, match="unregistered"):
        parse_cboe_index_history_csv(OHLC_CSV, "VIX")
