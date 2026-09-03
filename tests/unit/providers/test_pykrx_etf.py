from datetime import date

import pandas as pd
import pytest

from stock_data.providers.pykrx.kr_etf import PykrxEtfClient
from stock_data.providers.pykrx.safety import (
    PykrxAutomationDisabledError,
    PykrxRequestPolicy,
)


class FakeStock:
    calls: list[tuple] = []

    @classmethod
    def get_etf_ticker_list(cls, source_date):
        cls.calls.append(("list", source_date))
        return ["123320", "243880"]

    @classmethod
    def get_etf_ticker_name(cls, symbol):
        cls.calls.append(("name", symbol))
        return {"123320": "TIGER 레버리지", "243880": "TIGER 200 IT 레버리지"}[symbol]

    @classmethod
    def get_etf_ohlcv_by_date(cls, start, end, symbol):
        cls.calls.append(("ohlcv", start, end, symbol))
        return pd.DataFrame(
            {
                "NAV": [10000.5], "시가": [10000], "고가": [10100],
                "저가": [9900], "종가": [10050], "거래량": [1234],
                "거래대금": [12_345_000], "기초지수": [300.1],
            },
            index=pd.to_datetime(["2026-09-01"]),
        )


def _client() -> PykrxEtfClient:
    return PykrxEtfClient(
        stock_module=FakeStock,
        policy=PykrxRequestPolicy(min_interval_seconds=0, max_consecutive_requests=5),
    )


def test_pykrx_etf_adapter_preserves_raw_frames_and_exact_call_arguments() -> None:
    FakeStock.calls.clear()
    client = _client()

    assert client.get_etf_ticker_list(date(2026, 9, 2)) == ("123320", "243880")
    assert client.get_etf_ticker_name("123320") == "TIGER 레버리지"
    raw = client.get_etf_ohlcv_by_date(
        date(2026, 8, 24), date(2026, 9, 2), "123320",
    )

    assert list(raw.columns) == [
        "NAV", "시가", "고가", "저가", "종가", "거래량", "거래대금", "기초지수",
    ]
    assert client.request_count == 3
    assert FakeStock.calls == [
        ("list", "20260902"),
        ("name", "123320"),
        ("ohlcv", "20260824", "20260902", "123320"),
    ]


def test_pykrx_etf_live_adapter_is_disabled_without_explicit_manual_mode() -> None:
    with pytest.raises(PykrxAutomationDisabledError, match="automation is disabled"):
        PykrxEtfClient(manual=False, requested_days=1)
