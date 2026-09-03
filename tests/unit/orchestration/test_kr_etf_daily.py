from datetime import date, datetime, timezone
import json

import pandas as pd
import pytest

from stock_data.contracts.kr_etf import KR_ETF_MASTER, KR_ETF_PRICE_DAILY
from stock_data.orchestration.kr_etf_daily import (
    KrEtfDailyError,
    normalize_prices,
    run_kr_etf_daily,
    validate_window,
)
from stock_data.storage.contract_parquet import read_dataset
from stock_data.validation.kr_etf import (
    validate_kr_etf_master,
    validate_kr_etf_price_daily,
)


def _raw(symbol: str) -> pd.DataFrame:
    offset = 100 if symbol == "243880" else 0
    return pd.DataFrame({
        "NAV": [10000.5 + offset, 10020.25 + offset],
        "시가": [10000 + offset, 0],
        "고가": [10100 + offset, 0],
        "저가": [9900 + offset, 0],
        "종가": [10050 + offset, 10050 + offset],
        "거래량": [1234, 0],
        "거래대금": [12_345_000, 0],
        "기초지수": [300.1, 300.2],
    }, index=pd.to_datetime(["2026-09-01", "2026-09-02"]))


class OfflineProvider:
    def __init__(self, *, listed=("123320", "243880"), fail_if_called=False):
        self._listed = listed
        self._calls = 0
        self.fail_if_called = fail_if_called

    @property
    def request_count(self):
        return self._calls

    def _count(self):
        if self.fail_if_called:
            raise AssertionError("idempotent replay must not call the provider")
        self._calls += 1

    def get_etf_ticker_list(self, source_date):
        self._count()
        return self._listed

    def get_etf_ticker_name(self, symbol):
        self._count()
        return {"123320": "TIGER 레버리지", "243880": "TIGER 200 IT 레버리지"}[symbol]

    def get_etf_ohlcv_by_date(self, start, end, symbol):
        self._count()
        return _raw(symbol)


def test_kr_etf_normalization_preserves_nav_and_valid_zero_no_trade_rows() -> None:
    frame = normalize_prices(
        _raw("123320"), symbol="123320",
        start=date(2026, 8, 24), end=date(2026, 9, 2),
    )
    assert frame["nav"].tolist() == [10000.5, 10020.25]
    assert frame.iloc[1][["open", "high", "low", "volume", "trading_value"]].tolist() == [0, 0, 0, 0, 0]
    assert frame["close"].tolist() == [10050, 10050]


def test_kr_etf_daily_run_is_landing_first_atomic_and_idempotent(tmp_path) -> None:
    provider = OfflineProvider()
    result = run_kr_etf_daily(
        tmp_path,
        symbols=("123320", "243880"),
        start=date(2026, 8, 24),
        end=date(2026, 9, 2),
        provider=provider,
        run_id="offline-fixture",
        now=datetime(2026, 9, 3, tzinfo=timezone.utc),
    )

    assert result["status"] == "SUCCEEDED"
    assert result["provider_calls"] == 5
    assert result["price_rows"] == 4
    checkpoint = tmp_path / result["checkpoint"]
    payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert payload["status"] == "SUCCEEDED"
    assert payload["retry_count"] == 0
    assert set(payload["normalized_writes"]) == {"kr_etf_master", "kr_etf_price_daily"}
    assert (checkpoint.parent / "ticker_list.json").is_file()
    assert (checkpoint.parent / "symbol=123320/ohlcv.parquet").is_file()

    master = read_dataset(
        tmp_path / "data/normalized/kr_etf_master",
        KR_ETF_MASTER, validate_kr_etf_master,
    )
    prices = read_dataset(
        tmp_path / "data/normalized/kr_etf_price_daily",
        KR_ETF_PRICE_DAILY, validate_kr_etf_price_daily,
    )
    assert master[["symbol", "market", "security_type", "leverage_multiple"]].values.tolist() == [
        ["123320", "KRX", "ETF", 2], ["243880", "KRX", "ETF", 2],
    ]
    assert prices.groupby("symbol").size().to_dict() == {"123320": 2, "243880": 2}

    replay = run_kr_etf_daily(
        tmp_path,
        symbols=("123320", "243880"),
        start=date(2026, 8, 24),
        end=date(2026, 9, 2),
        provider=OfflineProvider(fail_if_called=True),
    )
    assert replay["status"] == "NOOP_ALREADY_SUCCEEDED"
    assert replay["provider_calls"] == 0


def test_kr_etf_daily_missing_exact_date_identity_stops_before_normalized_write(tmp_path) -> None:
    provider = OfflineProvider(listed=("123320",))
    with pytest.raises(KrEtfDailyError, match="not in the exact-date ETF list"):
        run_kr_etf_daily(
            tmp_path,
            symbols=("123320", "243880"),
            start=date(2026, 8, 24),
            end=date(2026, 9, 2),
            provider=provider,
            run_id="missing-identity",
        )
    assert provider.request_count == 1
    assert not (tmp_path / "data/normalized/kr_etf_master").exists()
    assert not (tmp_path / "data/normalized/kr_etf_price_daily").exists()
    checkpoint = next(tmp_path.rglob("checkpoint.json"))
    assert json.loads(checkpoint.read_text(encoding="utf-8"))["status"] == "STOPPED"


def test_kr_etf_live_window_is_explicitly_bounded() -> None:
    assert validate_window(date(2026, 8, 24), date(2026, 9, 2)) == 10
    with pytest.raises(ValueError, match="1..10"):
        validate_window(date(2026, 8, 23), date(2026, 9, 2))
