from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from stock_data.contracts.kr_equity import (
    KR_EQUITY_MARKET_CAP_DAILY,
    KR_EQUITY_MASTER,
    KR_EQUITY_PRICE_DAILY,
)
from stock_data.providers.pykrx.kr_equity_daily import (
    DailySourceFrames,
    _fetch_once,
    collect_equity_dates,
)
from stock_data.storage.equity_parquet import read_partitioned
from stock_data.validation.kr_equity import (
    EquityValidationError,
    validate_equity_market_cap,
    validate_equity_master,
    validate_equity_price,
)


def raw_frame(symbols=("000001", "000002")):
    return pd.DataFrame(
        {
            "시가": [100, 0], "고가": [110, 0], "저가": [90, 0],
            "종가": [105, 0], "거래량": [1000, 0], "거래대금": [100000, 0],
        }, index=list(symbols),
    )


class FakeStock:
    def get_market_ohlcv(self, text, market):
        return raw_frame()

    def get_market_cap(self, text, market):
        return pd.DataFrame(
            {"시가총액": [1_000_000, 2_000_000], "상장주식수": [10_000, 20_000]},
            index=["000001", "000002"],
        )

    def get_market_price_change(self, start, end, market, adjusted, delist):
        return pd.DataFrame({"종목명": ["Alpha", "Beta"]}, index=["000001", "000002"])


def test_source_frames_are_split_without_losing_valid_zero() -> None:
    result = _fetch_once(FakeStock(), date(2026, 8, 3), "KOSPI")
    assert list(result.price.columns) == list(KR_EQUITY_PRICE_DAILY.column_names)
    assert list(result.market_cap.columns) == list(KR_EQUITY_MARKET_CAP_DAILY.column_names)
    assert list(result.master.columns) == list(KR_EQUITY_MASTER.column_names)
    assert result.price.loc[result.price["symbol"].eq("000002"), "close"].item() == 0


def test_collector_writes_separate_market_year_datasets(tmp_path: Path) -> None:
    def fetcher(day, market):
        return _fetch_once(FakeStock(), day, market)

    result = collect_equity_dates(
        [date(2026, 8, 3)], normalized_root=tmp_path / "normalized",
        state_path=tmp_path / "state.json", fetcher=fetcher,
    )
    assert result.price_rows == 4
    assert result.market_cap_rows == 4
    assert result.master_rows == 4
    assert len(list(result.price_root.glob("market=*/year=2026/data.parquet"))) == 2
    assert len(list(result.market_cap_root.glob("market=*/year=2026/data.parquet"))) == 2
    assert len(list(result.master_root.glob("market=*/data.parquet"))) == 2
    assert read_partitioned(
        result.price_root, KR_EQUITY_PRICE_DAILY, validate_equity_price
    ).duplicated(["date", "market", "symbol"]).sum() == 0


def test_same_date_increment_replaces_without_duplicates(tmp_path: Path) -> None:
    kwargs = dict(
        normalized_root=tmp_path / "normalized", state_path=tmp_path / "state.json",
        fetcher=lambda day, market: _fetch_once(FakeStock(), day, market),
    )
    collect_equity_dates([date(2026, 8, 3)], **kwargs)
    collect_equity_dates([date(2026, 8, 3)], **kwargs)
    stored = read_partitioned(
        tmp_path / "normalized/kr_equity_price_daily",
        KR_EQUITY_PRICE_DAILY, validate_equity_price,
    )
    assert len(stored) == 4
    assert stored.duplicated(["date", "market", "symbol"]).sum() == 0


def test_backfill_resume_skips_already_saved_date(tmp_path: Path) -> None:
    root=tmp_path/"normalized"; state=tmp_path/"state.json"
    collect_equity_dates(
        [date(2026,8,3)], normalized_root=root, state_path=state,
        fetcher=lambda day,market:_fetch_once(FakeStock(),day,market),
    )
    result=collect_equity_dates(
        [date(2026,8,3)], normalized_root=root, state_path=state,
        fetcher=lambda day,market:(_ for _ in ()).throw(AssertionError("must skip")),
        skip_existing=True,
    )
    assert result.requested_dates==0 and result.price_rows==4


def test_duplicate_and_invalid_ohlc_are_rejected() -> None:
    valid = _fetch_once(FakeStock(), date(2026, 8, 3), "KOSPI").price
    with pytest.raises(EquityValidationError, match="duplicates"):
        validate_equity_price(pd.concat([valid, valid.iloc[[0]]], ignore_index=True))
    invalid = valid.copy()
    invalid.loc[0, "high"] = 1
    with pytest.raises(EquityValidationError, match="OHLC"):
        validate_equity_price(invalid)


def test_suspended_security_source_zero_is_preserved() -> None:
    price = _fetch_once(FakeStock(), date(2026, 8, 3), "KOSPI").price
    suspended = price.loc[price["symbol"].eq("000002")].copy()
    suspended.loc[:, "close"] = 123
    validate_equity_price(suspended.reset_index(drop=True))
    assert suspended["open"].item() == 0
    assert suspended["close"].item() == 123
