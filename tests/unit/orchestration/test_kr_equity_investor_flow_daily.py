from datetime import date, datetime, timezone
import json

import pandas as pd
import pytest

from stock_data.contracts.kr_equity_investor_flow import (
    KR_EQUITY_INVESTOR_FLOW_DAILY,
)
from stock_data.orchestration.kr_equity_investor_flow_daily import (
    plan_kr_equity_investor_flow_daily,
    resolve_kr_equity_investor_flow_symbols,
    run_kr_equity_investor_flow_daily,
    run_kr_equity_investor_flow_scheduler_lane,
    validate_window,
)
from stock_data.providers.pykrx.kr_equity_investor import normalize_investor_flow
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic
from stock_data.validation.kr_equity_investor_flow import (
    validate_kr_equity_investor_flow,
)


def _raw(dates: list[date], offset: int = 0) -> pd.DataFrame:
    count = len(dates)
    return pd.DataFrame(
        {
            "기관합계": [100 + offset] * count,
            "기타법인": [20] * count,
            "개인": [-70] * count,
            "외국인합계": [-50 - offset] * count,
            "전체": [0] * count,
        },
        index=pd.to_datetime([value.isoformat() for value in dates]),
    )


class OfflineProvider:
    def __init__(self, frames=None, *, fail_if_called=False):
        self.frames = frames or {}
        self.fail_if_called = fail_if_called
        self._calls = 0

    @property
    def request_count(self):
        return self._calls

    def get_market_trading_value_by_date(self, start, end, symbol):
        if self.fail_if_called:
            raise AssertionError("idempotent replay must not call pykrx")
        self._calls += 1
        return self.frames.get(symbol, _raw([start, end] if start != end else [start])).copy()


def _write_watchlist(root, items):
    path = root / "artifacts/local_user/watchlists.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": 1,
        "revision": 1,
        "lists": [{
            "list_id": "favorites",
            "name": "관심종목",
            "items": [
                {
                    "market": market,
                    "symbol": symbol,
                    "name": f"name-{symbol}",
                    "security_type": security_type,
                }
                for market, symbol, security_type in items
            ],
        }],
    }, ensure_ascii=False), encoding="utf-8")


def _write_retained(root, symbol: str, dates: list[date]) -> None:
    frame = normalize_investor_flow(
        _raw(dates),
        symbol=symbol,
        start=min(dates),
        end=max(dates),
        captured_at=datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc),
    )
    write_dataset_atomic(
        frame,
        root / "data/normalized/kr_equity_investor_flow_daily",
        KR_EQUITY_INVESTOR_FLOW_DAILY,
        validate_kr_equity_investor_flow,
    )


def test_landing_first_atomic_promotion_receipt_and_api_zero_replay(tmp_path) -> None:
    provider = OfflineProvider({
        "005930": _raw([date(2026, 9, 3), date(2026, 9, 4)]),
        "000660": _raw([date(2026, 9, 3), date(2026, 9, 4)], offset=10),
    })
    result = run_kr_equity_investor_flow_daily(
        tmp_path,
        symbols=("005930", "000660"),
        start=date(2026, 9, 3),
        end=date(2026, 9, 4),
        provider=provider,
        run_id="offline-fixture",
        now=datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc),
    )

    assert result["status"] == "SUCCEEDED"
    assert result["provider_calls"] == 2
    assert result["rows"] == 4
    landing = tmp_path / "data/landing/kr_equity_investor_flow_daily/offline-fixture"
    assert (landing / "symbol=005930.json").is_file()
    checkpoint = json.loads((landing / "checkpoint.json").read_text(encoding="utf-8"))
    assert checkpoint["max_provider_calls"] == checkpoint["provider_calls"] == 2
    assert checkpoint["normalized_writes"] == ["kr_equity_investor_flow_daily"]
    stored = read_dataset(
        tmp_path / "data/normalized/kr_equity_investor_flow_daily",
        KR_EQUITY_INVESTOR_FLOW_DAILY,
        validate_kr_equity_investor_flow,
    )
    assert stored.groupby("symbol").size().to_dict() == {"000660": 2, "005930": 2}

    replay = run_kr_equity_investor_flow_daily(
        tmp_path,
        symbols=("005930", "000660"),
        start=date(2026, 9, 3),
        end=date(2026, 9, 4),
        provider=OfflineProvider(fail_if_called=True),
    )
    assert replay["status"] == "NOOP_ALREADY_SUCCEEDED"
    assert replay["provider_calls"] == 0


def test_symbol_resolution_filters_watchlist_types_unions_retained_and_caps_40(tmp_path) -> None:
    watchlist = [
        ("KOSPI" if index % 2 == 0 else "KOSDAQ", f"{index:06d}",
         "보통주" if index % 3 else "우선주")
        for index in range(42)
    ] + [
        ("KOSPI", "900001", "ETF"),
        ("KRX", "123320", "ETF"),
    ]
    _write_watchlist(tmp_path, watchlist)
    _write_retained(tmp_path, "999999", [date(2026, 9, 4)])

    symbols = resolve_kr_equity_investor_flow_symbols(tmp_path)

    assert len(symbols) == 40
    assert symbols == tuple(f"{index:06d}" for index in range(40))
    assert "900001" not in symbols and "123320" not in symbols
    plan = plan_kr_equity_investor_flow_daily(
        tmp_path, target_session=date(2026, 9, 4),
    )
    assert plan.estimated_calls == 40
    assert plan.planned_symbols == symbols


def test_symbol_resolution_includes_retained_symbol_when_capacity_remains(tmp_path) -> None:
    _write_watchlist(tmp_path, [
        ("KOSPI", "005930", "보통주"),
        ("KOSDAQ", "000660", "우선주"),
    ])
    _write_retained(tmp_path, "999999", [date(2026, 9, 4)])

    assert resolve_kr_equity_investor_flow_symbols(tmp_path) == (
        "000660", "005930", "999999",
    )


def test_scheduler_uses_five_sessions_and_is_api_zero_when_window_retained(tmp_path) -> None:
    target = date(2026, 9, 4)
    _write_watchlist(tmp_path, [("KOSPI", "005930", "보통주")])
    sessions = [
        date(2026, 8, 31), date(2026, 9, 1), date(2026, 9, 2),
        date(2026, 9, 3), date(2026, 9, 4),
    ]
    _write_retained(tmp_path, "005930", sessions)

    plan = plan_kr_equity_investor_flow_daily(tmp_path, target_session=target)
    assert plan.sessions == tuple(sessions)
    assert plan.planned_symbols == ()
    result = run_kr_equity_investor_flow_scheduler_lane(
        tmp_path,
        target_session=target,
        provider_factory=lambda: pytest.fail("retained lane entered provider access"),
    )
    assert result["status"] == "ALREADY_CURRENT"
    assert result["estimated_calls"] == result["api_calls"] == 0


def test_provider_gap_is_not_marked_idempotently_complete(tmp_path) -> None:
    provider = OfflineProvider({
        "005930": _raw([date(2026, 9, 3)]),
    })
    first = run_kr_equity_investor_flow_daily(
        tmp_path,
        symbols=("005930",),
        start=date(2026, 9, 3),
        end=date(2026, 9, 4),
        provider=provider,
        run_id="provider-gap",
    )
    assert first["status"] == "SUCCEEDED_WITH_PROVIDER_GAPS"

    retry_provider = OfflineProvider({
        "005930": _raw([date(2026, 9, 3), date(2026, 9, 4)]),
    })
    second = run_kr_equity_investor_flow_daily(
        tmp_path,
        symbols=("005930",),
        start=date(2026, 9, 3),
        end=date(2026, 9, 4),
        provider=retry_provider,
        run_id="provider-gap-retry",
    )
    assert second["status"] == "SUCCEEDED"
    assert retry_provider.request_count == 1


def test_manual_window_allows_one_year_but_not_more() -> None:
    assert validate_window(date(2025, 9, 5), date(2026, 9, 4)) == 365
    with pytest.raises(ValueError, match="1..366"):
        validate_window(date(2025, 9, 3), date(2026, 9, 4))
