from datetime import date

import pandas as pd
import pytest

from stock_data.providers.pykrx.kospi200_index_daily import (
    KOSPI200IndexCollectionError,
    collect_kospi200_index_daily,
    fetch_kospi200_index,
    normalize_response,
)
from stock_data.storage.contract_parquet import read_dataset
from stock_data.contracts.kospi200_index_daily import KR_KOSPI200_INDEX_DAILY
from stock_data.validation.kospi200_index_daily import validate_kospi200_index_daily


def _raw():
    return pd.DataFrame({
        "시가": [100.0, 101.0], "고가": [102.0, 103.0], "저가": [99.0, 100.0],
        "종가": [101.0, 102.0], "거래량": [10, 11], "거래대금": [100, 110],
        "상장시가총액": [1000, 1010],
    }, index=pd.to_datetime(["2026-08-05", "2026-08-06"]))


def test_normalize_identity_schema_and_source_date():
    frame = normalize_response(_raw())
    assert frame.date.tolist() == ["2026-08-05", "2026-08-06"]
    assert frame.symbol.eq("KOSPI200").all() and frame.ticker.eq("1028").all()
    assert frame.date_semantics.eq("KRX_TRADING_DATE_DAILY_FINAL").all()


def test_source_zero_ohlc_is_preserved_like_existing_index_contract():
    raw = _raw()
    raw.loc[raw.index[0], ["시가", "고가", "저가"]] = 0
    raw.loc[raw.index[0], "종가"] = 100
    frame = normalize_response(raw)
    assert frame.iloc[0][["open", "high", "low"]].eq(0).all()
    assert frame.iloc[0].close == 100


def test_known_partial_zero_source_anomaly_is_flagged_not_repaired():
    raw = _raw()
    raw.loc[raw.index[0], ["시가", "고가", "저가", "종가"]] = [0, 0, 111.58, 109.3]
    frame = normalize_response(raw)
    row = frame.iloc[0]
    assert [row.open, row.high, row.low, row.close] == [0, 0, 111.58, 109.3]
    assert row.ohlc_status == "SOURCE_ANOMALY_OPEN_HIGH_ZERO_CLOSE_BELOW_LOW"


def test_fetch_is_one_call_retry_zero():
    calls = []
    class Stock:
        def get_index_ohlcv(self, start, end, ticker):
            calls.append((start, end, ticker))
            return _raw()
    frame = fetch_kospi200_index(date(2026, 8, 5), date(2026, 8, 6), stock_module=Stock())
    assert len(frame) == 2
    assert calls == [("20260805", "20260806", "1028")]


def test_explicit_historical_authorization_remains_one_call():
    calls = []
    class Stock:
        def get_index_ohlcv(self, start, end, ticker):
            calls.append((start, end, ticker))
            return _raw()
    fetch_kospi200_index(
        date(1990, 1, 3), date(2026, 8, 7), stock_module=Stock(),
        authorized_historical=True,
    )
    assert calls == [("19900103", "20260807", "1028")]


def test_fetch_does_not_retry_failure():
    calls = 0
    class Stock:
        def get_index_ohlcv(self, start, end, ticker):
            nonlocal calls
            calls += 1
            raise RuntimeError("blocked")
    with pytest.raises(KOSPI200IndexCollectionError, match="retry-zero"):
        fetch_kospi200_index(date(2026, 8, 5), date(2026, 8, 6), stock_module=Stock())
    assert calls == 1


def test_collection_writes_separate_contract_atomically(tmp_path):
    output = tmp_path / "kr_kospi200_index_daily"
    result = collect_kospi200_index_daily(
        "2026-08-05", "2026-08-06", output_path=output,
        fetcher=lambda start, end: normalize_response(_raw()),
    )
    stored = read_dataset(output, KR_KOSPI200_INDEX_DAILY, validate_kospi200_index_daily)
    assert result.rows == 2 and result.business_calls == 1 and result.retry_count == 0
    assert stored.close.tolist() == [101.0, 102.0]
