from datetime import date, datetime, timezone

import pandas as pd
import pytest

from stock_data.providers.pykrx.kr_equity_provisional import (
    PykrxProvisionalEquityClient,
    normalize_market_ohlcv,
)
from stock_data.providers.pykrx.safety import (
    PykrxAutomationDisabledError,
    PykrxRequestPolicy,
)


def _fixture_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "시가": [70_000, 0],
            "고가": [71_000, 0],
            "저가": [69_500, 0],
            "종가": [70_500, 50_000],
            "거래량": [1_234, 0],
            "거래대금": [87_000_000, 0],
            "등락률": [0.7, 0.0],
        },
        index=pd.Index(["005930", "000660"], name="티커"),
    )


class FakeStock:
    calls: list[tuple[str, str]] = []

    @classmethod
    def get_market_ohlcv_by_ticker(cls, source_date: str, market: str) -> pd.DataFrame:
        cls.calls.append((source_date, market))
        return _fixture_frame()


def test_pykrx_market_wide_fixture_parses_exact_128_columns_and_preserves_zero_rows() -> None:
    observed_at = datetime(2026, 9, 3, 11, 31, tzinfo=timezone.utc)
    frame = normalize_market_ohlcv(
        _fixture_frame(),
        market="KOSPI",
        source_date=date(2026, 9, 3),
        observed_at=observed_at,
    )

    assert frame[["symbol", "open", "close", "volume"]].values.tolist() == [
        ["000660", 0, 50_000, 0],
        ["005930", 70_000, 70_500, 1_234],
    ]
    assert frame["provisional"].tolist() == [True, True]
    assert set(frame["source_operation"]) == {"stock.get_market_ohlcv_by_ticker"}
    assert frame["observed_at"].dt.tz is timezone.utc


def test_pykrx_provisional_adapter_uses_exact_date_and_market_call() -> None:
    FakeStock.calls.clear()
    client = PykrxProvisionalEquityClient(
        stock_module=FakeStock,
        policy=PykrxRequestPolicy(min_interval_seconds=0, max_consecutive_requests=2),
    )

    raw = client.get_market_ohlcv_by_ticker(date(2026, 9, 3), "KOSDAQ")

    assert raw.equals(_fixture_frame())
    assert FakeStock.calls == [("20260903", "KOSDAQ")]
    assert client.request_count == 1


def test_pykrx_provisional_live_adapter_requires_explicit_bounded_mode() -> None:
    with pytest.raises(PykrxAutomationDisabledError, match="automation is disabled"):
        PykrxProvisionalEquityClient(manual=False, requested_days=1)
