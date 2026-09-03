from datetime import date, datetime, timezone
import json

import pandas as pd

from stock_data.contracts.kr_etf import KR_ETF_MASTER, KR_ETF_PRICE_DAILY
from stock_data.orchestration.kr_etf_daily import run_kr_etf_daily
from stock_data.providers.pykrx.kr_etf import PykrxEtfClient
from stock_data.providers.pykrx.safety import PykrxRequestPolicy
from stock_data.storage.contract_parquet import read_dataset
from stock_data.validation.kr_etf import (
    validate_kr_etf_master,
    validate_kr_etf_price_daily,
)


class FixtureStock:
    names = {
        "123320": "TIGER 레버리지",
        "243880": "TIGER 200 IT 레버리지",
    }

    @staticmethod
    def get_etf_ticker_list(source_date):
        assert source_date == "20260902"
        return list(FixtureStock.names)

    @staticmethod
    def get_etf_ticker_name(symbol):
        return FixtureStock.names[symbol]

    @staticmethod
    def get_etf_ohlcv_by_date(start, end, symbol):
        assert (start, end) == ("20260901", "20260902")
        base = 10_000 if symbol == "123320" else 20_000
        return pd.DataFrame({
            "NAV": [base + 0.5, base + 100.25],
            "시가": [base, base + 50],
            "고가": [base + 100, base + 150],
            "저가": [base - 100, base],
            "종가": [base + 50, base + 100],
            "거래량": [1000, 1100],
            "거래대금": [base * 1000, (base + 100) * 1100],
            "기초지수": [300.0, 301.0],
        }, index=pd.to_datetime(["2026-09-01", "2026-09-02"]))


def test_pykrx_etf_adapter_to_landing_and_normalized_pipeline_is_offline(tmp_path) -> None:
    provider = PykrxEtfClient(
        stock_module=FixtureStock,
        policy=PykrxRequestPolicy(min_interval_seconds=0, max_consecutive_requests=5),
    )
    result = run_kr_etf_daily(
        tmp_path,
        symbols=("123320", "243880"),
        start=date(2026, 9, 1),
        end=date(2026, 9, 2),
        provider=provider,
        run_id="offline-integration",
        now=datetime(2026, 9, 3, tzinfo=timezone.utc),
    )

    checkpoint = json.loads((tmp_path / result["checkpoint"]).read_text(encoding="utf-8"))
    master = read_dataset(
        tmp_path / "data/normalized/kr_etf_master",
        KR_ETF_MASTER, validate_kr_etf_master,
    )
    prices = read_dataset(
        tmp_path / "data/normalized/kr_etf_price_daily",
        KR_ETF_PRICE_DAILY, validate_kr_etf_price_daily,
    )

    assert result["status"] == "SUCCEEDED" and provider.request_count == 5
    assert checkpoint["max_provider_calls"] == 5 and checkpoint["retry_count"] == 0
    assert all(not str(item["path"]).startswith(str(tmp_path)) for item in [
        checkpoint["landing"]["ticker_list"],
        checkpoint["landing"]["symbols"]["123320"]["ohlcv"],
    ])
    assert master["symbol"].tolist() == ["123320", "243880"]
    assert prices.groupby("symbol").size().to_dict() == {"123320": 2, "243880": 2}
