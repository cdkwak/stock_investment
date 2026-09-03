from datetime import date, datetime, timezone

import pandas as pd

from stock_data.contracts.kr_equity import KR_EQUITY_PRICE_DAILY
from stock_data.contracts.kr_equity_provisional import (
    KR_EQUITY_PRICE_PROVISIONAL_DAILY,
    validate_kr_equity_price_provisional_daily,
)
from stock_data.orchestration.kr_equity_provisional_daily import (
    cleanup_canonicalized_provisional_rows,
    run_kr_equity_provisional_daily,
)
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic
from stock_data.validation.kr_equity import validate_equity_price


def _raw(market: str) -> pd.DataFrame:
    symbol = "005930" if market == "KOSPI" else "035720"
    return pd.DataFrame(
        {
            "시가": [70_000], "고가": [71_000], "저가": [69_500],
            "종가": [70_500], "거래량": [1_234], "거래대금": [87_000_000],
            "등락률": [0.7],
        },
        index=pd.Index([symbol], name="티커"),
    )


class OfflineProvider:
    def __init__(self, *, empty: bool = False, fail_if_called: bool = False) -> None:
        self._calls = 0
        self.empty = empty
        self.fail_if_called = fail_if_called

    @property
    def request_count(self) -> int:
        return self._calls

    def get_market_ohlcv_by_ticker(self, source_date: date, market: str) -> pd.DataFrame:
        if self.fail_if_called:
            raise AssertionError("idempotent replay entered provider access")
        self._calls += 1
        return pd.DataFrame() if self.empty else _raw(market)


def test_provisional_lane_targets_session_lands_two_markets_and_is_idempotent(tmp_path) -> None:
    target = date(2026, 9, 3)
    result = run_kr_equity_provisional_daily(
        tmp_path,
        target_session=target,
        provider_factory=OfflineProvider,
        now=datetime(2026, 9, 3, 11, 31, tzinfo=timezone.utc),
        run_id="offline-success",
    )

    assert result["status"] == "UPDATED"
    assert result["target_session"] == "2026-09-03"
    assert result["api_calls"] == 2
    assert result["rows"] == 2
    assert len(list(tmp_path.glob(
        "data/landing/pykrx/kr_equity_provisional_daily/date=20260903/"
        "run=offline-success/market=*/ohlcv.parquet"
    ))) == 2
    retained = read_dataset(
        tmp_path / "data/normalized/kr_equity_price_provisional_daily",
        KR_EQUITY_PRICE_PROVISIONAL_DAILY,
        validate_kr_equity_price_provisional_daily,
    )
    assert retained[["date", "market", "symbol"]].astype(str).values.tolist() == [
        ["2026-09-03", "KOSDAQ", "035720"],
        ["2026-09-03", "KOSPI", "005930"],
    ]

    replay = run_kr_equity_provisional_daily(
        tmp_path,
        target_session=target,
        provider_factory=lambda: OfflineProvider(fail_if_called=True),
    )
    assert replay["status"] == "ALREADY_CURRENT"
    assert replay["api_calls"] == 0


def test_provisional_lane_reports_expected_provider_lag_for_two_valid_empty_frames(tmp_path) -> None:
    result = run_kr_equity_provisional_daily(
        tmp_path,
        target_session=date(2026, 9, 3),
        provider_factory=lambda: OfflineProvider(empty=True),
        run_id="offline-lag",
    )

    assert result["status"] == "EXPECTED_PROVIDER_LAG"
    assert result["api_calls"] == 2
    assert result["latest_after"] is None
    assert not (tmp_path / "data/normalized/kr_equity_price_provisional_daily").exists()


def test_cleanup_drops_only_old_provisional_rows_that_have_canonical_keys(tmp_path) -> None:
    for target in (date(2026, 8, 20), date(2026, 9, 3)):
        run_kr_equity_provisional_daily(
            tmp_path,
            target_session=target,
            provider_factory=OfflineProvider,
            run_id=f"offline-{target:%Y%m%d}",
        )
    provisional_root = tmp_path / "data/normalized/kr_equity_price_provisional_daily"
    provisional = read_dataset(
        provisional_root,
        KR_EQUITY_PRICE_PROVISIONAL_DAILY,
        validate_kr_equity_price_provisional_daily,
    )
    canonical = provisional.loc[
        provisional["date"].astype(str).eq("2026-08-20"),
        list(KR_EQUITY_PRICE_DAILY.column_names),
    ].reset_index(drop=True)
    write_dataset_atomic(
        canonical,
        tmp_path / "data/normalized/kr_equity_price_daily",
        KR_EQUITY_PRICE_DAILY,
        validate_equity_price,
    )

    result = cleanup_canonicalized_provisional_rows(
        tmp_path, reference_session=date(2026, 9, 3),
    )
    retained = read_dataset(
        provisional_root,
        KR_EQUITY_PRICE_PROVISIONAL_DAILY,
        validate_kr_equity_price_provisional_daily,
    )

    assert result["status"] == "CLEANED"
    assert result["removed_rows"] == 2
    assert set(retained["date"].astype(str)) == {"2026-09-03"}
