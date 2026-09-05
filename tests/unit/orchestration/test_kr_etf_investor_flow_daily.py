from __future__ import annotations

from datetime import date, datetime, timezone
import json

import pandas as pd

from stock_data.contracts.kr_etf_investor_flow import (
    KR_ETF_INVESTOR_FLOW_DAILY,
)
from stock_data.orchestration.exchange_calendar import (
    ExchangeMarket,
    ExchangeTradingCalendar,
)
from stock_data.orchestration.kr_etf_investor_flow_daily import (
    normalize_etf_investor_flow,
    plan_kr_etf_investor_flow_daily,
    run_kr_etf_investor_flow_daily,
    validate_kr_etf_investor_flow,
)
from stock_data.storage.contract_parquet import read_dataset
from tests.unit.web import new_temp_root


ETF_SYMBOLS = ("123320", "139260", "243880")


def _raw(start: date, end: date) -> pd.DataFrame:
    sessions = ExchangeTradingCalendar(ExchangeMarket.KR).sessions_in_range(start, end)
    return pd.DataFrame(
        {
            "기관": [100] * len(sessions),
            "기타법인": [20] * len(sessions),
            "개인": [-70] * len(sessions),
            "외국인": [-50] * len(sessions),
            "전체": [0] * len(sessions),
        },
        index=pd.to_datetime([value.isoformat() for value in sessions]),
    )


class OfflineProvider:
    def __init__(self, *, fail_if_called: bool = False) -> None:
        self._calls = 0
        self.fail_if_called = fail_if_called

    @property
    def request_count(self) -> int:
        return self._calls

    def get_etf_investor_flow_by_date(
        self, start: date, end: date, symbol: str,
    ) -> pd.DataFrame:
        if self.fail_if_called:
            raise AssertionError("provider must not be called")
        self._calls += 1
        return _raw(start, end)


def _write_watchlist(root) -> None:
    path = root / "artifacts/local_user/watchlists.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "schema_version": 1,
        "revision": 1,
        "lists": [{
            "list_id": "favorites",
            "name": "관심종목",
            "items": [
                {
                    "market": "KRX", "symbol": symbol, "name": f"ETF-{symbol}",
                    "security_type": "ETF",
                }
                for symbol in ETF_SYMBOLS
            ] + [{
                "market": "KOSPI", "symbol": "005930", "name": "삼성전자",
                "security_type": "보통주",
            }],
        }],
    }, ensure_ascii=False), encoding="utf-8")


def test_normalization_maps_exact_etf_participant_columns_to_won_contract() -> None:
    frame = normalize_etf_investor_flow(
        _raw(date(2026, 9, 3), date(2026, 9, 4)),
        symbol="123320",
        start=date(2026, 9, 3),
        end=date(2026, 9, 4),
        retrieved_at=datetime(2026, 9, 4, 12, tzinfo=timezone.utc),
    )

    assert frame.iloc[0][[
        "symbol", "institution_net_krw", "other_corporation_net_krw",
        "individual_net_krw", "foreign_net_krw", "total_net_krw", "provider",
    ]].tolist() == ["123320", 100, 20, -70, -50, 0, "pykrx"]
    assert all(frame[column].dtype == "int64" for column in (
        "institution_net_krw", "other_corporation_net_krw",
        "individual_net_krw", "foreign_net_krw", "total_net_krw",
    ))
    validate_kr_etf_investor_flow(frame)


def test_plan_reuses_selected_etf_universe_and_splits_every_call_to_ten_days() -> None:
    root = new_temp_root()
    _write_watchlist(root)

    plan = plan_kr_etf_investor_flow_daily(
        root,
        start=date(2026, 8, 17),
        end=date(2026, 9, 4),
    )

    assert plan.symbols == ETF_SYMBOLS
    assert plan.estimated_calls == 6
    assert [(window.start, window.end) for window in plan.windows[:2]] == [
        (date(2026, 8, 17), date(2026, 8, 26)),
        (date(2026, 8, 27), date(2026, 9, 4)),
    ]
    assert all((window.end - window.start).days + 1 <= 10 for window in plan.windows)


def test_landing_atomic_receipt_and_retained_idempotency() -> None:
    root = new_temp_root()
    _write_watchlist(root)
    provider = OfflineProvider()
    result = run_kr_etf_investor_flow_daily(
        root,
        start=date(2026, 8, 17),
        end=date(2026, 9, 4),
        provider=provider,
        confirm_live=True,
        run_id="offline-fixture",
        now=datetime(2026, 9, 4, 12, tzinfo=timezone.utc),
    )

    assert result["status"] == "COMPLETE"
    assert result["api_calls"] == result["estimated_calls"] == 6
    assert len(result["landing_files"]) == 6
    assert all(item["sha256"] for item in result["landing_files"])
    assert (root / result["receipt_path"]).is_file()
    stored = read_dataset(
        root / "data/normalized/kr_etf_investor_flow_daily",
        KR_ETF_INVESTOR_FLOW_DAILY,
        validate_kr_etf_investor_flow,
    )
    assert set(stored["symbol"]) == set(ETF_SYMBOLS)

    replay = run_kr_etf_investor_flow_daily(
        root,
        start=date(2026, 8, 17),
        end=date(2026, 9, 4),
        provider=OfflineProvider(fail_if_called=True),
        confirm_live=True,
    )
    assert replay["status"] == "NOOP_ALREADY_CURRENT"
    assert replay["api_calls"] == replay["estimated_calls"] == 0


def test_dry_run_is_provider_free_and_does_not_write_receipt() -> None:
    root = new_temp_root()
    _write_watchlist(root)

    result = run_kr_etf_investor_flow_daily(
        root,
        start=date(2026, 8, 26),
        end=date(2026, 9, 4),
        provider=OfflineProvider(fail_if_called=True),
        dry_run=True,
    )

    assert result["status"] == "DRY_RUN_PASS"
    assert result["api_calls"] == 0
    assert result["estimated_calls"] == 3
    assert not (
        root
        / "artifacts/scheduler_logs/STOCK_DATA_KR_ETF_INVESTOR_FLOW_DAILY_last.json"
    ).exists()
