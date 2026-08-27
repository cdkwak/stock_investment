from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from stock_data.gui.services import (
    DashboardDisplayState,
    EquityIdentity,
    EquitySeriesView,
    US_ETF_CHART_IDENTITIES,
)
from stock_data.gui.watchlist_service import (
    DEFAULT_LIST_ID,
    LocalWatchlistService,
    quote_from_series,
)


def _identity(
    symbol: str = "005930", name: str = "삼성전자", market: str = "KOSPI",
) -> EquityIdentity:
    return EquityIdentity(symbol, name, market, f"KR7{symbol}03", "1975-06-11", "보통주")


def _service(path: Path) -> LocalWatchlistService:
    return LocalWatchlistService(
        path,
        clock=lambda: datetime(2026, 8, 20, 16, 0, tzinfo=ZoneInfo("Asia/Seoul")),
    )


def test_watchlists_persist_exact_identity_without_duplicates_and_reorder(tmp_path):
    service = _service(tmp_path / "artifacts/local_user/watchlists.json")
    assert service.load().default_list.name == "관심종목"
    samsung = _identity()
    hynix = _identity("000660", "SK하이닉스")

    state = service.add_item(DEFAULT_LIST_ID, samsung)
    assert service.add_item(DEFAULT_LIST_ID, samsung).revision == state.revision
    state = service.add_item(DEFAULT_LIST_ID, hynix)
    state = service.move_item(DEFAULT_LIST_ID, hynix.key, -1)
    assert [item.key for item in state.default_list.items] == [hynix.key, samsung.key]

    state = service.create_list("반도체")
    second = state.lists[-1]
    state = service.add_item(second.list_id, samsung)
    state = service.rename_list(second.list_id, "반도체 핵심")
    state = service.move_list(second.list_id, -1)
    loaded = service.load()
    assert loaded.lists[0].name == "반도체 핵심"
    assert loaded.lists[0].items[0].identity == samsung
    assert loaded.default_list.items[1].identity == samsung


def test_failed_atomic_replace_keeps_last_valid_configuration(tmp_path, monkeypatch):
    path = tmp_path / "artifacts/local_user/watchlists.json"
    service = _service(path)
    service.add_item(DEFAULT_LIST_ID, _identity())
    before = path.read_bytes()
    real_replace = __import__("os").replace

    def fail_primary(source, target):
        if Path(target) == path:
            raise OSError("fixture replace failure")
        return real_replace(source, target)

    monkeypatch.setattr("stock_data.gui.watchlist_service.os.replace", fail_primary)
    with pytest.raises(OSError, match="fixture replace failure"):
        service.add_item(DEFAULT_LIST_ID, _identity("000660", "SK하이닉스"))
    assert path.read_bytes() == before
    assert [item.identity.symbol for item in service.load().default_list.items] == ["005930"]


def test_corrupt_primary_recovers_backup_and_legacy_migrates_without_identity_guess(tmp_path):
    path = tmp_path / "watchlists.json"
    service = _service(path)
    service.add_item(DEFAULT_LIST_ID, _identity())
    service.add_item(DEFAULT_LIST_ID, _identity("000660", "SK하이닉스"))
    path.write_text("{broken", encoding="utf-8")
    recovered = service.load()
    assert recovered.recovered_from_backup is True
    assert [item.identity.symbol for item in recovered.default_list.items] == ["005930"]
    repaired = service.remove_item(DEFAULT_LIST_ID, ("KOSPI", "005930"))
    assert repaired.default_list.items == ()
    assert service.load().recovered_from_backup is False

    legacy = {
        "schema_version": 0,
        "lists": [{
            "name": "관심종목",
            "items": [{
                "market": "KOSDAQ", "symbol": "035720", "name": "카카오",
                "isin": "KR7035720002", "listing_date": "2017-07-10",
                "security_type": "보통주",
            }],
        }],
    }
    path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
    migrated = service.load()
    assert migrated.migration_required is True
    assert migrated.default_list.items[0].identity.key == ("KOSDAQ", "035720")


def test_watchlist_persists_only_exact_accepted_us_etf_identity(tmp_path):
    service = _service(tmp_path / "watchlists.json")
    spy = next(identity for identity in US_ETF_CHART_IDENTITIES if identity.symbol == "SPY")

    saved = service.add_item(DEFAULT_LIST_ID, spy)
    loaded = service.load()

    assert saved.default_list.items[0].identity == spy
    assert loaded.default_list.items[0].identity.identity_source == spy.identity_source
    assert loaded.default_list.items[0].identity.currency == "USD"

    spoofed = EquityIdentity(
        symbol="SPY", name="Not SPY", market="US ETF", isin=None,
        listing_date=spy.listing_date, security_type="ETF", issuer=spy.issuer,
        exposure=spy.exposure, currency="USD", leverage_style=spy.leverage_style,
        distribution_style=spy.distribution_style, identity_source=spy.identity_source,
    )
    with pytest.raises(ValueError, match="does not match the accepted catalog"):
        service.add_item(DEFAULT_LIST_ID, spoofed)


def test_ineligible_series_quote_hides_all_numeric_values():
    identity = _identity()
    unavailable = EquitySeriesView(
        identity=identity,
        period="20D",
        frame=pd.DataFrame({"close": [999999]}),
        display_state=DashboardDisplayState.REFRESH_REQUIRED,
        freshness="STALE",
        as_of="2026-08-18",
        expected_as_of="2026-08-19",
        source="fixture",
        reference_kst="2026-08-18 KST 일봉",
        unavailable_reason="기준일이 오래되었습니다.",
        change=1234,
        change_pct=9.9,
    )
    quote = quote_from_series(unavailable)
    assert quote.price is None
    assert quote.change is None
    assert quote.change_pct is None
    assert quote.reference_kst is None
    assert quote.unavailable_reason == "기준일이 오래되었습니다."


def test_eligible_series_quote_exposes_bounded_recent_flow_without_persistence():
    identity = _identity()
    closes = [100.0 + value for value in range(21)]
    view = EquitySeriesView(
        identity=identity,
        period="20D",
        frame=pd.DataFrame({"close": closes}),
        display_state=DashboardDisplayState.VALUE,
        freshness="CURRENT",
        as_of="2026-08-20",
        expected_as_of="2026-08-20",
        source="fixture",
        reference_kst="2026-08-20 KST 일봉",
        change=1.0,
        change_pct=0.84,
    )

    quote = quote_from_series(view)

    assert quote.recent_closes == tuple(closes[-20:])
    assert quote.five_session_pct == pytest.approx((120 / 115 - 1) * 100)
    assert quote.recent_period_pct == pytest.approx((120 / 101 - 1) * 100)
