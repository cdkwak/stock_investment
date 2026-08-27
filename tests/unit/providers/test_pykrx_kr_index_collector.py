from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from stock_data.contracts.kr_index_daily import KR_INDEX_DAILY
from stock_data.providers.pykrx.kr_index_daily import (
    PykrxCollectionError,
    _normalize_response,
    collect_kr_index_daily,
    fetch_indices,
)
from stock_data.storage.atomic_parquet import (
    read_kr_index_daily,
    write_kr_index_daily_atomic,
)


def normalized_rows(days: tuple[str, ...], *, close_offset: float = 0) -> pd.DataFrame:
    records = []
    for day in days:
        for market, base in (("KOSDAQ", 900.0), ("KOSPI", 3000.0)):
            records.append([
                day, market, market, base, base + 20, base - 10,
                base + 10 + close_offset, 10, 100, 1000, "pykrx",
            ])
    return pd.DataFrame(records, columns=KR_INDEX_DAILY.column_names)


def raw_response(days: tuple[str, ...], base: float) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "시가": [base] * len(days),
            "고가": [base + 20] * len(days),
            "저가": [base - 10] * len(days),
            "종가": [base + 10] * len(days),
            "거래량": [10] * len(days),
            "거래대금": [100] * len(days),
            "상장시가총액": [1000] * len(days),
        },
        index=pd.to_datetime(list(days)),
    )


def test_pykrx_response_is_normalized_without_mutation() -> None:
    source = raw_response(("2026-08-03",), 3000)
    snapshot = source.copy(deep=True)
    actual = _normalize_response(source, "KOSPI")
    pd.testing.assert_frame_equal(source, snapshot)
    assert actual.iloc[0].to_dict() == {
        "date": "2026-08-03", "symbol": "KOSPI", "market": "KOSPI",
        "open": 3000, "high": 3020, "low": 2990, "close": 3010,
        "volume": 10, "trading_value": 100, "market_cap": 1000,
        "source": "pykrx",
    }


def test_fetch_indices_calls_verified_market_tickers() -> None:
    calls = []

    class Stock:
        def get_index_ohlcv(self, start, end, ticker):
            calls.append((start, end, ticker))
            return raw_response(("2026-08-03",), 3000 if ticker == "1001" else 900)

    result = fetch_indices(date(2026, 8, 1), date(2026, 8, 3), stock_module=Stock())
    assert calls == [
        ("20260801", "20260803", "1001"),
        ("20260801", "20260803", "2001"),
    ]
    assert set(result["symbol"]) == {"KOSPI", "KOSDAQ"}


def test_full_history_writes_all_fetched_rows(tmp_path: Path) -> None:
    output = tmp_path / "kr_index_daily"
    calls = []

    def fetcher(start, end):
        calls.append((start, end))
        return normalized_rows(("2026-08-03", "2026-08-04"))

    result = collect_kr_index_daily(
        "2026-08-01", "2026-08-04", output_path=output, fetcher=fetcher
    )
    assert result.mode == "full"
    assert result.total_rows == 4
    assert calls == [(date(2026, 8, 1), date(2026, 8, 4))]
    pd.testing.assert_frame_equal(read_kr_index_daily(output), normalized_rows(("2026-08-03", "2026-08-04")))


def test_incremental_update_refetches_overlap_and_replaces_keys(tmp_path: Path) -> None:
    output = tmp_path / "kr_index_daily"
    collect_kr_index_daily(
        "2026-08-01", "2026-08-04", output_path=output,
        fetcher=lambda start, end: normalized_rows(("2026-08-03", "2026-08-04")),
    )
    calls = []

    def incremental_fetcher(start, end):
        calls.append((start, end))
        return normalized_rows(("2026-08-04", "2026-08-05"), close_offset=1)

    result = collect_kr_index_daily(
        "2026-08-01", "2026-08-05", output_path=output,
        fetcher=incremental_fetcher, incremental=True, overlap_days=1,
    )
    saved = read_kr_index_daily(output)
    assert calls == [(date(2026, 8, 3), date(2026, 8, 5))]
    assert result.replaced_rows == 2
    assert result.total_rows == 6
    assert saved.loc[saved["date"].eq("2026-08-04"), "close"].eq(pd.Series([911.0, 3011.0], index=[2, 3])).all()


def test_out_of_range_provider_rows_do_not_replace_existing(tmp_path: Path) -> None:
    output = tmp_path / "kr_index_daily"
    original = normalized_rows(("2026-08-03",))
    write_kr_index_daily_atomic(original, output)
    before = {path: path.read_bytes() for path in output.rglob("*.parquet")}
    with pytest.raises(PykrxCollectionError, match="outside"):
        collect_kr_index_daily(
            "2026-08-04", "2026-08-05", output_path=output,
            fetcher=lambda start, end: normalized_rows(("2026-08-06",)),
        )
    assert {path: path.read_bytes() for path in output.rglob("*.parquet")} == before
