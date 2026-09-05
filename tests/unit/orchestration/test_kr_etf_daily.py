from datetime import date, datetime, timezone
import json

import pandas as pd
import pytest

from stock_data.contracts.kr_etf import KR_ETF_MASTER, KR_ETF_PRICE_DAILY
from stock_data.orchestration.kr_etf_daily import (
    KrEtfDailyError,
    normalize_master,
    normalize_prices,
    plan_kr_etf_symbol_windows,
    resolve_kr_etf_symbols,
    run_kr_etf_daily,
    run_kr_etf_scheduler_lane,
    validate_window,
)
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic
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
    def __init__(
        self, *, listed=("123320", "243880"), fail_if_called=False,
        frames=None,
    ):
        self._listed = listed
        self._calls = 0
        self.fail_if_called = fail_if_called
        self.frames = frames or {}

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
        return {
            "123320": "TIGER 레버리지",
            "243880": "TIGER 200 IT 레버리지",
            "0193M0": "테스트 ETF",
        }[symbol]

    def get_etf_ohlcv_by_date(self, start, end, symbol):
        self._count()
        return self.frames.get(symbol, _raw(symbol)).copy(deep=True)


def _one_row(symbol: str, value_date: date) -> pd.DataFrame:
    frame = _raw(symbol).iloc[[0]].copy()
    frame.index = pd.to_datetime([value_date.isoformat()])
    return frame


def _write_watchlist(tmp_path, *symbols: str) -> None:
    path = tmp_path / "artifacts/local_user/watchlists.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "lists": [{"items": [
            {"market": "KRX", "security_type": "ETF", "symbol": symbol}
            for symbol in symbols
        ]}],
    }), encoding="utf-8")


def _write_master(tmp_path, names: dict[str, str], source_date: date) -> None:
    write_dataset_atomic(
        normalize_master(names, source_date=source_date),
        tmp_path / "data/normalized/kr_etf_master",
        KR_ETF_MASTER,
        validate_kr_etf_master,
    )


def _write_prices(tmp_path, rows: dict[str, date]) -> None:
    frames = [
        normalize_prices(
            _one_row(symbol, value_date), symbol=symbol,
            start=value_date, end=value_date,
        )
        for symbol, value_date in rows.items()
    ]
    write_dataset_atomic(
        pd.concat(frames, ignore_index=True),
        tmp_path / "data/normalized/kr_etf_price_daily",
        KR_ETF_PRICE_DAILY,
        validate_kr_etf_price_daily,
    )


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


def test_scheduler_symbol_resolution_unions_watchlist_and_retained_master(tmp_path) -> None:
    _write_watchlist(tmp_path, "123320", "0193M0")
    _write_master(tmp_path, {"243880": "TIGER 200 IT 레버리지"}, date(2026, 9, 2))

    assert resolve_kr_etf_symbols(tmp_path) == ("0193M0", "123320", "243880")


def test_scheduler_symbol_resolution_keeps_watched_first_and_drops_master_leftovers_past_the_cap(
    tmp_path, monkeypatch,
) -> None:
    from stock_data.orchestration import kr_etf_daily

    # 2026-09-05 20:30: watchlist + master + held ETFs reached 12 > cap and the lane raised.
    monkeypatch.setattr(kr_etf_daily, "MAX_SYMBOLS", 4)
    _write_watchlist(tmp_path, "123320", "243880")
    _write_master(tmp_path, {
        "0015B0": "A", "329200": "B", "456600": "C", "123320": "TIGER 레버리지",
    }, date(2026, 9, 4))

    selected, dropped = kr_etf_daily.resolve_kr_etf_symbol_plan(tmp_path)

    assert selected == ("123320", "243880", "0015B0", "329200")
    assert dropped == ("456600",)
    assert kr_etf_daily.resolve_kr_etf_symbols(tmp_path) == selected


def test_scheduler_symbol_resolution_raises_a_receipt_safe_error_when_watched_exceed_the_cap(
    tmp_path, monkeypatch,
) -> None:
    from stock_data.orchestration import kr_etf_daily

    monkeypatch.setattr(kr_etf_daily, "MAX_SYMBOLS", 2)
    _write_watchlist(tmp_path, "123320", "243880", "139260")

    with pytest.raises(kr_etf_daily.KrEtfSelectionError, match="3 watched/held"):
        kr_etf_daily.resolve_kr_etf_symbols(tmp_path)
    assert issubclass(kr_etf_daily.KrEtfSelectionError, ValueError)


def test_scheduler_windows_cover_current_partial_and_empty_retained_symbols(tmp_path) -> None:
    target = date(2026, 9, 2)
    symbols = ("0193M0", "123320", "243880")
    _write_master(tmp_path, {
        "0193M0": "테스트 ETF", "123320": "TIGER 레버리지",
        "243880": "TIGER 200 IT 레버리지",
    }, target)
    _write_prices(tmp_path, {
        "123320": target,
        "243880": date(2026, 8, 28),
    })

    windows = {
        item.symbol: item
        for item in plan_kr_etf_symbol_windows(
            tmp_path, symbols=symbols, target_session=target,
        )
    }

    assert "123320" not in windows
    assert windows["243880"].latest_before == date(2026, 8, 28)
    assert windows["243880"].start == date(2026, 8, 31)
    assert windows["243880"].sessions[-1] == target
    assert windows["0193M0"].latest_before is None
    assert len(windows["0193M0"].sessions) == 30
    assert windows["0193M0"].sessions[-1] == target


def test_scheduler_lane_is_api_zero_when_every_symbol_is_current(tmp_path) -> None:
    target = date(2026, 9, 2)
    _write_watchlist(tmp_path, "123320")
    _write_master(tmp_path, {"123320": "TIGER 레버리지"}, target)
    _write_prices(tmp_path, {"123320": target})

    result = run_kr_etf_scheduler_lane(
        tmp_path,
        target_session=target,
        provider_factory=lambda: pytest.fail("current lane entered provider access"),
    )

    assert result == {
        "schema_version": 1,
        "lane": "KR_ETF_PRICE_DAILY",
        "status": "ALREADY_CURRENT",
        "target_session": "2026-09-02",
        "latest_before": {"123320": "2026-09-02"},
        "latest_after": {"123320": "2026-09-02"},
        "api_calls": 0,
        "retry_count": 0,
        "predictive_use": False,
        "symbols": ["123320"],
    }


def test_scheduler_lane_reports_provider_lag_and_retries_next_occurrence(tmp_path) -> None:
    target = date(2026, 9, 2)
    _write_watchlist(tmp_path, "123320")
    first_provider = OfflineProvider(
        listed=("123320",), frames={"123320": pd.DataFrame()},
    )

    first = run_kr_etf_scheduler_lane(
        tmp_path, target_session=target,
        provider_factory=lambda: first_provider,
    )

    assert first["status"] == "EXPECTED_PROVIDER_LAG"
    assert first["api_calls"] == 3
    assert first["latest_before"] == {"123320": None}
    assert first["latest_after"] == {"123320": None}
    assert target.isoformat() in first["provider_gap_dates"]["123320"]

    retry_provider = OfflineProvider(
        listed=("123320",), frames={"123320": pd.DataFrame()},
    )
    second = run_kr_etf_scheduler_lane(
        tmp_path, target_session=target,
        provider_factory=lambda: retry_provider,
    )
    assert second["status"] == "EXPECTED_PROVIDER_LAG"
    assert second["api_calls"] == 3


def test_master_merge_keeps_newest_source_date_per_symbol(tmp_path) -> None:
    from stock_data.orchestration.kr_etf_daily import _merge_master

    _write_master(tmp_path, {"123320": "TIGER 레버리지"}, date(2026, 9, 3))
    older = normalize_master({"123320": "TIGER 레버리지", "243880": "TIGER 200IT레버리지"}, source_date=date(2022, 6, 17))

    merged = _merge_master(tmp_path, older)

    by_symbol = merged.set_index("symbol")["source_date"].astype(str)
    assert by_symbol["123320"] == "2026-09-03"  # a historical backfill must not roll the master back
    assert by_symbol["243880"] == "2022-06-17"  # new symbols enter with their own date
    assert len(merged) == 2
