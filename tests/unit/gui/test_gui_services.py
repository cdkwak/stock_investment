from __future__ import annotations

import json
import os
import warnings
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from stock_data.gui.query import LocalParquetQuery
from stock_data.gui.current_display import (
    CurrentDisplayObservation,
    DashboardCurrentObservation,
    promote_current_display,
    promote_dashboard_current,
)
from stock_data.gui.account_snapshot_service import (
    AccountAssetPoint,
    AccountCurrencySummaryView,
    AccountPortfolioEntryView,
    AccountPortfolioView,
    AccountPositionView,
    AccountSnapshotState,
    AccountSnapshotView,
    LocalAccountPortfolioService,
    LocalAccountSnapshotService,
    manual_account_snapshot_to_portfolio,
    LocalAccountSourceSpec,
    build_account_portfolio_presentation,
    build_account_source_action_views,
)
from stock_data.gui.manual_account_snapshot import (
    parse_manual_account_snapshot,
)
from stock_data.gui.health_service import DailyHealthArtifactService, HealthArtifactView
from stock_data.providers.kbsec.account import normalize_domestic_balance_payload
from stock_data.contracts.market_60m import MARKET_PRICE_60M_OBSERVATION
from stock_data.contracts.market_15m import MARKET_PRICE_15M_OBSERVATION
from stock_data.contracts.kospi200_constituent_breadth import KR_KOSPI200_BREADTH_DAILY
from stock_data.contracts.kr_index_fundamental_daily import KR_INDEX_FUNDAMENTAL_DAILY
from stock_data.contracts.ls_t1633 import LS_T1633_PROGRAM_TRADING_DAILY
from stock_data.gui.services import (
    classify_current_display_timestamp,
    classify_intraday_60m_freshness,
    DashboardCurrentStageView,
    DashboardChartCoverage,
    DASHBOARD_CHART_COVERAGE_ATTR,
    DashboardDisplayState,
    DashboardMetricView,
    DashboardSeriesView,
    DashboardSparklineView,
    DashboardService,
    DerivativesDashboardService,
    EquityChartService,
    EquityIdentity,
    EquitySeriesView,
    instrument_facts_view,
    IndexSeriesView,
    NormalizedBenchmarkComparisonView,
    US_ETF_CHART_IDENTITIES,
    USEtfChartService,
    IndexQueryService,
    MarketMicrostructureService,
    MarketInvestorFlowView,
    LS_T8412_CURRENT_OBSERVATION_PATH,
    LS_T8412_CURRENT_ROUTE,
    LS_T8412_CURRENT_SAFE_REASON,
    NAVER_WEB_000660_CURRENT_OBSERVATION_PATH,
    NAVER_WEB_000660_CURRENT_ROUTE,
    NAVER_WEB_000660_PROVENANCE_WARNING,
    NASDAQ_SOXX_INFO_CURRENT_OBSERVATION_PATH,
    NASDAQ_SOXX_INFO_CURRENT_ROUTE,
    NASDAQ_SOXX_INFO_CURRENT_SAFE_REASON,
    NAVER_MOBILE_BASIC_000660_UR199_OBSERVATION_PATH,
    NAVER_MOBILE_BASIC_000660_UR199_ROUTE,
    NAVER_MOBILE_BASIC_005930_UR199_OBSERVATION_PATH,
    NAVER_MOBILE_BASIC_005930_UR199_ROUTE,
    NAVER_MOBILE_BASIC_UR199_SAFE_REASON,
    TOSS_DOMESTIC_UR246_SAFE_REASON,
    TOSS_000660_NXT_CLOSE_UR240_OBSERVATION_PATH,
    TOSS_000660_NXT_CLOSE_UR240_ROUTE,
    TOSS_005930_NXT_CLOSE_UR241_OBSERVATION_PATH,
    TOSS_005930_NXT_CLOSE_UR241_ROUTE,
    _toss_domestic_ur246_path,
    _toss_domestic_ur246_route,
    short_selling_scope_regime,
    technical_indicators,
    PERIOD_ROWS,
)
from stock_data.storage.contract_parquet import write_dataset_atomic
from stock_data.validation.market_60m import validate_market_price_60m
from stock_data.validation.market_15m import validate_market_price_15m
from stock_data.validation.ls_t1633 import (
    normalize_ls_t1633_market_pair,
    validate_ls_t1633_program_trading,
)
from stock_data.validation.kospi200_constituent_breadth import validate_kospi200_breadth_daily
from stock_data.validation.kr_index_fundamental_daily import (
    validate_kr_index_fundamental_daily,
)


def test_instrument_facts_use_only_accepted_identity_and_view_metadata():
    identity = next(item for item in US_ETF_CHART_IDENTITIES if item.symbol == "SPY")
    view = EquitySeriesView(
        identity=identity,
        period="120D",
        frame=pd.DataFrame(),
        display_state=DashboardDisplayState.PROHIBITED,
        freshness="BLOCKED",
        as_of=None,
        expected_as_of="2026-08-24",
        source="accepted local scope: SOXX only",
        reference_kst=None,
        unavailable_reason="outside accepted scope",
    )

    facts = instrument_facts_view(view)

    assert facts.identity_line == "SPDR S&P 500 ETF Trust · SPY · US ETF · ETF"
    assert facts.market_line == "통화 USD · 세션 미확인 · 상태 BLOCKED"
    assert facts.source_line.endswith("기준 미확인 · 가격기준 PROVIDER_NATIVE_ORIGINAL_PRICE")
    assert "State Street SPDR" in facts.risk_line
    assert "비레버리지 지수 추종" in facts.risk_line
    assert "보수·분배율·유동성 순위·52주 범위·KRW 환산" in facts.unsupported_line
    assert not facts.displays_price_facts


def test_instrument_facts_reject_non_series_input():
    with pytest.raises(TypeError, match="equity series view"):
        instrument_facts_view(object())


def test_instrument_facts_do_not_infer_korean_currency_and_reject_incomplete_identity():
    identity = EquityIdentity(
        "005930", "삼성전자", "KOSPI", "KR7005930003", "1975-06-11", "STOCK",
    )
    view = EquitySeriesView(
        identity, "120D", pd.DataFrame(), DashboardDisplayState.UNAVAILABLE,
        "UNKNOWN", None, None, "local canonical equity master", None,
    )
    assert "통화 미보존" in instrument_facts_view(view).market_line

    with pytest.raises(ValueError, match="complete accepted identity"):
        instrument_facts_view(replace(view, identity=replace(identity, name="")))


def test_instrument_facts_reject_spoofed_us_etf_identity():
    canonical = next(item for item in US_ETF_CHART_IDENTITIES if item.symbol == "SPY")
    spoofed = replace(canonical, issuer="Spoofed issuer")
    view = EquitySeriesView(
        spoofed, "120D", pd.DataFrame(), DashboardDisplayState.PROHIBITED,
        "BLOCKED", None, None, "accepted local scope", None,
    )
    with pytest.raises(ValueError, match="unaccepted U.S. ETF"):
        instrument_facts_view(view)


def _write(root: Path, dataset: str, year: int, frame: pd.DataFrame, market: str | None = None) -> None:
    path = root / dataset
    if market:
        path /= f"market={market}"
    path /= f"year={year}"
    path.mkdir(parents=True)
    frame.to_parquet(path / "data.parquet", index=False)


def _market_valuation_frame() -> pd.DataFrame:
    rows = []
    values = {
        "1001": ((10.0, 1.0), (20.0, 2.0), (40.0, 4.0)),
        "2001": ((25.0, 2.5), (10.0, 1.0), (5.0, 0.5)),
    }
    for date_index, market_date in enumerate((
        "2026-08-21", "2026-08-24", "2026-08-25",
    )):
        for index_code, market in (("1001", "KOSPI"), ("2001", "KOSDAQ")):
            per, pbr = values[index_code][date_index]
            rows.append({
                "date": market_date,
                "index_code": index_code,
                "market": market,
                "close": 3000.0 if market == "KOSPI" else 900.0,
                "weighted_per": per,
                "weighted_pbr": pbr,
                "dividend_yield": 1.0,
                "source": "KRX_MDCSTAT00702",
                "source_response_sha256": (
                    "a" * 64 if market == "KOSPI" else "b" * 64
                ),
            })
    return pd.DataFrame(
        rows, columns=KR_INDEX_FUNDAMENTAL_DAILY.column_names,
    )


def _write_market_valuation_contract(root: Path, frame: pd.DataFrame) -> None:
    write_dataset_atomic(
        frame,
        root / "data/normalized/kr_index_fundamental_daily",
        KR_INDEX_FUNDAMENTAL_DAILY,
        validate_kr_index_fundamental_daily,
    )
    state = root / "data/state/kr_index_fundamental_daily.json"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text(json.dumps({
        "schema_version": 1,
        "status": "ACCEPTED_DESCRIPTIVE_NON_PREDICTIVE",
        "last_accepted_market_date": "2026-08-25",
        "rows": len(frame),
        "predictive_eligibility": "NON_PREDICTIVE",
    }), encoding="utf-8")


def test_market_valuation_views_read_exact_contract_and_describe_history(tmp_path):
    frame = _market_valuation_frame()
    _write_market_valuation_contract(tmp_path, frame)
    before = {
        path: path.read_bytes()
        for path in (tmp_path / "data").rglob("*") if path.is_file()
    }

    views = DashboardService(tmp_path).market_valuation_views(
        now_utc="2026-08-26T00:30:00+00:00",
    )

    kospi, kosdaq = views["KOSPI"], views["KOSDAQ"]
    assert (kospi.index_code, kosdaq.index_code) == ("1001", "2001")
    assert kospi.as_of == kosdaq.as_of == "2026-08-25"
    assert kospi.weighted_per == 40.0 and kospi.per_median == 20.0
    assert kospi.per_mean == pytest.approx(70 / 3)
    assert kospi.per_percentile == 100.0
    assert kosdaq.weighted_per == 5.0 and kosdaq.per_median == 10.0
    assert kosdaq.per_mean == pytest.approx(40 / 3)
    assert kosdaq.per_percentile == pytest.approx(100 / 3)
    assert kospi.baseline_start == "2026-08-21"
    assert (
        kospi.per_baseline_start, kospi.per_baseline_end,
        kospi.per_observations,
    ) == ("2026-08-21", "2026-08-25", 3)
    assert (
        kospi.pbr_baseline_start, kospi.pbr_baseline_end,
        kospi.pbr_observations,
    ) == ("2026-08-21", "2026-08-25", 3)
    assert tuple(window.window_years for window in kospi.rolling_windows) == (5, 10)
    assert all(
        window.per_percentile == 100.0
        and window.pbr_percentile == 100.0
        and window.per_observations == 3
        and window.pbr_observations == 3
        for window in kospi.rolling_windows
    )
    assert kospi.pit_status == "NON_PREDICTIVE"
    assert all(view.display_state is DashboardDisplayState.VALUE for view in views.values())
    after = {
        path: path.read_bytes()
        for path in (tmp_path / "data").rglob("*") if path.is_file()
    }
    assert after == before


def test_market_valuation_views_keep_5y_10y_percentiles_distinct_and_as_of_only():
    frame = _market_valuation_frame()
    older = []
    for market_date, kospi_value, kosdaq_value in (
        ("2010-08-25", (100.0, 10.0), (50.0, 5.0)),
        ("2018-08-24", (80.0, 8.0), (40.0, 4.0)),
    ):
        for index_code, market, values in (
            ("1001", "KOSPI", kospi_value),
            ("2001", "KOSDAQ", kosdaq_value),
        ):
            older.append({
                "date": market_date,
                "index_code": index_code,
                "market": market,
                "close": 3000.0 if market == "KOSPI" else 900.0,
                "weighted_per": values[0],
                "weighted_pbr": values[1],
                "dividend_yield": 1.0,
                "source": "KRX_MDCSTAT00702",
                "source_response_sha256": (
                    "a" * 64 if market == "KOSPI" else "b" * 64
                ),
            })
    candidate = pd.concat([
        pd.DataFrame(older, columns=KR_INDEX_FUNDAMENTAL_DAILY.column_names),
        frame,
    ], ignore_index=True)

    view = DashboardService.build_market_valuation_views(
        candidate, as_of="2026-08-25", expected_as_of="2026-08-25",
    )["KOSPI"]
    windows = {window.window_years: window for window in view.rolling_windows}

    assert view.per_percentile == 60.0
    assert windows[5].per_percentile == 100.0
    assert windows[5].per_observations == 3
    assert windows[5].per_baseline_start == "2026-08-21"
    assert windows[10].per_percentile == 75.0
    assert windows[10].per_observations == 4
    assert windows[10].per_baseline_start == "2018-08-24"
    assert windows[10].per_baseline_end == "2026-08-25"


def test_market_valuation_views_ignore_malformed_values_after_as_of():
    frame = _market_valuation_frame()
    future = frame.loc[frame["date"].eq("2026-08-25")].copy()
    future["date"] = "2026-08-26"
    future["weighted_per"] = future["weighted_per"].astype(object)
    future.loc[future["index_code"].eq("1001"), "weighted_per"] = "malformed"
    candidate = pd.concat([frame, future], ignore_index=True)

    views = DashboardService.build_market_valuation_views(
        candidate, as_of="2026-08-25", expected_as_of="2026-08-25",
    )

    assert all(
        view.display_state is DashboardDisplayState.VALUE
        and view.as_of == "2026-08-25"
        for view in views.values()
    )
    assert views["KOSPI"].weighted_per == 40.0


def test_market_valuation_views_follow_provider_availability_after_market_close(
    tmp_path,
):
    _write_market_valuation_contract(tmp_path, _market_valuation_frame())
    service = DashboardService(tmp_path)

    expected_lag = service.market_valuation_views(
        now_utc="2026-08-26T11:31:00+00:00",
    )
    assert all(
        view.display_state is DashboardDisplayState.VALUE
        and view.as_of == "2026-08-25"
        and view.expected_as_of == "2026-08-25"
        for view in expected_lag.values()
    )

    due = service.market_valuation_views(
        now_utc="2026-08-27T00:10:00+00:00",
    )
    assert all(
        view.display_state is DashboardDisplayState.REFRESH_REQUIRED
        and view.weighted_per is None
        and view.weighted_pbr is None
        and view.as_of == "2026-08-25"
        and view.expected_as_of == "2026-08-26"
        for view in due.values()
    )


def test_market_valuation_history_excludes_rows_after_selected_as_of():
    frame = _market_valuation_frame()
    future = frame.loc[frame["date"].eq("2026-08-25")].copy()
    future["date"] = "2026-08-26"
    future["weighted_per"] = 999.0
    future["weighted_pbr"] = 99.0
    candidate = pd.concat([frame, future], ignore_index=True)

    views = DashboardService.build_market_valuation_views(
        candidate, as_of="2026-08-25", expected_as_of="2026-08-25",
    )

    assert views["KOSPI"].per_median == 20.0
    assert views["KOSPI"].pbr_median == 2.0
    assert views["KOSPI"].per_mean == pytest.approx(70 / 3)
    assert views["KOSPI"].pbr_mean == pytest.approx(7 / 3)
    assert views["KOSPI"].per_observations == 3


@pytest.mark.parametrize("mutation", [
    lambda frame: pd.concat([frame, frame.iloc[[0]]], ignore_index=True),
    lambda frame: frame.assign(index_code="9999"),
    lambda frame: frame.loc[~(
        frame["date"].eq("2026-08-24") & frame["market"].eq("KOSDAQ")
    )].reset_index(drop=True),
    lambda frame: frame.assign(weighted_per=float("inf")),
])
def test_market_valuation_views_fail_closed_for_malformed_contract(mutation):
    views = DashboardService.build_market_valuation_views(
        mutation(_market_valuation_frame()),
        as_of="2026-08-25",
        expected_as_of="2026-08-25",
    )
    assert all(
        view.display_state is DashboardDisplayState.UNAVAILABLE
        and view.weighted_per is None and view.weighted_pbr is None
        for view in views.values()
    )


def test_market_valuation_views_preserve_latest_provider_null_per_independently():
    frame = _market_valuation_frame()
    frame.loc[
        frame["date"].eq("2026-08-25") & frame["market"].eq("KOSPI"),
        "weighted_per",
    ] = None
    views = DashboardService.build_market_valuation_views(
        frame, as_of="2026-08-25", expected_as_of="2026-08-25",
    )
    assert not views["KOSPI"].displays_per
    assert views["KOSPI"].weighted_per is None
    assert views["KOSPI"].per_mean is None
    assert (
        views["KOSPI"].per_baseline_start,
        views["KOSPI"].per_baseline_end,
        views["KOSPI"].per_observations,
    ) == ("2026-08-21", "2026-08-24", 2)
    assert views["KOSPI"].displays_pbr
    assert views["KOSDAQ"].displays_per


def test_market_valuation_views_report_metric_specific_null_safe_coverage():
    frame = _market_valuation_frame()
    frame.loc[
        frame["date"].eq("2026-08-21") & frame["market"].eq("KOSPI"),
        "weighted_per",
    ] = None

    view = DashboardService.build_market_valuation_views(
        frame, as_of="2026-08-25", expected_as_of="2026-08-25",
    )["KOSPI"]

    assert view.displays_per and view.displays_pbr
    assert view.per_mean == 30.0
    assert (
        view.per_baseline_start, view.per_baseline_end, view.per_observations,
    ) == ("2026-08-24", "2026-08-25", 2)
    assert (
        view.pbr_baseline_start, view.pbr_baseline_end, view.pbr_observations,
    ) == ("2026-08-21", "2026-08-25", 3)


def test_market_valuation_views_keep_all_missing_ratio_history_numeric_free():
    frame = _market_valuation_frame()
    frame["weighted_per"] = None

    views = DashboardService.build_market_valuation_views(
        frame, as_of="2026-08-25", expected_as_of="2026-08-25",
    )

    assert all(
        not view.displays_per
        and view.weighted_per is None
        and view.per_mean is None
        and view.per_median is None
        and view.per_percentile is None
        and view.per_observations == 0
        and view.per_baseline_start is None
        and view.per_baseline_end is None
        and view.displays_pbr
        for view in views.values()
    )


def test_market_valuation_views_suppress_stale_state_before_dataset_read(tmp_path):
    state = tmp_path / "data/state/kr_index_fundamental_daily.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({
        "schema_version": 1,
        "status": "ACCEPTED_DESCRIPTIVE_NON_PREDICTIVE",
        "last_accepted_market_date": "2026-08-24",
        "rows": 6,
        "predictive_eligibility": "NON_PREDICTIVE",
    }), encoding="utf-8")

    views = DashboardService(tmp_path).market_valuation_views(
        now_utc="2026-08-26T00:30:00+00:00",
    )

    assert all(
        view.display_state is DashboardDisplayState.REFRESH_REQUIRED
        and view.weighted_per is None and view.expected_as_of == "2026-08-25"
        for view in views.values()
    )


def _write_ls_t8412_current(
    root: Path, *, identity: dict[str, str] | None = None,
    provider_timestamp_utc: str = "2026-08-21T05:45:00+00:00",
) -> Path:
    path = root / LS_T8412_CURRENT_OBSERVATION_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": 1,
        "observations": [{
            "route_id": LS_T8412_CURRENT_ROUTE.route_id,
            "identity": identity or {"dataset_id": "KR_EQUITY_CURRENT", "market": "XKRX", "symbol": "005930"},
            "interval": "15m", "value": 71_500.0, "unit": "provider_native_price",
            "provider": "LS_OPENAPI", "upstream_provider": "LS_OPENAPI",
            "source_route": "LS_OPENAPI:/stock/chart:t8412",
            "provider_timestamp_utc": provider_timestamp_utc,
            "retrieved_at_utc": "2026-08-21T05:55:00+00:00",
            "finality": "AS_RETRIEVED", "display_only": True, "pit_safe": False,
        }],
        "circuits": {}, "decisions": {},
    }), encoding="utf-8")
    return path


def _write_toss_nxt_close_current(
    root: Path, *, symbol: str, provider_timestamp_utc: str | None = None,
    unit: str = "KRW per share",
) -> Path:
    specs = {
        "000660": (
            TOSS_000660_NXT_CLOSE_UR240_OBSERVATION_PATH,
            TOSS_000660_NXT_CLOSE_UR240_ROUTE,
            "POST_CLOSE_SNAPSHOT", "/api/v1/prices", "2026-08-21T10:59:59+00:00", 1_761_000.0,
        ),
        "005930": (
            TOSS_005930_NXT_CLOSE_UR241_OBSERVATION_PATH,
            TOSS_005930_NXT_CLOSE_UR241_ROUTE,
            "PROVISIONAL", "/api/v1/prices:TOSS_NXT_CLOSE_INFERRED_FROM_EXCLUSIVE_TIME_WINDOW",
            "2026-08-21T10:59:59+00:00", 72_000.0,
        ),
    }
    path, route, finality, source_route, timestamp, value = specs[symbol]
    path = root / path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": 1,
        "observations": [{
            "route_id": route.route_id,
            "identity": {"dataset_id": "KR_EQUITY_CURRENT", "market": "XKRX", "symbol": symbol},
            "interval": "snapshot", "value": value, "unit": unit,
            "provider": "tossinvest_open_api", "upstream_provider": "tossinvest_open_api",
            "source_route": source_route, "provider_timestamp_utc": provider_timestamp_utc or timestamp,
            "retrieved_at_utc": "2026-08-21T13:12:25+00:00", "finality": finality,
            "display_only": True, "pit_safe": False,
        }],
        "circuits": {route.route_id: {"is_open": False, "failure_kind": None, "safe_code": None, "generation": 0}},
        "decisions": {},
    }), encoding="utf-8")
    return path


def _write_toss_domestic_ur246_current(
    root: Path, *, symbol: str, provider_timestamp_utc: str,
    timestamp_basis: str = "PROVIDER_TIMESTAMP",
) -> Path:
    route = _toss_domestic_ur246_route(symbol)
    path = root / _toss_domestic_ur246_path(symbol)
    path.parent.mkdir(parents=True, exist_ok=True)
    is_index = symbol in {"KOSPI", "KOSDAQ"}
    path.write_text(json.dumps({
        "schema_version": 1,
        "observations": [{
            "route_id": route.route_id,
            "identity": {
                "dataset_id": "TOSS_MARKET_PRICE_SNAPSHOT" if is_index else "KR_EQUITY_CURRENT",
                "market": "XKRX", "symbol": symbol,
            },
            "interval": "snapshot", "value": 2810.25 if is_index else 250_000.0,
            "unit": "index points" if is_index else "KRW per share",
            "provider": "tossinvest_open_api", "upstream_provider": "tossinvest_open_api",
            "source_route": route.fallback_policy.primary_route,
            "provider_timestamp_utc": provider_timestamp_utc,
            "retrieved_at_utc": provider_timestamp_utc,
            "finality": "PROVISIONAL", "display_only": True, "pit_safe": False,
            "timestamp_basis": timestamp_basis,
        }],
        "circuits": {}, "decisions": {},
    }), encoding="utf-8")
    return path


def _write_naver_web_000660_current(root: Path) -> Path:
    path = root / NAVER_WEB_000660_CURRENT_OBSERVATION_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": 1,
        "observations": [{
            "route_id": NAVER_WEB_000660_CURRENT_ROUTE.route_id,
            "identity": {"dataset_id": "KR_EQUITY_CURRENT", "market": "XKRX", "symbol": "000660"},
            "interval": "snapshot", "value": 738_000.0, "unit": "KRW per share",
            "provider": "NAVER_FINANCE_WEB", "upstream_provider": "NAVER_FINANCE_WEB",
            "source_route": "NAVER_WEB:/api/stock/000660/basic",
            "provider_timestamp_utc": "2026-08-21T04:26:15+00:00",
            "retrieved_at_utc": "2026-08-21T04:27:00+00:00",
            "finality": "PROVISIONAL", "display_only": True, "pit_safe": False,
        }],
        "circuits": {}, "decisions": {},
    }), encoding="utf-8")
    return path


def _write_nasdaq_soxx_current(
    root: Path, *, identity: dict[str, str] | None = None,
    provider_timestamp_utc: str = "2026-08-21T08:08:00+00:00",
    unit: str = "USD per share",
) -> Path:
    path = root / NASDAQ_SOXX_INFO_CURRENT_OBSERVATION_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": 1,
        "observations": [{
            "route_id": NASDAQ_SOXX_INFO_CURRENT_ROUTE.route_id,
            "identity": identity or {"dataset_id": "US_ETF_CURRENT", "market": "NASDAQ", "symbol": "SOXX"},
            "interval": "snapshot", "value": 526.6332, "unit": unit,
            "provider": "NASDAQ_OFFICIAL", "upstream_provider": "NASDAQ_OFFICIAL",
            "source_route": "NASDAQ_OFFICIAL:api.nasdaq.com/api/quote/SOXX/info?assetclass=etf",
            "provider_timestamp_utc": provider_timestamp_utc,
            "retrieved_at_utc": "2026-08-21T08:09:35.261238+00:00",
            "finality": "PROVISIONAL", "display_only": True, "pit_safe": False,
        }],
        "circuits": {}, "decisions": {},
    }), encoding="utf-8")
    return path


def _write_naver_mobile_basic_ur199_current(
    root: Path, *, symbol: str, value: float, unit: str = "KRW per share",
    provider_timestamp_utc: str = "2026-08-24T00:35:00+00:00",
) -> Path:
    path, route = {
        "000660": (NAVER_MOBILE_BASIC_000660_UR199_OBSERVATION_PATH, NAVER_MOBILE_BASIC_000660_UR199_ROUTE),
        "005930": (NAVER_MOBILE_BASIC_005930_UR199_OBSERVATION_PATH, NAVER_MOBILE_BASIC_005930_UR199_ROUTE),
    }[symbol]
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps({
        "schema_version": 1,
        "observations": [{
            "route_id": route.route_id,
            "identity": {"dataset_id": "KR_EQUITY_CURRENT", "market": "XKRX", "symbol": symbol},
            "interval": "snapshot", "value": value, "unit": unit,
            "provider": "NAVER_FINANCE_WEB", "upstream_provider": "NAVER_FINANCE_WEB",
            "source_route": route.fallback_policy.primary_route,
            "provider_timestamp_utc": provider_timestamp_utc,
            "retrieved_at_utc": "2026-08-24T00:36:00+00:00",
            "finality": "PROVISIONAL", "display_only": True, "pit_safe": False,
        }],
        "circuits": {}, "decisions": {},
    }), encoding="utf-8")
    return target


def _write_treasury_quote_15m(root: Path, *, missing_last_tnx: bool = False) -> None:
    rows = []
    starts = pd.date_range(
        "2026-08-19 08:20", "2026-08-19 13:50", freq="15min",
        tz="America/Chicago",
    )
    for series_id, base in (("^FVX", 39.0), ("^TNX", 42.0), ("^TYX", 48.0)):
        selected = starts[:-1] if missing_last_tnx and series_id == "^TNX" else starts
        for number, start in enumerate(selected):
            close = base + number / 100
            rows.append({
                "market_date": start.date(), "market": "CBOE",
                "series_id": series_id, "provider_symbol": series_id,
                "instrument_type": "TREASURY_YIELD_INDEX",
                "bar_start": start.tz_convert("UTC"),
                "bar_end": (start + timedelta(minutes=15)).tz_convert("UTC"),
                "source_timezone": "America/Chicago",
                "display_timezone": "Asia/Seoul", "session": "REGULAR",
                "interval": "15m", "open": close, "high": close + 0.02,
                "low": close - 0.02, "close": close, "volume": None,
                "provider": "yahoo_chart_api",
                "data_availability": "INDICATIVE_DELAYED_NOT_LICENSED_REALTIME",
                "retrieved_at": pd.Timestamp("2026-08-19 20:32", tz="UTC"),
            })
    frame = pd.DataFrame(rows, columns=MARKET_PRICE_15M_OBSERVATION.column_names)
    frame["volume"] = frame["volume"].astype("Int64")
    write_dataset_atomic(
        frame, root / "data/normalized/market_price_15m_observation",
        MARKET_PRICE_15M_OBSERVATION, validate_market_price_15m,
    )


def _write_vix_15m(
    root: Path, *, session_date: str = "2026-08-19", checkpoint: bool = True,
) -> None:
    day = pd.Timestamp(session_date)
    starts = pd.date_range(
        day.tz_localize("America/New_York") + timedelta(hours=9, minutes=30),
        periods=26, freq="15min",
    ).tz_convert("UTC")
    rows = []
    for number, start in enumerate(starts):
        close = 20.0 + number / 10
        rows.append({
            "market_date": start.tz_convert("America/Chicago").date(),
            "market": "CBOE", "series_id": "^VIX", "provider_symbol": "^VIX",
            "instrument_type": "VOLATILITY_INDEX", "bar_start": start,
            "bar_end": start + timedelta(minutes=15),
            "source_timezone": "America/Chicago", "display_timezone": "Asia/Seoul",
            "session": "REGULAR", "interval": "15m", "open": close,
            "high": close + 0.1, "low": close - 0.1, "close": close,
            "volume": None, "provider": "yahoo_chart_api",
            "data_availability": "INDICATIVE_DELAYED_NOT_LICENSED_REALTIME",
            "retrieved_at": starts[-1] + timedelta(minutes=46),
        })
    frame = pd.DataFrame(rows, columns=MARKET_PRICE_15M_OBSERVATION.column_names)
    frame["volume"] = frame["volume"].astype("Int64")
    write_dataset_atomic(
        frame, root / "data/normalized/market_price_15m_observation",
        MARKET_PRICE_15M_OBSERVATION, validate_market_price_15m,
    )
    if checkpoint:
        state = root / "data/state/global_market_15m/cboe_vix.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        state.write_text(json.dumps({
            "status": "PASS", "dataset_id": "market_price_15m_observation",
            "lane_id": "CBOE_VIX", "series_ids": ["^VIX"],
            "scope_utc": [starts[0].isoformat(), (starts[-1] + timedelta(minutes=15)).isoformat()],
            "expected_bars": {"^VIX": 26},
            "latest_bar_end_utc": {
                "^VIX": (starts[-1] + timedelta(minutes=15)).isoformat()
            },
        }), encoding="utf-8")


def _write_vix_current(
    root: Path, *, provider_timestamp_utc: str = "2026-08-24T18:00:00+00:00",
) -> None:
    path = root / "data/state/current_observations/yahoo_native15m_current/idxvix.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": 1,
        "observations": [{
            "route_id": "yahoo-market-current:CBOE:VIX",
            "identity": {
                "dataset_id": "MARKET_PRICE_CURRENT",
                "market": "CBOE", "symbol": "^VIX",
            },
            "interval": "15m", "value": 15.81, "unit": "index points",
            "provider": "YAHOO", "upstream_provider": "YAHOO_CHART_API",
            "source_route": "YAHOO_CHART_15M:^VIX",
            "provider_timestamp_utc": provider_timestamp_utc,
            "retrieved_at_utc": "2026-08-24T18:02:03+00:00",
            "finality": "AS_RETRIEVED", "display_only": True,
            "pit_safe": False, "timestamp_basis": "PROVIDER_TIMESTAMP",
        }],
        "circuits": {}, "decisions": {},
    }), encoding="utf-8")


def _official(date: str, *, volume: int = 100, value: int = 1_000) -> pd.DataFrame:
    return pd.DataFrame({
        "date": [pd.Timestamp(date)], "market": ["KOSPI"], "symbol": ["005930"],
        "source_name": ["KRX MDCSTAT30101"], "short_volume": [volume],
        "short_trading_value": [value], "short_volume_ratio": [1.25],
        "short_trading_value_ratio": [1.5],
    })


def _write_ls_t1716(root: Path, date: str, *, volume: int = 90, value_million: int = 0) -> None:
    path = root / "data/landing/diagnostics/ls_openapi_source_inventory/run/10_samsung_foreign_holding.response.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"t1716OutBlock": [{"date": date.replace("-", ""), "gm_volume": volume, "gm_value": value_million}]}), encoding="utf-8")


def _write_ls_t1633_normalized(root: Path, day: str = "2026-08-19") -> None:
    source = {
        "date": day.replace("-", ""),
        "tot1": "100", "tot2": "80", "tot3": "20",
        "cha1": "30", "cha2": "35", "cha3": "-5",
        "bcha1": "70", "bcha2": "45", "bcha3": "25",
    }
    frames = [normalize_ls_t1633_market_pair(
        amount_row=source, quantity_row=source, market=market,
        collected_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
        amount_landing_sha256=("a" if market == "KOSPI" else "c") * 64,
        quantity_landing_sha256=("b" if market == "KOSPI" else "d") * 64,
    ) for market in ("KOSPI", "KOSDAQ")]
    frame = pd.concat(frames, ignore_index=True).sort_values(
        list(LS_T1633_PROGRAM_TRADING_DAILY.sort_key), kind="stable"
    ).reset_index(drop=True)
    write_dataset_atomic(
        frame, root / "data/normalized/ls_t1633_program_trading_daily",
        LS_T1633_PROGRAM_TRADING_DAILY, validate_ls_t1633_program_trading,
    )


def _health(*rows):
    return SimpleNamespace(rows=tuple(SimpleNamespace(
        dataset=dataset,
        latest=latest,
        expected=expected,
        freshness=freshness,
        operational=operational,
        blocker=blocker,
        pit=pit,
        automation="AUTO_ELIGIBLE / ENABLED",
        source=source,
    ) for dataset, latest, expected, freshness, operational, blocker, pit, source in rows))


def _account_payload(**overrides):
    payload = {
        "schema_version": 2,
        "state": "LOCAL_MOCK",
        "as_of": "2026-08-19T21:00:00+09:00",
        "last_reconciled_at": "2026-08-19T21:05:00+09:00",
        "currency": "KRW",
        "total_assets": 10_000_000,
        "securities_value": None,
        "cash_balance": 2_000_000,
        "available_cash": None,
        "realized_pnl": 75_000,
        "unrealized_pnl": -125_000,
        "positions": [{
            "symbol": "TEST1", "name": "Fixture Asset", "quantity": 2,
            "market_value": 1_000_000, "realized_pnl": None,
            "unrealized_pnl": -25_000,
        }],
        "asset_history": [
            {"date": "2026-08-15", "total_assets": 9_500_000},
            {"date": "2026-08-19", "total_assets": 10_000_000},
        ],
    }
    payload.update(overrides)
    return payload


def _toss_account_payload(*, include_us: bool = False, **overrides):
    positions = [{
        "symbol": "005930", "name": "Fixture KR", "market_country": "KR",
        "currency": "KRW", "quantity": "2", "last_price": "1100",
        "average_purchase_price": "1000", "purchase_amount": "2000",
        "market_value": "2200", "market_value_after_cost": "2180",
        "profit_loss": "200", "profit_loss_after_cost": "180",
        "profit_loss_rate": "0.1", "profit_loss_rate_after_cost": "0.09",
        "daily_profit_loss": "50", "daily_profit_loss_rate": "0.02",
        "commission": "10", "tax": "10",
    }]
    summaries = [{
        "currency": "KRW", "purchase_amount": "2000", "market_value": "2200",
        "market_value_after_cost": "2180", "profit_loss": "200",
        "profit_loss_after_cost": "180", "daily_profit_loss": "50",
    }]
    if include_us:
        positions.append({
            "symbol": "AAPL", "name": "Fixture US", "market_country": "US",
            "currency": "USD", "quantity": "1", "last_price": "11",
            "average_purchase_price": "10", "purchase_amount": "10",
            "market_value": "11", "market_value_after_cost": "10.8",
            "profit_loss": "1", "profit_loss_after_cost": "0.8",
            "profit_loss_rate": "0.1", "profit_loss_rate_after_cost": "0.08",
            "daily_profit_loss": "0.2", "daily_profit_loss_rate": "0.02",
            "commission": "0.1", "tax": None,
        })
        summaries.append({
            "currency": "USD", "purchase_amount": "10", "market_value": "11",
            "market_value_after_cost": "10.8", "profit_loss": "1",
            "profit_loss_after_cost": "0.8", "daily_profit_loss": "0.2",
        })
    payload = {
        "schema_version": 1, "provider": "tossinvest_open_api",
        "source_operation": "getHoldings", "source_spec_version": "1.2.14",
        "collected_at": "2026-08-20T01:02:03+00:00",
        "registered_holder_scope": "SELF", "economic_attribution_scope": "SELF",
        "cash_balance": None, "buying_power": None,
        "unsupported_fields": ["cash_balance", "buying_power", "realized_pnl"],
        "summaries": summaries,
        "overall_rates": {
            "profit_loss_rate": "0.1", "profit_loss_rate_after_cost": "0.09",
            "daily_profit_loss_rate": "0.02",
        },
        "positions": positions,
    }
    payload.update(overrides)
    return payload


def _kb_account_payload() -> dict:
    raw = {
        "dataHeader": {
            "resultCode": "200", "processCode": "0011",
            "processTime": "20260622162350500",
        },
        "dataBody": {
            "grid_cnt1": "0001", "tl_data_cnt": "0001",
            "nt_asts_val_amt": "000000000001066450",
            "scrts_nt_val_amt": "000000000000426500",
            "byng_amt_sum": "000000000000360050",
            "val_amt_sum": "000000000000426500",
            "val_pl_sum": "000000000000066450",
            "Record1": [{
                "is_cd": "A005930", "is_nm": "Fixture Equity", "clsf": "현금",
                "ec_q_p6": "000000001.000000",
                "ordr_psbl_q_p6": "000000001.000000",
                "byng_avr_prc": "000000360050.00",
                "now_prc": "000000426500.00",
                "byng_amt": "000000000000360050",
                "val_amt": "000000000000426500",
                "val_pl": "000000000000066450",
            }],
        },
    }
    return normalize_domestic_balance_payload(
        raw, collected_at=datetime(2026, 8, 20, tzinfo=timezone.utc)
    )


def _family_account_payload(**overrides) -> dict:
    payload = {
        "schema_version": 3,
        "state": "FAMILY_LOCAL_MANUAL",
        "provider": "MIRAE_ASSET_LOCAL_MANUAL",
        "source_mode": "LOCAL_MANUAL",
        "as_of": "2026-08-19T21:00:00+09:00",
        "last_reconciled_at": "2026-08-19T21:05:00+09:00",
        "registered_holder_scope": "FAMILY_MEMBER",
        "economic_attribution_scope": "USER_DECLARED_FUNDS",
        "legal_ownership_claimed": False,
        "include_in_user_fund_total": True,
        "currency": "KRW",
        "total_assets": 500_000,
        "securities_value": 500_000,
        "cash_balance": None,
        "available_cash": None,
        "realized_pnl": None,
        "unrealized_pnl": 20_000,
        "positions": [{
            "symbol": "ETF1", "name": "Fixture ETF", "quantity": 2,
            "market_value": 500_000, "realized_pnl": None,
            "unrealized_pnl": 20_000,
        }],
        "asset_history": [],
    }
    payload.update(overrides)
    return payload


def test_local_account_snapshot_requires_an_explicit_path():
    view = LocalAccountSnapshotService().load()

    assert view.state is AccountSnapshotState.NOT_AVAILABLE
    assert not view.available
    assert view.total_assets is None
    assert view.reason == "ACCOUNT_SNAPSHOT_NOT_CONFIGURED"


def test_local_account_snapshot_preserves_explicit_null_fields(tmp_path):
    path = tmp_path / "account_snapshot.json"
    path.write_text(json.dumps(_account_payload()), encoding="utf-8")

    view = LocalAccountSnapshotService(path).load()

    assert view.state is AccountSnapshotState.LOCAL_MOCK
    assert view.available
    assert view.as_of == "2026-08-19T21:00:00+09:00"
    assert view.last_reconciled_at == "2026-08-19T21:05:00+09:00"
    assert view.currency == "KRW"
    assert view.total_assets == 10_000_000
    assert view.securities_value is None
    assert view.available_cash is None
    assert view.unrealized_pnl == -125_000
    assert view.realized_pnl == 75_000
    assert view.positions[0].symbol == "TEST1"
    assert [point.date for point in view.asset_history] == ["2026-08-15", "2026-08-19"]


@pytest.mark.parametrize("payload", [
    _account_payload(state="AVAILABLE"),
    _account_payload(total_assets="10000000"),
    _account_payload(currency="krw"),
    {**_account_payload(), "unexpected": 1},
    _account_payload(asset_history=[
        {"date": "2026-08-19", "total_assets": 10_000_000},
        {"date": "2026-08-18", "total_assets": 9_900_000},
    ]),
    _account_payload(positions=[
        {"symbol": "TEST1", "name": "One", "quantity": 1, "market_value": 1,
         "realized_pnl": None, "unrealized_pnl": None},
        {"symbol": "TEST1", "name": "Two", "quantity": 1, "market_value": 1,
         "realized_pnl": None, "unrealized_pnl": None},
    ]),
])
def test_local_account_snapshot_fails_closed_on_schema_or_value_errors(tmp_path, payload):
    path = tmp_path / "account_snapshot.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    view = LocalAccountSnapshotService(path).load()

    assert view.state is AccountSnapshotState.NOT_AVAILABLE
    assert not view.available
    assert view.total_assets is None
    assert view.cash_balance is None
    assert view.reason == "ACCOUNT_SNAPSHOT_INVALID"


def test_local_account_snapshot_missing_locked_and_corrupt_fail_closed(tmp_path, monkeypatch):
    missing = LocalAccountSnapshotService(tmp_path / "missing.json").load()
    assert missing.reason == "ACCOUNT_SNAPSHOT_MISSING"
    assert not missing.available and missing.positions == ()

    locked_path = tmp_path / "locked.json"
    original_read_text = Path.read_text

    def locked_read_text(path, *args, **kwargs):
        if path == locked_path:
            raise PermissionError("private path and value must not escape")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", locked_read_text)
    locked = LocalAccountSnapshotService(locked_path).load()
    assert locked.reason == "ACCOUNT_SNAPSHOT_LOCKED"
    assert "private" not in repr(locked)

    corrupt_path = tmp_path / "corrupt.json"
    corrupt_path.write_text("{not-json", encoding="utf-8")
    corrupt = LocalAccountSnapshotService(corrupt_path).load()
    assert corrupt.reason == "ACCOUNT_SNAPSHOT_INVALID"
    assert corrupt.total_assets is None and corrupt.positions == ()


def test_local_account_snapshot_maps_sanitized_toss_holdings_without_fx_merge(tmp_path):
    path = tmp_path / "latest.json"
    path.write_text(json.dumps(_toss_account_payload(include_us=True)), encoding="utf-8")

    view = LocalAccountSnapshotService(path).load()

    assert view.state is AccountSnapshotState.TOSS_READ_ONLY
    assert view.available and view.provider == "TOSS_SECURITIES"
    assert view.currency is None and view.total_assets is None
    assert view.cash_balance is None and view.available_cash is None
    assert [(row.currency, row.securities_value) for row in view.currency_summaries] == [
        ("KRW", 2200.0), ("USD", 11.0),
    ]
    assert [position.symbol for position in view.positions] == ["005930", "AAPL"]
    kr = view.positions[0]
    assert (
        kr.average_purchase_price,
        kr.current_price,
        kr.return_pct,
        kr.unrealized_pnl_after_cost,
        kr.daily_pnl,
        kr.return_pct_after_cost,
        kr.daily_return_pct,
        kr.commission,
        kr.tax,
    ) == (1000.0, 1100.0, 10.0, 180.0, 50.0, 9.0, 2.0, 10.0, 10.0)
    assert view.positions[1].tax is None


def test_account_source_actions_are_identifier_free_typed_and_schedule_aware(
    tmp_path: Path,
) -> None:
    toss_state = tmp_path / "data/state/toss_account_snapshot.json"
    toss_log = tmp_path / "artifacts/scheduler_logs/STOCK_DATA_TOSS_ACCOUNT_DAILY_last.json"
    kb_state = tmp_path / "data/state/kbsec_account_snapshot.json"
    for path in (toss_state, toss_log, kb_state):
        path.parent.mkdir(parents=True, exist_ok=True)
    toss_state.write_text(json.dumps({
        "status": "SUCCEEDED",
        "collected_at": "2026-08-26T13:16:55+00:00",
        "ignored_private_payload": "ACCOUNT-12345678",
    }), encoding="utf-8")
    toss_log.write_text(json.dumps({
        "outcome": "FAILED_PRESERVED_PRIOR",
        "finished_at_utc": "2026-08-26T18:00:00+00:00",
    }), encoding="utf-8")
    kb_state.write_text(json.dumps({
        "status": "SUCCEEDED",
        "collected_at": "2026-08-26T13:16:57+00:00",
    }), encoding="utf-8")
    portfolio = AccountPortfolioView(entries=(
        AccountPortfolioEntryView(
            "toss_self", "Toss", AccountSnapshotView(
                state=AccountSnapshotState.TOSS_READ_ONLY,
                as_of="2026-08-26T13:16:55+00:00",
                freshness="AS_RETRIEVED",
            ),
        ),
        AccountPortfolioEntryView(
            "kb_self", "KB", AccountSnapshotView(
                state=AccountSnapshotState.KB_READ_ONLY,
                as_of="2026-08-26T13:16:57+00:00",
                freshness="AS_RETRIEVED",
            ),
        ),
        AccountPortfolioEntryView(
            "manual:local", "Manual", AccountSnapshotView(
                state=AccountSnapshotState.MANUAL_HOLDINGS_BASIS,
                as_of="2026-08-20",
                freshness="DATED_MANUAL_BASIS",
            ),
        ),
    ), user_fund_totals=())

    rows = build_account_source_action_views(
        portfolio, tmp_path, toss_runtime_enabled=True, kb_runtime_enabled=True,
        now=datetime(2026, 8, 27, 3, 0, tzinfo=timezone(timedelta(hours=9))),
    )

    assert [row.source_id for row in rows] == ["toss_self", "kb_self", "manual:local"]
    assert rows[0].last_outcome == "FAILED_PRESERVED_PRIOR"
    assert rows[0].next_eligibility == "다음 예약 자격 08-27 07:00 KST"
    assert rows[1].last_outcome == "SUCCEEDED"
    assert rows[1].next_eligibility == "예약 미등록 · 지금 수동 갱신 가능"
    assert rows[2].last_outcome == "기록 없음"
    assert "ACCOUNT-12345678" not in repr(rows)


def test_toss_v2_buying_power_currency_without_holdings_remains_a_missing_valuation_bucket(
    tmp_path,
):
    payload = _toss_account_payload(
        schema_version=2,
        buying_power=[
            {
                "currency": "KRW", "cash_buying_power": "345000",
                "source_operation": "getBuyingPower",
            },
            {
                "currency": "USD", "cash_buying_power": "12.34",
                "source_operation": "getBuyingPower",
            },
        ],
        unsupported_fields=["cash_balance", "realized_pnl"],
    )
    path = tmp_path / "latest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    view = LocalAccountSnapshotService(path).load()
    presentation = build_account_portfolio_presentation(AccountPortfolioView(
        entries=(AccountPortfolioEntryView("toss", "Synthetic Toss", view),),
        user_fund_totals=(),
    ))

    assert view.state is AccountSnapshotState.TOSS_READ_ONLY
    assert [summary.currency for summary in view.currency_summaries] == ["KRW", "USD"]
    usd_summary = view.currency_summaries[1]
    assert usd_summary.cash_buying_power == 12.34
    assert (
        usd_summary.purchase_amount,
        usd_summary.securities_value,
        usd_summary.securities_value_after_cost,
        usd_summary.unrealized_pnl,
        usd_summary.unrealized_pnl_after_cost,
        usd_summary.daily_pnl,
    ) == (None, None, None, None, None, None)
    assert [row.currency for row in presentation.currencies] == ["KRW", "USD"]
    usd_bucket = presentation.currencies[1]
    assert usd_bucket.available_cash == 12.34
    assert (
        usd_bucket.total_assets,
        usd_bucket.securities_value,
        usd_bucket.cash_balance,
        usd_bucket.unrealized_pnl,
    ) == (None, None, None, None)
    assert {holding.currency for holding in presentation.holdings} == {"KRW"}
    assert all(row.currency != "USD" for row in presentation.allocations)
    assert all(row.currency != "USD" for row in presentation.histories)


@pytest.mark.parametrize("mutation", ["summary", "identity"])
def test_local_toss_account_snapshot_fails_closed_on_invalid_artifact(tmp_path, mutation):
    payload = _toss_account_payload()
    if mutation == "summary":
        payload["summaries"][0]["market_value"] = "9999"
    else:
        payload["accountSeq"] = 7
    path = tmp_path / "latest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    view = LocalAccountSnapshotService(path).load()

    assert view.state is AccountSnapshotState.NOT_AVAILABLE
    assert not view.available and view.positions == ()


def test_local_kb_account_snapshot_maps_verified_fields_without_invented_cash(tmp_path):
    path = tmp_path / "kb.json"
    path.write_text(json.dumps(_kb_account_payload()), encoding="utf-8")

    view = LocalAccountSnapshotService(path).load()

    assert view.state is AccountSnapshotState.KB_READ_ONLY
    assert view.provider == "KB_SECURITIES"
    assert view.registered_holder_scope == "SELF"
    assert view.economic_attribution_scope == "SELF"
    assert view.total_assets == 1_066_450
    assert view.positions[0].return_pct == pytest.approx(
        66_450 / 360_050 * 100.0
    )
    assert view.securities_value == 426_500
    assert view.cash_balance is None and view.available_cash is None
    assert view.realized_pnl is None and view.unrealized_pnl == 66_450
    assert view.positions[0].purchase_amount == 360_050
    assert view.positions[0].orderable_quantity == 1


@pytest.mark.parametrize("mutation", ["aggregate", "cash", "identity"])
def test_local_kb_account_snapshot_fails_closed_on_invalid_projection(tmp_path, mutation):
    payload = _kb_account_payload()
    if mutation == "aggregate":
        payload["securities_value"] = "426501"
    elif mutation == "cash":
        payload["cash_balance"] = "639950"
    else:
        payload["registered_holder_scope"] = "FAMILY_MEMBER"
    path = tmp_path / "kb.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    view = LocalAccountSnapshotService(path).load()

    assert view.state is AccountSnapshotState.NOT_AVAILABLE
    assert not view.available and view.total_assets is None


def test_family_local_snapshot_keeps_holder_and_economic_attribution_separate(tmp_path):
    path = tmp_path / "family.json"
    path.write_text(json.dumps(_family_account_payload()), encoding="utf-8")

    view = LocalAccountSnapshotService(path).load()

    assert view.state is AccountSnapshotState.FAMILY_LOCAL_MANUAL
    assert view.provider == "MIRAE_ASSET"
    assert view.source_mode == "LOCAL_MANUAL"
    assert view.registered_holder_scope == "FAMILY_MEMBER"
    assert view.economic_attribution_scope == "USER_DECLARED_FUNDS"
    assert view.include_in_user_fund_total
    assert not view.legal_ownership_claimed


@pytest.mark.parametrize("mutation", [
    {"legal_ownership_claimed": True},
    {"registered_holder_scope": "SELF"},
    {"provider": "MIRAE_ASSET_API"},
])
def test_family_local_snapshot_rejects_ownership_or_connection_relabelling(tmp_path, mutation):
    path = tmp_path / "family.json"
    path.write_text(json.dumps(_family_account_payload(**mutation)), encoding="utf-8")

    view = LocalAccountSnapshotService(path).load()

    assert view.state is AccountSnapshotState.NOT_AVAILABLE
    assert not view.available


def test_local_portfolio_totals_only_complete_selected_contract_totals(tmp_path):
    kb_path = tmp_path / "kb.json"
    family_path = tmp_path / "family.json"
    toss_path = tmp_path / "toss.json"
    kb_path.write_text(json.dumps(_kb_account_payload()), encoding="utf-8")
    family_path.write_text(json.dumps(_family_account_payload()), encoding="utf-8")
    toss_path.write_text(json.dumps(_toss_account_payload()), encoding="utf-8")
    specs = (
        LocalAccountSourceSpec("kb", "KB Securities", kb_path),
        LocalAccountSourceSpec("family", "가족 명의 · 사용자 신고 자금", family_path),
    )

    complete = LocalAccountPortfolioService(specs).load()
    total = complete.user_fund_totals[0]
    assert (total.currency, total.total_assets, total.included_accounts, total.complete) == (
        "KRW", 1_566_450, 2, True,
    )

    incomplete = LocalAccountPortfolioService(specs + (
        LocalAccountSourceSpec("toss", "Toss Securities", toss_path),
    )).load()
    total = incomplete.user_fund_totals[0]
    assert total.currency == "KRW" and total.total_assets is None
    assert total.included_accounts == 3 and not total.complete


def test_manual_holdings_basis_maps_to_source_neutral_account_without_live_values():
    manual = parse_manual_account_snapshot({
        "schema_version": 1,
        "source_sheet": "아빠",
        "snapshot_date": "2026-02-03",
        "currency": "KRW",
        "holdings": [
            {
                "section": "ISA", "name": "Fixture Alpha", "ticker": "111111",
                "quantity": 2, "average_cost": 100, "purchase_total": 200,
            },
            {
                "section": "종합", "name": "Fixture Beta", "ticker": "222222",
                "quantity": 1, "average_cost": None, "purchase_total": None,
            },
        ],
    })

    portfolio = manual_account_snapshot_to_portfolio(manual)
    presentation = build_account_portfolio_presentation(portfolio)

    assert [entry.source_id for entry in portfolio.entries] == [
        "manual:ISA", "manual:종합",
    ]
    assert portfolio.user_fund_totals == ()
    assert all(entry.snapshot.available for entry in portfolio.entries)
    assert all(entry.snapshot.as_of == "2026-02-03" for entry in portfolio.entries)
    assert all(entry.snapshot.provider == "LOCAL_MANUAL" for entry in portfolio.entries)
    assert all(entry.snapshot.source_mode == "DATED_HOLDINGS_BASIS" for entry in portfolio.entries)
    assert all(entry.snapshot.freshness == "DATED_MANUAL_BASIS" for entry in portfolio.entries)
    assert all(entry.snapshot.total_assets is None for entry in portfolio.entries)
    assert all(entry.snapshot.securities_value is None for entry in portfolio.entries)
    assert all(position.current_price is None and position.market_value is None
               for entry in portfolio.entries for position in entry.snapshot.positions)
    assert [holding.purchase_amount for holding in presentation.holdings] == [200.0, None]
    assert all(holding.market_value is None for holding in presentation.holdings)
    assert all("명의 주장 없음" in holding.ownership_scope
               for holding in presentation.holdings)


@pytest.mark.parametrize(
    ("level", "field", "value"),
    [
        ("snapshot", "source_sheet", "unauthorized"),
        ("snapshot", "snapshot_date", "not-a-date"),
        ("snapshot", "snapshot_date", "2099-01-01"),
        ("snapshot", "currency", "USD"),
        ("holding", "quantity", -1.0),
        ("holding", "quantity", float("nan")),
        ("holding", "average_cost", -1.0),
        ("holding", "average_cost", float("inf")),
        ("holding", "purchase_total", -1.0),
        ("holding", "purchase_total", float("nan")),
        ("holding", "purchase_total", 999.0),
    ],
)
def test_manual_holdings_mapper_revalidates_directly_constructed_boundary(
    level, field, value,
):
    valid = parse_manual_account_snapshot({
        "schema_version": 1,
        "source_sheet": "아빠",
        "snapshot_date": "2026-02-03",
        "currency": "KRW",
        "holdings": [{
            "section": "ISA",
            "name": "Fixture Alpha",
            "ticker": "111111",
            "quantity": 2,
            "average_cost": 100,
            "purchase_total": 200,
        }],
    })
    invalid = (
        replace(valid, **{field: value})
        if level == "snapshot"
        else replace(
            valid,
            holdings=(replace(valid.holdings[0], **{field: value}),),
        )
    )

    with pytest.raises((TypeError, ValueError)):
        manual_account_snapshot_to_portfolio(invalid)


def test_index_query_period_indicators_and_lazy_year_read(tmp_path):
    data = tmp_path / "data"
    old = pd.DataFrame({"date": pd.date_range("2025-01-01", periods=3), "symbol": "KOSPI", "open": [1, 2, 3], "high": [2, 3, 4], "low": [0, 1, 2], "close": [1, 2, 3], "volume": [1, 1, 1]})
    new = pd.DataFrame({"date": pd.date_range("2026-01-01", periods=150), "symbol": "KOSPI", "open": range(150), "high": range(1, 151), "low": range(-1, 149), "close": range(150), "volume": range(150)})
    _write(data, "normalized/kr_index_daily", 2025, old, "KOSPI")
    _write(data, "normalized/kr_index_daily", 2026, new, "KOSPI")
    query = LocalParquetQuery(data)
    result = IndexQueryService(query).series("KOSPI", "20D")
    assert len(result) == 20
    assert {"open", "high", "low", "close", "ma5", "ma20", "ma60", "ma120", "rsi14", "disparity60"}.issubset(result.columns)
    assert all("year=2026" in str(path) for path in query.files_read)


@pytest.mark.parametrize("method", ["read", "tail"])
def test_local_parquet_query_enumerates_exact_direct_partition_before_recursion(
    tmp_path, monkeypatch, method,
):
    data = tmp_path / "data"
    dataset = "normalized/kr_index_daily"
    kospi = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=3),
        "symbol": "KOSPI", "close": [1.0, 2.0, 3.0],
    })
    kosdaq = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=3),
        "symbol": "KOSDAQ", "close": [4.0, 5.0, 6.0],
    })
    _write(data, dataset, 2026, kospi, "KOSPI")
    _write(data, dataset, 2026, kosdaq, "KOSDAQ")
    base = data / dataset
    selected = base / "market=KOSPI"
    recursive_roots = []
    original_rglob = Path.rglob

    def tracked_rglob(path, pattern):
        recursive_roots.append(path)
        if path == base:
            raise AssertionError("dataset root must not be recursively enumerated")
        return original_rglob(path, pattern)

    monkeypatch.setattr(Path, "rglob", tracked_rglob)
    query = LocalParquetQuery(data)
    common = {
        "dataset": dataset,
        "columns": ["date", "symbol", "close"],
        "partitions": {"market": "KOSPI"},
    }
    result = (
        query.tail(rows=2, **common)
        if method == "tail"
        else query.read(**common)
    )

    assert recursive_roots == [
        selected / "year=2026" if method == "tail" else selected
    ]
    assert set(result["symbol"]) == {"KOSPI"}
    assert len(result) == (2 if method == "tail" else 3)
    assert query.files_read == [selected / "year=2026" / "data.parquet"]


@pytest.mark.parametrize("method", ["read", "tail"])
def test_local_parquet_query_missing_direct_partition_preserves_fallback(
    tmp_path, monkeypatch, method,
):
    data = tmp_path / "data"
    dataset = "normalized/unpartitioned_fixture"
    frame = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=2),
        "symbol": "KOSPI", "close": [1.0, 2.0],
    })
    _write(data, dataset, 2026, frame)
    base = data / dataset
    recursive_roots = []
    original_rglob = Path.rglob

    def tracked_rglob(path, pattern):
        recursive_roots.append(path)
        return original_rglob(path, pattern)

    monkeypatch.setattr(Path, "rglob", tracked_rglob)
    query = LocalParquetQuery(data)
    common = {
        "dataset": dataset,
        "columns": ["date", "symbol", "close"],
        "partitions": {"market": "KOSPI"},
    }
    result = (
        query.tail(rows=1, **common)
        if method == "tail"
        else query.read(**common)
    )

    assert recursive_roots == [base]
    assert result.empty
    assert query.files_read == []


def test_local_parquet_tail_discovers_one_hive_level_without_root_recursion(
    tmp_path, monkeypatch,
):
    data = tmp_path / "data"
    dataset = "normalized/global_index_price_daily"
    base = data / dataset
    for symbol, close in (("NASDAQ", 2.0), ("SP500", 1.0)):
        path = base / f"symbol={symbol}" / "year=2026"
        path.mkdir(parents=True)
        pd.DataFrame({
            "date": pd.date_range("2026-01-01", periods=3),
            "symbol": symbol, "close": [close] * 3,
        }).to_parquet(path / "data.parquet", index=False)
    recursive_roots = []
    original_rglob = Path.rglob

    def tracked_rglob(path, pattern):
        recursive_roots.append(path)
        if path == base:
            raise AssertionError("dataset root must not be recursively enumerated")
        return original_rglob(path, pattern)

    monkeypatch.setattr(Path, "rglob", tracked_rglob)
    query = LocalParquetQuery(data)
    result = query.tail(
        dataset, rows=2, columns=["date", "symbol", "close"],
    )

    assert recursive_roots == [
        base / "symbol=NASDAQ" / "year=2026",
        base / "symbol=SP500" / "year=2026",
    ]
    assert len(result) == 2
    assert set(result["symbol"]).issubset({"NASDAQ", "SP500"})
    assert len(query.files_read) == 2


def test_local_parquet_tail_probes_recent_year_without_listing_history(
    tmp_path, monkeypatch,
):
    data = tmp_path / "data"
    dataset = "normalized/long_history_fixture"
    base = data / dataset
    for year in range(1980, 2027):
        _write(data, dataset, year, pd.DataFrame({
            "date": [pd.Timestamp(year, 1, 2)], "close": [float(year)],
        }))
    original_iterdir = Path.iterdir

    def guarded_iterdir(path):
        if path == base:
            raise AssertionError("recent tail must not list all retained years")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", guarded_iterdir)
    query = LocalParquetQuery(data)
    result = query.tail(
        dataset, rows=1, columns=["date", "close"], end="2026-12-31",
    )

    assert list(result["close"]) == [2026.0]
    assert query.files_read == [base / "year=2026" / "data.parquet"]


def test_local_parquet_tail_falls_back_beyond_recent_year_probe(tmp_path):
    data = tmp_path / "data"
    dataset = "normalized/sparse_history_fixture"
    _write(data, dataset, 2026, pd.DataFrame({
        "date": [pd.Timestamp(2026, 1, 2)], "close": [3.0],
    }))
    _write(data, dataset, 2020, pd.DataFrame({
        "date": pd.date_range("2020-01-02", periods=2), "close": [1.0, 2.0],
    }))

    result = LocalParquetQuery(data).tail(
        dataset, rows=3, columns=["date", "close"], end="2026-12-31",
    )

    assert list(result["close"]) == [1.0, 2.0, 3.0]


def test_local_parquet_query_parses_retained_kst_wall_clock_without_warning():
    with warnings.catch_warnings():
        warnings.simplefilter("error", FutureWarning)
        result = LocalParquetQuery._timestamp("08-25 09:00 KST")

    assert (result.month, result.day, result.hour, result.minute) == (8, 25, 9, 0)


def test_local_parquet_query_reuses_unchanged_frame_and_invalidates_on_replace(
    tmp_path, monkeypatch,
):
    data = tmp_path / "data"
    dataset = "normalized/cache_fixture"
    frame = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=2),
        "symbol": "KOSPI", "close": [1.0, 2.0],
    })
    _write(data, dataset, 2026, frame)
    path = data / dataset / "year=2026" / "data.parquet"
    calls = []
    original_read_parquet = pd.read_parquet

    def counting_read_parquet(*args, **kwargs):
        calls.append(Path(args[0]))
        return original_read_parquet(*args, **kwargs)

    monkeypatch.setattr(pd, "read_parquet", counting_read_parquet)
    query = LocalParquetQuery(data)
    kwargs = {
        "rows": 2,
        "columns": ["date", "symbol", "close"],
    }
    first = query.tail(dataset, **kwargs)
    second = query.tail(dataset, **kwargs)

    assert list(first["close"]) == [1.0, 2.0]
    pd.testing.assert_frame_equal(second, first)
    assert calls == [path]
    assert query.files_read == [path, path]

    replacement = frame.assign(close=[8.0, 9.0])
    replacement.to_parquet(path, index=False)
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    third = query.tail(dataset, **kwargs)

    assert list(third["close"]) == [8.0, 9.0]
    assert calls == [path, path]
    assert query.files_read == [path, path, path]


def test_local_parquet_tail_reuses_larger_same_boundary_result(tmp_path, monkeypatch):
    data = tmp_path / "data"
    dataset = "normalized/superset_fixture"
    frame = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=5),
        "symbol": "KOSPI", "close": range(5),
    })
    _write(data, dataset, 2026, frame)
    calls = []
    original_read_parquet = pd.read_parquet

    def counting_read_parquet(*args, **kwargs):
        calls.append(Path(args[0]))
        return original_read_parquet(*args, **kwargs)

    monkeypatch.setattr(pd, "read_parquet", counting_read_parquet)
    query = LocalParquetQuery(data)
    common = {"columns": ["date", "symbol", "close"], "end": "2026-12-31"}
    larger = query.tail(dataset, rows=5, **common)
    smaller = query.tail(dataset, rows=2, **common)

    assert list(larger["close"]) == [0, 1, 2, 3, 4]
    assert list(smaller["close"]) == [3, 4]
    assert len(calls) == 1


def test_local_parquet_small_file_cache_serves_distinct_projections_and_filters(
    tmp_path, monkeypatch,
):
    data = tmp_path / "data"
    dataset = "normalized/small_projection_fixture"
    frame = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=4),
        "kind": ["A", "B", "A", "B"],
        "left": [1, 2, 3, 4], "right": [5, 6, 7, 8],
    })
    _write(data, dataset, 2026, frame)
    calls = []
    original_read_parquet = pd.read_parquet

    def counting_read_parquet(*args, **kwargs):
        calls.append(Path(args[0]))
        return original_read_parquet(*args, **kwargs)

    monkeypatch.setattr(pd, "read_parquet", counting_read_parquet)
    query = LocalParquetQuery(data)
    left = query.tail(
        dataset, rows=4, columns=["date", "kind", "left"],
        filters={"kind": ("A",)},
    )
    right = query.tail(
        dataset, rows=4, columns=["date", "kind", "right"],
        filters={"kind": ("B",)},
    )

    assert list(left["left"]) == [1, 3]
    assert list(right["right"]) == [6, 8]
    assert len(calls) == 1


def test_index_query_uses_wilder_rsi_not_simple_rolling_average(tmp_path):
    data = tmp_path / "data"
    closes = [
        54.8, 56.8, 57.85, 59.85, 60.57, 61.1, 62.17, 60.6, 62.35, 62.15,
        62.35, 61.45, 62.8, 61.37, 62.5, 62.57, 60.8, 59.37, 60.35, 62.35,
        62.17, 62.55, 64.55, 64.37, 65.3, 64.42, 62.9, 61.6, 62.05, 60.05,
    ]
    frame = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(closes)),
        "symbol": "KOSPI", "open": closes, "high": [value + 1 for value in closes],
        "low": [value - 1 for value in closes], "close": closes, "volume": 1,
    })
    _write(data, "normalized/kr_index_daily", 2026, frame, "KOSPI")

    result = IndexQueryService(LocalParquetQuery(data)).series("KOSPI", "20D")

    assert result["rsi14"].iloc[-1] == pytest.approx(49.8157930444)
    assert result["rsi14"].iloc[-1] != pytest.approx(42.125)


def test_technical_indicators_match_independent_warmup_fixture_without_input_mutation():
    close = pd.Series(range(100, 131), dtype=float)
    source = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(close)),
        "open": close - .5, "high": close + 1, "low": close - 1,
        "close": close, "volume": range(1_000, 1_000 + len(close)),
    })
    before = source.copy(deep=True)
    result = technical_indicators(source)

    expected_ema = float(close.iloc[0])
    for value in close.iloc[1:]:
        expected_ema = (2 / 21) * float(value) + (19 / 21) * expected_ema
    assert result["ema20"].iloc[18] != result["ema20"].iloc[18]
    assert result["ema20"].iloc[-1] == pytest.approx(expected_ema)
    assert result["bollinger_mid"].iloc[-1] == pytest.approx(close.iloc[-20:].mean())
    assert result["bollinger_upper"].iloc[-1] == pytest.approx(
        close.iloc[-20:].mean() + 2 * close.iloc[-20:].std(ddof=0)
    )
    assert result["atr14"].iloc[12] != result["atr14"].iloc[12]
    assert result["atr14"].iloc[-1] == pytest.approx(2.0)
    assert result["adx14"].iloc[-1] == pytest.approx(100.0)
    assert result["obv"].iloc[-1] == pytest.approx(sum(range(1_001, 1_000 + len(close))))
    pd.testing.assert_frame_equal(source, before)


@pytest.mark.parametrize("column, value", [
    ("high", float("inf")), ("volume", -1.0),
])
def test_technical_indicators_fail_closed_for_invalid_required_inputs(column, value):
    source = pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=30), "open": range(30),
        "high": range(2, 32), "low": range(30), "close": range(1, 31),
        "volume": [1_000] * 30,
    })
    source.loc[15, column] = value
    result = technical_indicators(source)
    if column == "high":
        assert result.loc[15:, ["atr14", "adx14"]].isna().all().all()
    else:
        assert result.loc[15:, "obv"].isna().all()


def test_technical_indicators_reject_duplicate_dates_and_insufficient_history():
    source = pd.DataFrame({
        "date": ["2026-01-01", "2026-01-01"], "high": [2, 3], "low": [0, 1],
        "close": [1, 2], "volume": [1, 1],
    })
    result = technical_indicators(source)
    assert result[["ema20", "atr14", "adx14", "obv", "bollinger_upper"]].isna().all().all()


def test_technical_indicators_non_monotonic_wilder_obv_and_bollinger_conventions():
    close = [10, 12, 11, 11, 13, 12, 14, 13, 15, 14, 16, 15, 17, 16, 18,
             17, 19, 18, 20, 19, 21, 20, 22, 21, 23, 22, 24, 23, 25, 24]
    high = [value + (2 if index == 1 else 1) for index, value in enumerate(close)]
    low = [value - (2 if index == 1 else 1) for index, value in enumerate(close)]
    high[3], low[3] = high[2] + 1, low[2] - 1  # equal +DM/-DM: both are zero.
    source = pd.DataFrame({"date": pd.date_range("2026-02-01", periods=30), "high": high, "low": low, "close": close, "volume": [10 + index for index in range(30)]})
    result = technical_indicators(source)
    true_ranges = [high[0] - low[0]] + [max(high[i]-low[i], abs(high[i]-close[i-1]), abs(low[i]-close[i-1])) for i in range(1, 30)]
    expected_atr = sum(true_ranges[:14]) / 14
    for value in true_ranges[14:]:
        expected_atr = (expected_atr * 13 + value) / 14
    plus_dm, minus_dm = [0.0], [0.0]
    for index in range(1, 30):
        up, down = high[index] - high[index - 1], low[index - 1] - low[index]
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
    assert plus_dm[3] == minus_dm[3] == 0.0
    def wilder(values, start=0):
        output = [float("nan")] * len(values)
        output[start + 13] = sum(values[start:start + 14]) / 14
        for index in range(start + 14, len(values)):
            output[index] = (output[index - 1] * 13 + values[index]) / 14
        return output
    atr = wilder(true_ranges)
    plus, minus = wilder(plus_dm), wilder(minus_dm)
    dx = [float("nan")] * 30
    for index in range(13, 30):
        positive, negative = 100 * plus[index] / atr[index], 100 * minus[index] / atr[index]
        dx[index] = 100 * abs(positive - negative) / (positive + negative)
    expected_adx = wilder(dx, 13)
    expected_obv = 0
    for index in range(1, 30):
        expected_obv += (1 if close[index] > close[index-1] else -1 if close[index] < close[index-1] else 0) * (10 + index)
    assert result["atr14"].iloc[12] != result["atr14"].iloc[12]
    assert result["atr14"].iloc[-1] == pytest.approx(expected_atr)
    assert result["adx14"].first_valid_index() == 26
    assert result["adx14"].iloc[-1] == pytest.approx(expected_adx[-1])
    assert result["obv"].iloc[-1] == expected_obv
    tail = pd.Series(close[-20:], dtype=float)
    assert result["bollinger_lower"].iloc[-1] == pytest.approx(tail.mean() - 2 * tail.std(ddof=0))
    assert result["bollinger_bandwidth"].iloc[-1] == pytest.approx(4 * tail.std(ddof=0) / tail.mean() * 100)


def test_technical_indicators_fail_closed_for_missing_close_and_invalid_date():
    source = pd.DataFrame({"date": list(pd.date_range("2026-01-01", periods=20)), "high": range(2, 22), "low": range(20), "close": range(1, 21), "volume": [1] * 20})
    source.loc[10, "close"] = None
    assert technical_indicators(source).loc[10:, ["atr14", "obv"]].isna().all().all()
    source.loc[0, "date"] = "not-a-date"
    assert technical_indicators(source)[["ema20", "atr14", "obv"]].isna().all().all()


def test_index_chart_view_maps_exact_identity_metadata_and_fails_closed_before_read(tmp_path):
    data = tmp_path / "data"
    dates = pd.bdate_range(end="2026-08-19", periods=30)
    close = pd.Series(range(3000, 3030), dtype=float)
    frame = pd.DataFrame({
        "date": dates, "symbol": "KOSPI", "open": close - 2,
        "high": close + 5, "low": close - 5, "close": close,
        "volume": range(1_000_000, 1_000_030),
    })
    _write(data, "normalized/kr_index_daily", 2026, frame, "KOSPI")
    query = LocalParquetQuery(data)
    service = IndexQueryService(query)
    healthy = SimpleNamespace(
        artifact_state="READY",
        rows=(SimpleNamespace(
            dataset="kr_index_daily", latest="2026-08-19", expected="2026-08-19",
            freshness="CURRENT", operational="READY_WITH_FINALITY_GATE",
            blocker="N/A", source="pykrx", runtime_coverage="VALIDATED",
        ),),
    )

    view = service.chart_view("KOSPI", "20D", health=healthy)
    assert view.displays_values
    assert (view.name, view.exact_identity, view.dataset_id) == (
        "코스피 종합지수", "KRX:KOSPI", "kr_index_daily",
    )
    assert view.reference_kst == "2026-08-19 KST 일봉 · 일중 기준시각 미보존"
    assert view.change == pytest.approx(1.0)
    assert view.change_pct == pytest.approx(1 / 3028 * 100)
    assert view.period_high == pytest.approx(3034.0)
    assert view.period_low == pytest.approx(3005.0)
    reads_after_current = len(query.files_read)

    stale = SimpleNamespace(
        artifact_state="READY",
        rows=(SimpleNamespace(
            **{
                **vars(healthy.rows[0]),
                "freshness": "STALE",
                "latest": "2026-08-19",
                "expected": "2026-08-20",
            }
        ),),
    )
    retained = service.chart_view("KOSPI", "20D", health=stale)
    assert retained.displays_values and retained.freshness == "STALE"
    assert retained.as_of == "2026-08-19" and retained.expected_as_of == "2026-08-20"
    assert "current-data claims" in (retained.unavailable_reason or "")
    assert len(query.files_read) > reads_after_current


def test_domestic_index_views_stop_at_the_health_verified_date(tmp_path):
    data = tmp_path / "data"
    dates = pd.bdate_range(end="2026-08-19", periods=30)
    verified = pd.DataFrame({
        "date": dates, "symbol": "KOSPI", "open": range(3000, 3030),
        "high": range(3001, 3031), "low": range(2999, 3029),
        "close": range(3000, 3030), "volume": range(1_000_000, 1_000_030),
    })
    unverified = pd.DataFrame({
        "date": [pd.Timestamp("2026-08-20")], "symbol": ["KOSPI"],
        "open": [3030], "high": [3031], "low": [3029], "close": [3030],
        "volume": [1_000_030],
    })
    _write(
        data, "normalized/kr_index_daily", 2026,
        pd.concat([verified, unverified]), "KOSPI",
    )
    health = _health((
        "kr_index_daily", "2026-08-19", "2026-08-19", "CURRENT",
        "READY_WITH_FINALITY_GATE", "N/A", "PIT_LIMITED", "pykrx",
    ))
    health.artifact_state = "READY"
    health.rows[0].runtime_coverage = "VALIDATED"

    view = IndexQueryService(LocalParquetQuery(data)).chart_view("KOSPI", "20D", health=health)
    service = DashboardService(tmp_path)
    metrics = service.dashboard_metrics(health, now_utc="2026-08-20T05:00:00Z")
    series = service.dashboard_series(metrics)["KOSPI"]

    assert view.displays_values and view.as_of == "2026-08-19"
    assert metrics["KOSPI"].displays_value and metrics["KOSPI"].as_of == "2026-08-19"
    assert series.frame["date"].max().date().isoformat() == "2026-08-19"


def _write_equity_master(
    root: Path, market: str, rows: list[dict],
) -> None:
    columns = [
        "symbol", "name", "market", "isin", "listing_date", "delisting_date",
        "security_type_name",
    ]
    path = root / "data/normalized/kr_equity_master" / f"market={market}"
    path.mkdir(parents=True)
    pd.DataFrame(rows, columns=columns).to_parquet(path / "data.parquet", index=False)


def _write_kr_etf_master(root: Path) -> None:
    path = root / "data/normalized/kr_etf_master/market=KRX"
    path.mkdir(parents=True)
    pd.DataFrame([
        {
            "symbol": "123320", "name": "TIGER 레버리지", "market": "KRX",
            "security_type": "ETF", "listing_status": "LISTED_AT_SOURCE_DATE",
            "listing_date": None, "leverage_multiple": 2, "source": "pykrx",
            "source_operation": "get_etf_ticker_list+get_etf_ticker_name",
            "source_date": "2026-09-02",
        },
        {
            "symbol": "243880", "name": "TIGER 200 IT 레버리지", "market": "KRX",
            "security_type": "ETF", "listing_status": "LISTED_AT_SOURCE_DATE",
            "listing_date": None, "leverage_multiple": 2, "source": "pykrx",
            "source_operation": "get_etf_ticker_list+get_etf_ticker_name",
            "source_date": "2026-09-02",
        },
    ]).to_parquet(path / "data.parquet", index=False)


def _equity_health(
    *, latest: str = "2026-08-19", expected: str = "2026-08-19",
    freshness: str = "CURRENT",
) -> SimpleNamespace:
    return SimpleNamespace(
        artifact_state="READY",
        rows=(SimpleNamespace(
            dataset="kr_equity_price_daily", latest=latest, expected=expected,
            freshness=freshness, operational="READY_WITH_FINALITY_GATE",
            blocker="N/A", source="retained-provider-chain",
            runtime_coverage="PASSED",
        ),),
    )


def _equity_master_rows(market: str) -> list[dict]:
    return [{
        "symbol": "005930" if market == "KOSPI" else "035720",
        "name": "삼성전자" if market == "KOSPI" else "카카오",
        "market": market,
        "isin": "KR7005930003" if market == "KOSPI" else "KR7035720002",
        "listing_date": "1975-06-11" if market == "KOSPI" else "2017-07-10",
        "delisting_date": None, "security_type_name": "보통주",
    }]


def test_equity_search_resolves_exact_identity_and_excludes_preferred_shares(tmp_path):
    kospi = _equity_master_rows("KOSPI") + [{
        "symbol": "005935", "name": "삼성전자우", "market": "KOSPI",
        "isin": "KR7005931001", "listing_date": "1975-06-11",
        "delisting_date": None, "security_type_name": "우선주",
    }]
    _write_equity_master(tmp_path, "KOSPI", kospi)
    _write_equity_master(tmp_path, "KOSDAQ", _equity_master_rows("KOSDAQ"))
    service = EquityChartService(tmp_path)

    ticker = service.search("005930")
    name = service.search("삼성")

    assert [item.display_label for item in ticker.matches] == [
        "삼성전자 · 005930 · KOSPI · 보통주",
    ]
    assert [(item.symbol, item.name, item.market) for item in name.matches] == [
        ("005930", "삼성전자", "KOSPI"),
    ]
    assert service.search("005935").matches == ()


def test_equity_search_adds_retained_korean_etfs_without_changing_stock_filter(tmp_path):
    _write_equity_master(tmp_path, "KOSPI", _equity_master_rows("KOSPI"))
    _write_equity_master(tmp_path, "KOSDAQ", _equity_master_rows("KOSDAQ"))
    _write_kr_etf_master(tmp_path)

    service = EquityChartService(tmp_path)
    tiger = service.search("123320").matches[0]
    stock = service.search("005930").matches[0]

    assert (tiger.symbol, tiger.name, tiger.market, tiger.security_type) == (
        "123320", "TIGER 레버리지", "KRX", "ETF",
    )
    assert tiger.leverage_multiple == 2
    assert (stock.symbol, stock.market, stock.security_type) == (
        "005930", "KOSPI", "보통주",
    )


def test_equity_series_preserves_original_ohlcv_and_computes_display_indicators(tmp_path):
    _write_equity_master(tmp_path, "KOSPI", _equity_master_rows("KOSPI"))
    _write_equity_master(tmp_path, "KOSDAQ", _equity_master_rows("KOSDAQ"))
    dates = pd.bdate_range(end="2026-08-19", periods=150)
    close = pd.Series(range(70_000, 70_150), dtype="int64")
    frame = pd.DataFrame({
        "date": dates, "market": "KOSPI", "symbol": "005930",
        "open": close - 2, "high": close + 5, "low": close - 5,
        "close": close, "volume": range(1_000_000, 1_000_150),
        "source": "provider-native", "source_operation": "daily",
        "source_date": dates,
    })
    _write(tmp_path / "data", "normalized/kr_equity_price_daily", 2026, frame, "KOSPI")
    service = EquityChartService(tmp_path)
    identity = service.search("005930").matches[0]

    view = service.series(identity, "60D", health=_equity_health())

    assert view.displays_values
    assert view.price_mode == "원본(미조정) OHLCV"
    assert view.as_of == "2026-08-19"
    assert view.reference_kst == "2026-08-19 KST 일봉 · 정확한 시각 미보존"
    assert len(view.frame) == 60
    assert view.frame.iloc[-1][["open", "high", "low", "close", "volume"]].tolist() == [
        70_147, 70_154, 70_144, 70_149, 1_000_149,
    ]
    assert {"ma5", "ma20", "ma60", "ma120", "rsi14", "disparity60"}.issubset(view.frame)
    assert view.change == 1
    assert view.period_high == 70_154
    assert view.period_low == 70_085
    assert view.current_refresh_status == "CURRENT_UNAVAILABLE"
    assert "TOSS_STOCK_CURRENT_QUOTE_UR141_UNAVAILABLE" in (view.current_unavailable_reason or "")


def test_equity_series_hides_daily_current_display_without_mutating_daily_frame(tmp_path):
    _write_equity_master(tmp_path, "KOSPI", _equity_master_rows("KOSPI"))
    _write_equity_master(tmp_path, "KOSDAQ", _equity_master_rows("KOSDAQ"))
    dates = pd.bdate_range(end="2026-08-19", periods=20)
    close = pd.Series(range(70_000, 70_020), dtype="int64")
    frame = pd.DataFrame({
        "date": dates, "market": "KOSPI", "symbol": "005930",
        "open": close - 2, "high": close + 5, "low": close - 5,
        "close": close, "volume": range(1_000_000, 1_000_020),
        "source": "provider-native", "source_operation": "daily",
        "source_date": dates,
    })
    _write(tmp_path / "data", "normalized/kr_equity_price_daily", 2026, frame, "KOSPI")
    promote_current_display(tmp_path, CurrentDisplayObservation(
        symbol="005930", value=71_000.0, unit="KRW", source_date="2026-08-20",
        retrieved_at_utc="2026-08-20T16:38:18+00:00",
        provider="FinanceDataReader 0.9.202 / Naver daily", interval="1d",
        finality="POLLABLE_DAILY_AS_RETRIEVED",
    ))
    service = EquityChartService(tmp_path)
    view = service.series(
        service.search("005930").matches[0], "20D", health=_equity_health(),
        now_utc="2026-08-21T00:30:00Z",
    )

    assert view.current_refresh_status == "CURRENT_GATE_BLOCKED"
    assert view.current_value is None and view.current_source_date == "2026-08-20"
    assert "CURRENT_SOURCE_TIMESTAMP_REQUIRED" in (view.current_unavailable_reason or "")
    assert view.frame["date"].max().date().isoformat() == "2026-08-19"

    blocked_daily = service.series(
        service.search("005930").matches[0], "20D",
        health=_equity_health(latest="2026-08-18", expected="2026-08-19", freshness="STALE"),
        now_utc="2026-08-21T00:30:00Z",
    )
    assert not blocked_daily.displays_values
    assert blocked_daily.current_refresh_status == "CURRENT_GATE_BLOCKED"
    assert blocked_daily.current_value is None


def test_equity_series_displays_contract_valid_stale_history_with_warning(tmp_path):
    _write_equity_master(tmp_path, "KOSPI", _equity_master_rows("KOSPI"))
    _write_equity_master(tmp_path, "KOSDAQ", _equity_master_rows("KOSDAQ"))
    service = EquityChartService(tmp_path)
    identity = service.search("삼성전자").matches[0]

    dates = pd.bdate_range(end="2026-08-13", periods=30)
    close = pd.Series(range(70_000, 70_030), dtype="int64")
    _write(tmp_path / "data", "normalized/kr_equity_price_daily", 2026, pd.DataFrame({
        "date": dates, "market": "KOSPI", "symbol": "005930",
        "open": close - 2, "high": close + 5, "low": close - 5,
        "close": close, "volume": range(1_000_000, 1_000_030),
        "source": "provider-native", "source_operation": "daily",
        "source_date": dates,
    }), "KOSPI")

    view = service.series(
        identity, "120D",
        health=_equity_health(latest="2026-08-13", expected="2026-08-19", freshness="STALE"),
    )

    assert view.displays_values
    assert view.display_state is DashboardDisplayState.VALUE
    assert view.as_of == "2026-08-13" and view.expected_as_of == "2026-08-19"
    assert view.freshness == "STALE"
    assert "current-data claims" in (view.unavailable_reason or "")
    assert service.query.files_read


def test_normalized_benchmark_comparison_uses_only_exact_common_sessions_and_base_100():
    identity = EquityIdentity(
        "005930", "Samsung Electronics", "KOSPI", "KR7005930003",
        "1975-06-11", "COMMON", currency="KRW",
    )
    target = EquitySeriesView(
        identity=identity, period="20D", display_state=DashboardDisplayState.VALUE,
        freshness="CURRENT", as_of="2026-08-19", expected_as_of="2026-08-19",
        source="fixture", reference_kst="2026-08-19 KST", frame=pd.DataFrame({
            "date": pd.to_datetime(["2026-08-14", "2026-08-17", "2026-08-18", "2026-08-19"]),
            "close": [70_000.0, 71_000.0, 69_000.0, 72_000.0],
        }),
    )
    benchmark = IndexSeriesView(
        index="KOSPI", name="KOSPI", exact_identity="KRX:KOSPI", period="20D",
        dataset_id="kr_index_daily", display_state=DashboardDisplayState.VALUE,
        freshness="CURRENT", as_of="2026-08-19", expected_as_of="2026-08-19",
        source="fixture", reference_kst="2026-08-19 KST", frame=pd.DataFrame({
            "date": pd.to_datetime(["2026-08-14", "2026-08-17", "2026-08-19"]),
            "close": [3_000.0, 3_060.0, 3_030.0],
        }),
    )

    view = NormalizedBenchmarkComparisonView.from_exact_common_sessions(target, benchmark)

    assert view.displays_values
    assert view.common_start == "2026-08-14"
    assert view.frame["date"].dt.date.astype(str).tolist() == ["2026-08-14", "2026-08-17", "2026-08-19"]
    assert view.frame["target_position"].tolist() == [0.0, 1.0, 3.0]
    assert view.frame.loc[0, ["target_normalized", "benchmark_normalized"]].tolist() == [100.0, 100.0]
    assert view.frame.loc[2, "target_normalized"] == pytest.approx(72_000 / 70_000 * 100)
    assert view.frame.loc[2, "benchmark_normalized"] == pytest.approx(3_030 / 3_000 * 100)
    assert view.currency == "KRW"
    assert view.target_price_basis == "PROVIDER_NATIVE_ORIGINAL_PRICE"
    assert view.benchmark_price_basis == "KRX_INDEX_LEVEL"


def test_us_etf_comparison_is_numeric_free_before_any_etf_or_benchmark_file_read(tmp_path):
    service = DashboardService(tmp_path)
    spy = service.us_etf.search("SPY").matches[0]
    target = service.us_etf.series(spy, "120D", health=_us_etf_health())

    view = service.benchmark_comparison(target)

    assert not view.displays_values
    assert view.currency == "USD"
    assert view.benchmark_id == "SP500_OR_NASDAQ100"
    assert "S&P 500" in view.benchmark_label and "Nasdaq-100" in view.benchmark_label
    assert service.us_etf.query.files_read == []
    assert service.index.query.files_read == []


def test_equity_series_rejects_changed_identity_and_missing_local_ohlcv(tmp_path):
    _write_equity_master(tmp_path, "KOSPI", _equity_master_rows("KOSPI"))
    _write_equity_master(tmp_path, "KOSDAQ", _equity_master_rows("KOSDAQ"))
    service = EquityChartService(tmp_path)
    identity = service.search("005930").matches[0]

    missing = service.series(identity, "20D", health=_equity_health())
    changed = service.series(
        type(identity)(
            symbol=identity.symbol, name="다른 회사", market=identity.market,
            isin=identity.isin, listing_date=identity.listing_date,
            security_type=identity.security_type,
        ),
        "20D", health=_equity_health(),
    )

    assert not missing.displays_values and "OHLCV" in (missing.unavailable_reason or "")
    assert not changed.displays_values and "식별정보" in (changed.unavailable_reason or "")


def _us_etf_health(
    *, latest: str = "2026-08-19", expected: str = "2026-08-19",
    freshness: str = "CURRENT",
) -> SimpleNamespace:
    return SimpleNamespace(
        artifact_state="READY",
        rows=(SimpleNamespace(
            dataset="global_etf_price_daily", latest=latest, expected=expected,
            freshness=freshness, operational="READY_WITH_LIMITS", blocker=None,
            source="yahoo_chart_api", runtime_coverage="VALIDATED",
        ),),
    )


def _write_us_etf_price(root: Path, symbol: str = "SPY") -> pd.DataFrame:
    dates = pd.bdate_range(end="2026-08-19", periods=150)
    close = pd.Series([500.0 + number / 10 for number in range(150)])
    frame = pd.DataFrame({
        "date": dates, "symbol": symbol, "source_ticker": symbol,
        "open": close - 0.2, "high": close + 0.5, "low": close - 0.5,
        "close": close, "adjusted_close": close + 1.0,
        "volume": range(1_000_000, 1_000_150), "currency": "USD",
        "exchange": "PCX", "provider": "yahoo_chart_api",
        "retrieved_at": pd.Timestamp("2026-08-20T01:00:00Z"),
        "adjustment_status": "SOURCE_ADJUSTED_CLOSE_RETAINED_SEPARATELY",
    })
    path = root / "data/normalized/global_etf_price_daily" / f"symbol={symbol}" / "year=2026"
    path.mkdir(parents=True)
    frame.to_parquet(path / "data.parquet", index=False)
    return frame


def test_us_etf_catalog_has_exact_nineteen_fund_identities_and_official_references():
    by_symbol = {identity.symbol: identity for identity in US_ETF_CHART_IDENTITIES}

    assert tuple(by_symbol) == (
        "SOXL", "TQQQ", "QLD", "KORU", "EWY", "TLT", "TLTW", "QQQ", "SPY",
        "QQQI", "QDVO", "GPIQ", "JEPQ", "JEPI", "SGOV", "VGLT", "VNQ", "IEF", "SHY",
    )
    assert len({identity.key for identity in by_symbol.values()}) == 19
    assert all(identity.is_us_etf and identity.currency == "USD" for identity in by_symbol.values())
    assert all(identity.issuer and identity.exposure and identity.listing_date for identity in by_symbol.values())
    assert all((identity.identity_source or "").startswith("https://") for identity in by_symbol.values())
    assert by_symbol["KORU"].name == "Direxion Daily MSCI South Korea Bull 3X Shares"
    assert "3X" in (by_symbol["KORU"].name + (by_symbol["KORU"].leverage_style or ""))
    assert "Treasury Bond ETF" in by_symbol["TLT"].name
    assert "BuyWrite" in by_symbol["TLTW"].name
    assert all(by_symbol[symbol].distribution_style for symbol in ("TLTW", "QQQI", "QDVO", "GPIQ", "JEPQ", "JEPI"))
    assert {symbol: by_symbol[symbol].leverage_multiple for symbol in (
        "SOXL", "TQQQ", "QLD", "TLT", "QQQ", "SPY", "EWY",
    )} == {
        "SOXL": 3, "TQQQ": 3, "QLD": 2, "TLT": 1,
        "QQQ": 1, "SPY": 1, "EWY": 1,
    }


def test_us_etf_production_scope_is_searchable_but_numeric_free_before_any_price_read(tmp_path):
    service = USEtfChartService(tmp_path)
    spy = service.search("SPY").matches[0]

    view = service.series(spy, "120D", health=_us_etf_health())

    assert view.identity == spy
    assert not view.displays_values
    assert view.display_state is DashboardDisplayState.UNAVAILABLE
    assert view.frame.empty and view.change is None and view.period_high is None
    assert "OHLCV" in (view.unavailable_reason or "")
    assert service.query.files_read == []

    koru = service.series(service.search("KORU").matches[0], "120D", health=_us_etf_health())
    assert koru.display_state is DashboardDisplayState.PROHIBITED
    assert "no external lookup" in (koru.unavailable_reason or "")


def test_us_etf_authorized_fixture_requires_typed_health_and_provider_native_usd_original(tmp_path):
    _write_us_etf_price(tmp_path)
    service = USEtfChartService(tmp_path, authorized_symbols=frozenset({"SPY"}))
    spy = service.search("SPY").matches[0]

    current = service.series(spy, "60D", health=_us_etf_health())

    assert current.displays_values and len(current.frame) == 60
    assert current.as_of == "2026-08-19" and current.identity.currency == "USD"
    assert "USD" in current.price_mode
    assert "adjusted_close" not in current.frame
    assert current.frame.iloc[-1]["close"] == pytest.approx(514.9)
    assert current.identity.identity_source.startswith("https://www.ssga.com/")

    reads = len(service.query.files_read)
    stale = service.series(
        spy, "60D",
        health=_us_etf_health(latest="2026-08-19", expected="2026-08-20", freshness="STALE"),
    )
    assert stale.displays_values and stale.display_state is DashboardDisplayState.VALUE
    assert stale.as_of == "2026-08-19" and stale.expected_as_of == "2026-08-20"
    assert "current-data claims" in (stale.unavailable_reason or "")
    assert len(service.query.files_read) > reads


def test_us_etf_original_frame_rejects_adjustment_conflation_and_pre_inception_rows(tmp_path):
    frame = _write_us_etf_price(tmp_path)
    service = USEtfChartService(tmp_path, authorized_symbols=frozenset({"SPY", "QQQI"}))
    spy = service.search("SPY").matches[0]
    qqqi = service.search("QQQI").matches[0]

    conflated = frame.copy()
    conflated["adjustment_status"] = "ADJUSTED"
    with pytest.raises(ValueError, match="semantics are not separated"):
        service._validated_original_frame(conflated, spy)

    pre_inception = frame.copy()
    pre_inception["symbol"] = "QQQI"
    pre_inception["source_ticker"] = "QQQI"
    pre_inception.loc[0, "date"] = pd.Timestamp("2023-12-29")
    with pytest.raises(ValueError, match="predates the exact fund inception"):
        service._validated_original_frame(pre_inception, qqqi)


def test_ls_session_switch_and_institutional_complex(tmp_path):
    directory = tmp_path / "data/landing/ls_openapi/t8462_raw/run"
    directory.mkdir(parents=True)
    payload = {"t8462OutBlock1": [{"date": "20260814", "sv_08": 1, "sv_17": 2, "sv_18": 3, "sv_07": 4, "sa_17": "5", "sa_18": "6", "sa_07": "7"}]}
    (directory / "01_K2I_F_N.response.json").write_text(json.dumps(payload), encoding="utf-8")
    row = DerivativesDashboardService(tmp_path, LocalParquetQuery(tmp_path / "data")).ls_flow("N")
    assert row["institutional_complex_contracts"] == 7
    assert row["institutional_complex_amount_100m_krw"] == 13
    assert row["status"] == "RAW_DESCRIPTIVE_ONLY"
    assert row["route"] == "HISTORICAL_RESEARCH_RAW"
    assert row["predictive_status"].startswith("PIT_BLOCKED")

    (directory / "02_K2I_F_U.response.json").write_text(
        json.dumps(payload), encoding="utf-8",
    )
    value, as_of, source = DashboardService(
        tmp_path
    )._read_ls_futures_foreign_net_metric()
    assert value == 2
    assert as_of == "2026-08-14"
    assert "LS OpenAPI t8462" in source


def test_volatility_interface_keeps_vix_and_vkospi_distinct(tmp_path):
    data = tmp_path / "data"
    _write(data, "normalized/fred_vix_daily", 2026, pd.DataFrame({"date": pd.date_range("2026-01-01", periods=250), "vixcls": range(1, 251)}))
    values = DashboardService(tmp_path).volatility()
    assert values["VIX"]["value"] == 250
    assert values["VIX"]["percentile_250d"] == 100
    assert values["VKOSPI"]["status"] == "DATA_MISSING"


def test_volatility_250d_percentile_uses_250_valid_sessions(tmp_path):
    data = tmp_path / "data"
    dates = pd.date_range("2025-01-01", periods=270)
    values = pd.Series(range(1, 271), dtype="float64")
    values.iloc[250:269] = pd.NA
    _write(data, "normalized/fred_vix_daily", 2025, pd.DataFrame({
        "date": dates, "vixcls": values,
    }))

    result = DashboardService(tmp_path).volatility()["VIX"]

    assert result["value"] == 270
    assert result["percentile_250d"] == 100


def test_short_selling_scope_regime_boundary_is_explicit():
    assert short_selling_scope_regime("2025-03-03") == "KRX_ONLY"
    assert short_selling_scope_regime("2025-03-04") == "KRX_NXT_COMBINED"


def test_short_selling_official_only_preserves_official_scope(tmp_path):
    data = tmp_path / "data"
    _write(data, "normalized/kr_short_selling_trading_daily", 2026, _official("2026-08-07"), "KOSPI")
    view = MarketMicrostructureService(tmp_path, LocalParquetQuery(data)).short_selling()
    assert view["official"]["status"] == "LATEST_CONFIRMED"
    assert view["official"]["scope"] == "KRX_NXT_COMBINED"
    assert view["provider"] is None
    assert view["inferred_additional_venue"] is None


def test_short_selling_provider_only_is_eod_and_not_live(tmp_path):
    _write_ls_t1716(tmp_path, "2026-08-14", volume=90, value_million=2)
    view = MarketMicrostructureService(tmp_path, LocalParquetQuery(tmp_path / "data")).short_selling()
    assert view["official"] is None
    assert view["provider"]["status"] == "PROVIDER_EOD"
    assert view["provider"]["scope"] == "KRX_ONLY_EMPIRICALLY_CONFIRMED"
    assert view["provider"]["value"] == 2_000_000
    assert view["provider"]["amount_precision"] == "TRUNCATED_TO_MILLION_KRW"


def test_short_selling_same_date_creates_labelled_inferred_remainder(tmp_path):
    data = tmp_path / "data"
    _write(data, "normalized/kr_short_selling_trading_daily", 2026, _official("2026-08-07", volume=120, value=3_000_000), "KOSPI")
    _write_ls_t1716(tmp_path, "2026-08-07", volume=100, value_million=2)
    view = MarketMicrostructureService(tmp_path, LocalParquetQuery(data)).short_selling()
    inferred = view["inferred_additional_venue"]
    assert inferred["volume"] == 20
    assert inferred["volume_precision"] == "EXACT"
    assert inferred["value"] is None
    assert inferred["amount_precision"] == "APPROXIMATE_FROM_TRUNCATED_PROVIDER_AMOUNT"
    assert inferred["status"] == "AGGREGATE_MINUS_KRX_ONLY_INFERRED"
    assert inferred["display_name"] == "Additional venue inferred"


def test_short_selling_date_mismatch_blocks_inference(tmp_path):
    data = tmp_path / "data"
    _write(data, "normalized/kr_short_selling_trading_daily", 2026, _official("2026-08-07"), "KOSPI")
    _write_ls_t1716(tmp_path, "2026-08-08")
    view = MarketMicrostructureService(tmp_path, LocalParquetQuery(data)).short_selling()
    assert view["official"]["market_date"] == pd.Timestamp("2026-08-07")
    assert view["provider"]["market_date"] == pd.Timestamp("2026-08-08")
    assert view["inferred_additional_venue"] is None


def test_dashboard_asset_mapping_keeps_soxx_unavailable_without_substitution(tmp_path):
    data = tmp_path / "data"
    _write(data, "normalized/global_index_price_daily", 2026, pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=3),
        "symbol": ["NASDAQ100"] * 3, "close": [1, 2, 3], "volume": [10, 11, 12],
    }))
    service = DashboardService(tmp_path)
    cards = {item["asset"]: item for item in service.market_cards()}
    assert cards["NDX"]["value"] == 3
    assert cards["SOXX"]["value"] is None
    assert cards["SOXX"]["status"] == "UNKNOWN"


def test_dashboard_soxx_uses_retained_etf_dataset(tmp_path):
    data = tmp_path / "data"
    _write(data, "normalized/global_etf_price_daily", 2026, pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=25),
        "symbol": ["SOXX"] * 25,
        "close": range(100, 125),
        "volume": range(1000, 1025),
    }), "SOXX")
    service = DashboardService(tmp_path)
    card = {item["asset"]: item for item in service.market_cards()}["SOXX"]
    assert card["value"] == 124
    assert card["status"] == "UNKNOWN"
    series = service.chart_series("SOXX", "20D")
    assert len(series) == 20
    assert series.iloc[-1]["volume"] == 1024


def _dashboard_kr_index_health(
    *, freshness="STALE", operational="READY_WITH_FINALITY_GATE",
    artifact_state="READY",
):
    return SimpleNamespace(
        artifact_state=artifact_state,
        rows=(SimpleNamespace(
            dataset="kr_index_daily", latest="2026-08-21",
            expected="2026-08-24", freshness=freshness,
            operational=operational, blocker="N/A", pit="PIT_LIMITED",
            source="fixture retained KRX daily", runtime_coverage="PASS",
        ),),
    )


def _write_dashboard_kospi_history(root: Path) -> None:
    dates = pd.bdate_range(end="2026-08-21", periods=125)
    close = pd.Series(range(3000, 3125), dtype="float64")
    _write(root / "data", "normalized/kr_index_daily", 2026, pd.DataFrame({
        "date": dates,
        "symbol": "KOSPI",
        "open": close - 2,
        "high": close + 5,
        "low": close - 5,
        "close": close,
        "volume": range(1_000_000, 1_000_125),
    }), "KOSPI")


def test_dashboard_kospi_chart_keeps_exact_date_history_when_health_is_stale(
    tmp_path, monkeypatch,
):
    _write_dashboard_kospi_history(tmp_path)
    health = _dashboard_kr_index_health()
    monkeypatch.setattr(DailyHealthArtifactService, "load", lambda _self: health)

    frame = DashboardService(tmp_path).chart_series("KOSPI", "120D")

    assert len(frame) == 120
    assert frame["date"].iloc[-1].date().isoformat() == "2026-08-21"
    assert float(frame["close"].iloc[-1]) == 3124.0


@pytest.mark.parametrize(
    "health",
    [
        _dashboard_kr_index_health(freshness="UNKNOWN"),
        _dashboard_kr_index_health(operational="BLOCKED"),
        _dashboard_kr_index_health(artifact_state="INVALID"),
    ],
)
def test_dashboard_kospi_chart_still_fails_closed_without_valid_retained_authority(
    tmp_path, monkeypatch, health,
):
    _write_dashboard_kospi_history(tmp_path)
    monkeypatch.setattr(DailyHealthArtifactService, "load", lambda _self: health)

    assert DashboardService(tmp_path).chart_series("KOSPI", "120D").empty


def test_dashboard_chart_period_is_bounded_and_health_report_is_consumed(tmp_path):
    data = tmp_path / "data"
    _write(data, "normalized/global_index_price_daily", 2026, pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=150),
        "symbol": ["SP500"] * 150, "close": range(150), "volume": range(150),
    }))
    service = DashboardService(tmp_path)
    assert len(service.chart_series("SP500", "60D")) == 60

    class Report:
        overall_status = "DEGRADED"
        current_count = 2
        expected_lag_count = 1
        stale_count = 3
        operational_blocked_count = 4
        predictive_blocked_count = 5
        research_only_count = 1
        failed_count = 0

        @staticmethod
        def dimension_summary():
            return {
                "freshness": {"CURRENT": 2, "EXPECTED_LAG": 1, "STALE": 3, "UNKNOWN": 0},
                "finality": {"CONFIRMED": 1, "MANUAL_CONFIRMED": 0, "AS_RETRIEVED": 1, "UNKNOWN": 4},
                "operational": {"ELIGIBLE": 2, "MANUAL_ONLY": 0, "BLOCKED": 4},
                "predictive": {"ELIGIBLE": 0, "BLOCKED": 5, "RESEARCH_ONLY": 1},
            }

    service.set_health_report(Report())
    health = service.data_health()
    assert health["source"] == "DailyHealthReport"
    assert health["stale"] == 3
    assert health["predictive_blocked"] == 5
    assert health["dimensions"]["freshness"]["CURRENT"] == 2
    assert health["dimensions"]["finality"]["UNKNOWN"] == 4


@pytest.mark.parametrize(
    ("period", "expected"),
    (("3Y", 756), ("5Y", 1260), ("10Y", 2520)),
)
def test_dashboard_extended_periods_use_exact_row_budgets(period, expected):
    dates = pd.bdate_range("2014-01-01", periods=3000)
    source = pd.concat([
        pd.DataFrame({
            "date": dates,
            "symbol": symbol,
            "close": range(offset, offset + 3000),
            "volume": range(3000),
        })
        for offset, symbol in enumerate(
            ("SP500", "NASDAQ_COMPOSITE", "NASDAQ100", "UNRELATED"),
            start=0,
        )
    ], ignore_index=True).sort_values(["date", "symbol"]).reset_index(drop=True)

    class Query:
        def __init__(self):
            self.tail_rows = None
            self.filters = None

        def tail(self, _dataset, *, rows, filters, **_kwargs):
            self.tail_rows = rows
            self.filters = filters
            symbol = filters["symbol"][0]
            return source.loc[source["symbol"].eq(symbol)].tail(rows).reset_index(drop=True)

        def read(self, *_args, **_kwargs):
            raise AssertionError("finite presets must not issue a MAX read")

    query = Query()
    result = IndexQueryService(query).asset_series("SP500", period)
    assert query.tail_rows == PERIOD_ROWS[period] + 130
    assert query.filters == {"symbol": ("SP500",)}
    assert len(result) == expected
    assert result.iloc[0]["close"] == 3000 - expected
    assert result.iloc[-1]["close"] == 2999


def test_dashboard_max_reads_only_selected_contract_and_attaches_identity(tmp_path):
    source = pd.DataFrame({
        "date": pd.bdate_range("2014-01-01", periods=3000),
        "symbol": ["SP500"] * 2999 + ["NASDAQ_COMPOSITE"],
        "close": range(3000),
        "volume": range(3000),
    })

    class Query:
        def __init__(self):
            self.datasets = []
            self.filters = []

        def read(self, dataset, *, filters, **_kwargs):
            self.datasets.append(dataset)
            self.filters.append(filters)
            return source.loc[
                source["symbol"].eq(filters["symbol"][0])
            ].reset_index(drop=True)

        def tail(self, *_args, **_kwargs):
            raise AssertionError("MAX must read the contracted dataset")

    service = DashboardService(tmp_path)
    service.index = IndexQueryService(Query())
    result = service.chart_series("SP500", "MAX")
    coverage = result.attrs[DASHBOARD_CHART_COVERAGE_ATTR]
    assert isinstance(coverage, DashboardChartCoverage)
    assert service.index.query.datasets == ["normalized/global_index_price_daily"]
    assert service.index.query.filters == [{"symbol": ("SP500",)}]
    assert set(result["symbol"]) == {"SP500"}
    assert coverage.period == "MAX"
    assert coverage.requested_sessions is None
    assert coverage.available_sessions == 2999
    assert coverage.dataset_id == "global_index_price_daily"
    assert coverage.series_id == "SP500"
    assert coverage.retained_scope == "SELECTED_CONTRACTED_LOCAL_DATASET"


@pytest.mark.parametrize(
    ("asset", "dataset", "symbol"),
    (
        ("SP500", "normalized/global_index_price_daily", "SP500"),
        ("SOXX", "normalized/global_etf_price_daily", "SOXX"),
        ("GOLD", "normalized/global_commodity_futures_daily", "GOLD"),
    ),
)
def test_dashboard_finite_routes_partition_exact_symbol_before_tail(
    asset, dataset, symbol,
):
    dates = pd.bdate_range("2020-01-02", periods=900)
    selected = pd.DataFrame({
        "date": dates, "symbol": symbol,
        "open": range(1000, 1900), "high": range(1001, 1901),
        "low": range(999, 1899), "close": range(1000, 1900),
        "volume": range(900),
    })

    class Query:
        def tail(self, actual_dataset, *, rows, filters, **_kwargs):
            assert actual_dataset == dataset
            assert rows == 886
            assert filters == {"symbol": (symbol,)}
            return selected.tail(rows).reset_index(drop=True)

        def read(self, *_args, **_kwargs):
            raise AssertionError("finite route must remain a bounded tail read")

    frame = IndexQueryService(Query()).asset_series(asset, "3Y")
    assert len(frame) == 756
    assert set(frame["symbol"]) == {symbol}


def test_dashboard_partial_coverage_discloses_requested_and_available_span(tmp_path):
    source = pd.DataFrame({
        "date": pd.bdate_range("2025-01-02", periods=400),
        "symbol": "SP500",
        "close": range(400),
        "volume": range(400),
    })
    service = DashboardService(tmp_path)
    service.index.asset_series = lambda asset, period: source.copy()
    result = service.chart_series("SP500", "10Y")
    coverage = result.attrs[DASHBOARD_CHART_COVERAGE_ATTR]
    assert coverage.requested_sessions == 2520
    assert coverage.available_sessions == 400
    assert coverage.available_start == source.iloc[0]["date"].date().isoformat()
    assert coverage.available_end == source.iloc[-1]["date"].date().isoformat()
    assert coverage.complete is False
    assert result["close"].tolist() == source["close"].tolist()


def test_dashboard_sections_use_typed_metrics_and_never_infer_blocked_fx_cross(tmp_path):
    data = tmp_path / "data"
    _write(data, "normalized/fred_usd_fx_daily", 2026, pd.DataFrame({
        "date": [pd.Timestamp("2026-08-07")],
        "dexkous": [1400.0], "dexjpus": [150.0],
    }))
    service = DashboardService(tmp_path)
    service.dashboard_metrics = lambda: {
        "USD_KRW": "typed-usd-krw", "USD_JPY": "typed-usd-jpy",
        "UST2": "typed-2y", "UST10": "typed-10y",
        "UST30": "typed-30y", "UST10_2_SPREAD": "typed-spread",
        "VIX": "typed-vix", "VKOSPI": "typed-vkospi", "GOLD": "blocked-gold", "WTI": "blocked-wti",
    }
    fx = service.sections()["fx"]
    assert fx["USD/KRW"] == "typed-usd-krw"
    assert fx["USD/JPY"] == "typed-usd-jpy"
    assert fx["JPY/KRW"] is None


def test_dashboard_usdjpy_uses_dexjpus_native_direction_and_typed_daily_change(tmp_path):
    data = tmp_path / "data"
    _write(data, "normalized/fred_usd_fx_daily", 2026, pd.DataFrame({
        "date": [pd.Timestamp("2026-08-13"), pd.Timestamp("2026-08-14")],
        "dexkous": [1390.0, 1392.0],
        "dexjpus": [159.35, 159.21],
    }))
    health = _health((
        "fred_usd_fx_daily", "2026-08-14", "2026-08-18",
        "EXPECTED_LAG", "READY", "N/A", "PIT_LIMITED", "FRED H.10",
    ))

    service = DashboardService(tmp_path)
    metrics = service.dashboard_metrics(health)
    metric = metrics["USD_JPY"]

    assert metric.displays_value
    assert metric.dataset_id == "fred_usd_fx_daily"
    assert metric.series_id == "USD_JPY"
    assert metric.unit == "JPY per USD"
    assert metric.value == 159.21
    assert metric.change == pytest.approx(-0.14)
    assert metric.change_pct == pytest.approx(-0.14 / 159.35 * 100)
    assert metric.value != pytest.approx(1 / 159.21)
    assert metric.value != pytest.approx(159.21 / 100)
    assert service.dashboard_series(metrics)["USD_JPY"].frame["value"].tolist() == [159.35, 159.21]

    stale_health = _health((
        "fred_usd_fx_daily", "2026-08-14", "2026-08-18",
        "STALE", "READY", "N/A", "PIT_LIMITED", "FRED H.10",
    ))
    stale = service.dashboard_metrics(stale_health)["USD_JPY"]
    assert not stale.displays_value and stale.value is None


def test_dashboard_metric_view_gates_current_expected_lag_stale_unknown_and_blocked(tmp_path):
    data = tmp_path / "data"
    _write(data, "normalized/global_index_price_daily", 2026, pd.DataFrame({
        "date": [pd.Timestamp("2026-08-18")] * 2,
        "symbol": ["SP500", "NASDAQ_COMPOSITE"], "close": [6500.0, 22000.0], "volume": [1, 1],
    }))
    _write(data, "normalized/fred_vix_daily", 2026, pd.DataFrame({
        "date": [pd.Timestamp("2026-08-17")], "vixcls": [15.0],
    }))
    health = _health(
        ("global_index_price_daily", "2026-08-18", "2026-08-18", "CURRENT", "READY", "N/A", "PIT_LIMITED", "Yahoo"),
        ("fred_vix_daily", "2026-08-17", "2026-08-17", "EXPECTED_LAG", "READY", "N/A", "PIT_LIMITED", "FRED"),
    )
    metrics = DashboardService(tmp_path).dashboard_metrics(health)
    assert metrics["SP500"].displays_value
    assert metrics["VIX"].displays_value

    promote_dashboard_current(tmp_path, [DashboardCurrentObservation(
        identity="SP500", value=7684.13, unit="index points",
        source_date="2026-08-20", retrieved_at_utc="2026-08-20T16:50:44+00:00",
        provider="FinanceDataReader 0.9.202 / Yahoo daily", route="YAHOO:^GSPC",
    )])
    refreshed = DashboardService(tmp_path).dashboard_metrics(health)["SP500"]
    assert refreshed.value is None
    assert refreshed.as_of == "2026-08-20"
    assert refreshed.display_state is DashboardDisplayState.REFRESH_REQUIRED
    assert refreshed.route == "FDR_DAILY_CURRENT_DISPLAY_FALLBACK"
    assert "FDR" in refreshed.source and refreshed.pit_status == "PIT_BLOCKED"
    assert "CURRENT_SOURCE_TIMESTAMP_REQUIRED" in (refreshed.unavailable_reason or "")

    for freshness in ("STALE", "UNKNOWN"):
        changed = _health(
            ("global_index_price_daily", "2026-08-18", "2026-08-18", freshness, "READY", "N/A", "PIT_LIMITED", "Yahoo"),
        )
        metric = DashboardService(tmp_path).dashboard_metrics(changed)["SP500"]
        assert metric.value is None
        assert metric.display_state is DashboardDisplayState.REFRESH_REQUIRED
        assert metric.route == "FDR_DAILY_CURRENT_DISPLAY_FALLBACK"
        assert "CURRENT_SOURCE_TIMESTAMP_REQUIRED" in (metric.unavailable_reason or "")

    blocked = _health(
        ("global_index_price_daily", "2026-08-18", "2026-08-18", "CURRENT", "BLOCKED", "SOURCE_CONTRACT", "PIT_BLOCKED", "Yahoo"),
    )
    metric = DashboardService(tmp_path).dashboard_metrics(blocked)["SP500"]
    assert metric.value is None
    assert metric.display_state is DashboardDisplayState.PROHIBITED

    failed_runtime = _health((
        "global_index_price_daily", "2026-08-18", "2026-08-18",
        "CURRENT", "READY", "N/A", "PIT_LIMITED", "Yahoo",
    ))
    failed_runtime.rows[0].runtime_coverage = "FAILED:ValueError"
    metric = DashboardService(tmp_path).dashboard_metrics(failed_runtime)["SP500"]
    assert metric.value is None
    assert metric.display_state is DashboardDisplayState.REFRESH_REQUIRED
    assert "CURRENT_SOURCE_TIMESTAMP_REQUIRED" in (metric.unavailable_reason or "")


def test_current_observation_coverage_keeps_broker_candidates_numeric_free(tmp_path):
    promote_dashboard_current(tmp_path, [DashboardCurrentObservation(
        identity="SP500", value=7684.13, unit="index points",
        source_date="2026-08-20", retrieved_at_utc="2026-08-20T16:50:44+00:00",
        provider="FinanceDataReader 0.9.202 / Yahoo daily", route="YAHOO:^GSPC",
    )])
    promote_current_display(tmp_path, CurrentDisplayObservation(
        symbol="000660", value=1_691_000, unit="KRW", source_date="2026-08-20",
        retrieved_at_utc="2026-08-20T16:45:00+00:00",
        provider="FinanceDataReader 0.9.202 / Naver", interval="1d",
        finality="POLLABLE_DAILY_AS_RETRIEVED",
    ))

    coverage = DashboardService(tmp_path).current_observation_coverage(
        now_utc="2026-08-21T00:30:00Z",
    )

    sp500 = coverage["SP500"]
    assert not sp500.displays_value
    assert sp500.value is None
    assert (sp500.provider, sp500.route, sp500.interval, sp500.as_of) == (
        "FinanceDataReader 0.9.202 / Yahoo daily", "YAHOO:^GSPC", "1d", "2026-08-20",
    )
    assert sp500.freshness == "CURRENT_GATE_BLOCKED"
    assert "CURRENT_SOURCE_TIMESTAMP_REQUIRED" in (sp500.unavailable_reason or "")
    equity = coverage["EQUITY_000660"]
    assert not equity.displays_value and equity.value is None
    assert equity.unit == "KRW" and equity.interval == "1d"
    assert equity.freshness == "CURRENT_GATE_BLOCKED"

    for coverage_id in ("KB_IVSA0070", "TOSS_KOSPI", "TOSS_KOSDAQ", "LS_T8412"):
        row = coverage[coverage_id]
        assert not row.displays_value
        assert row.value is None
        assert row.freshness == "UNAVAILABLE"
        assert row.unavailable_reason
    assert coverage["TOSS_KOSPI"].interval == "snapshot"
    assert coverage["LS_T8412"].as_of is None
    assert coverage["LS_T8412"].unavailable_reason.startswith(LS_T8412_CURRENT_SAFE_REASON)


def test_ur246_toss_domestic_projections_take_current_display_precedence(tmp_path):
    for symbol in ("KOSPI", "KOSDAQ", "000660", "005930"):
        _write_toss_domestic_ur246_current(
            tmp_path, symbol=symbol, provider_timestamp_utc="2026-08-24T01:00:00+00:00",
        )

    coverage = DashboardService(tmp_path).current_observation_coverage(
        now_utc="2026-08-24T01:20:00+00:00",
    )

    for coverage_id, symbol in (("KOSPI", "KOSPI"), ("KOSDAQ", "KOSDAQ")):
        row = coverage[coverage_id]
        assert row.displays_value
        assert row.route == f"toss-market-price:{symbol}:snapshot:PROVISIONAL"
        assert row.provider == "tossinvest_open_api"
        assert row.timestamp_basis == "PROVIDER_TIMESTAMP"
    assert coverage["TOSS_KOSPI"].displays_value
    assert coverage["TOSS_KOSDAQ"].displays_value

    for coverage_id in ("EQUITY_000660", "EQUITY_005930"):
        equity = coverage[coverage_id]
        assert equity.displays_value and equity.value == pytest.approx(250_000.0)
        assert equity.route.endswith(":TOSS_ACTIVE_SESSION_60M")
        assert equity.provider == "tossinvest_open_api"
        assert equity.unit == "KRW per share"
        assert equity.timestamp_basis == "PROVIDER_TIMESTAMP"

    stale = DashboardService(tmp_path).current_observation_coverage(
        now_utc="2026-08-24T02:00:01+00:00",
    )
    assert not stale["KOSPI"].displays_value
    assert not stale["EQUITY_000660"].displays_value
    assert not stale["EQUITY_005930"].displays_value


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    (
        (
            "identity",
            {"dataset_id": "KR_EQUITY_CURRENT", "market": "XKRX", "symbol": "000660"},
        ),
        ("unit", "KRW"),
        ("route_id", TOSS_005930_NXT_CLOSE_UR241_ROUTE.route_id),
        ("timestamp_basis", "RETRIEVAL_TIMESTAMP"),
    ),
)
def test_ur246_005930_active_session_rejects_contract_mismatch(
    tmp_path, field, invalid_value,
):
    path = _write_toss_domestic_ur246_current(
        tmp_path, symbol="005930",
        provider_timestamp_utc="2026-08-24T01:00:00+00:00",
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["observations"][0][field] = invalid_value
    path.write_text(json.dumps(payload), encoding="utf-8")

    coverage = DashboardService(tmp_path).current_observation_coverage(
        now_utc="2026-08-24T01:20:00+00:00",
    )["EQUITY_005930"]

    assert not coverage.displays_value
    assert coverage.value is None
    assert coverage.unavailable_reason.startswith(TOSS_DOMESTIC_UR246_SAFE_REASON)


def test_ur246_005930_active_session_is_numeric_free_for_malformed_or_nxt_only_state(
    tmp_path,
):
    path = tmp_path / _toss_domestic_ur246_path("005930")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not-json", encoding="utf-8")
    malformed = DashboardService(tmp_path).current_observation_coverage(
        now_utc="2026-08-24T01:20:00+00:00",
    )["EQUITY_005930"]
    assert not malformed.displays_value and malformed.value is None
    assert "malformed" in (malformed.unavailable_reason or "")

    path.unlink()
    nxt_path = _write_toss_nxt_close_current(
        tmp_path, symbol="005930",
        provider_timestamp_utc="2026-08-21T10:59:59+00:00",
    )
    nxt_only = DashboardService(tmp_path).current_observation_coverage(
        now_utc="2026-08-21T13:25:00+00:00",
    )
    assert not nxt_only["EQUITY_005930"].displays_value
    assert nxt_only["EQUITY_005930"].value is None
    assert nxt_only["EQUITY_005930_NXT_CLOSE"].displays_value

    nxt_path.unlink()
    _write_ls_t8412_current(tmp_path)
    ls_only = DashboardService(tmp_path).current_observation_coverage(
        now_utc="2026-08-21T06:00:00+00:00",
    )
    assert not ls_only["EQUITY_005930"].displays_value
    assert ls_only["EQUITY_005930"].value is None
    assert ls_only["LS_T8412"].displays_value


def test_ur246_retrieval_time_indices_drive_headline_cards_without_claiming_provider_time(tmp_path):
    for symbol in ("KOSPI", "KOSDAQ"):
        _write_toss_domestic_ur246_current(
            tmp_path, symbol=symbol,
            provider_timestamp_utc="2026-08-24T01:00:00+00:00",
            timestamp_basis="RETRIEVAL_TIMESTAMP",
        )

    metrics = DashboardService(tmp_path).dashboard_metrics(
        _health(), now_utc="2026-08-24T01:20:00+00:00",
    )

    for symbol in ("KOSPI", "KOSDAQ"):
        metric = metrics[symbol]
        assert metric.displays_value and metric.value == pytest.approx(2810.25)
        assert metric.dataset_id == "TOSS_MARKET_PRICE_SNAPSHOT"
        assert metric.route == f"toss-market-price:{symbol}:snapshot:PROVISIONAL"
        assert metric.freshness == "CURRENT_RETRIEVAL_TIME"
        assert metric.timestamp_basis == "RETRIEVAL_TIMESTAMP"
        assert metric.retrieved_at_utc == "2026-08-24T01:00:00+00:00"
        assert "provider event time unavailable" in metric.source
        assert metric.pit_status == "PIT_BLOCKED"


def test_ur246_clock_headlines_keep_completed_daily_index_sparklines_bounded(tmp_path):
    dates = pd.bdate_range(end="2026-08-25", periods=25)
    for symbol, base in (("KOSPI", 2700.0), ("KOSDAQ", 850.0)):
        close = pd.Series([base + offset for offset in range(len(dates))])
        _write(
            tmp_path / "data", "normalized/kr_index_daily", 2026,
            pd.DataFrame({
                "date": dates,
                "symbol": symbol,
                "open": close - 1.0,
                "high": close + 2.0,
                "low": close - 2.0,
                "close": close,
                "volume": list(range(1_000, 1_000 + len(dates))),
            }),
            symbol,
        )
        _write_toss_domestic_ur246_current(
            tmp_path,
            symbol=symbol,
            provider_timestamp_utc="2026-08-26T01:00:00+00:00",
            timestamp_basis="RETRIEVAL_TIMESTAMP",
        )
    health = _health((
        "kr_index_daily", "2026-08-25", "2026-08-25", "CURRENT",
        "READY_WITH_FINALITY_GATE", "N/A", "PIT_LIMITED", "pykrx",
    ))

    service = DashboardService(tmp_path)
    metrics = service.dashboard_metrics(
        health, now_utc="2026-08-26T01:20:00+00:00",
    )
    series = service.dashboard_series(metrics)

    for symbol in ("KOSPI", "KOSDAQ"):
        metric = metrics[symbol]
        assert metric.displays_value and metric.value == pytest.approx(2810.25)
        assert metric.as_of == "08-26 10:00 KST"
        assert metric.route == _toss_domestic_ur246_route(symbol).route_id
        assert symbol in series and len(series[symbol].frame) == 20
        assert series[symbol].metric is metric
        assert series[symbol].frame["date"].max().date().isoformat() == "2026-08-25"
        assert not series[symbol].frame["date"].dt.date.eq(
            pd.Timestamp("2026-08-26").date()
        ).any()
        assert metric.value not in series[symbol].frame["value"].tolist()


def test_ur246_clock_headlines_keep_malformed_or_missing_daily_graph_numeric_free(tmp_path):
    _write(
        tmp_path / "data", "normalized/kr_index_daily", 2026,
        pd.DataFrame({
            "date": [pd.Timestamp("2026-08-25")],
            "symbol": ["KOSPI"],
            "open": [2700.0], "high": [2710.0], "low": [2690.0],
            "close": [float("inf")], "volume": [1_000],
        }),
        "KOSPI",
    )
    for symbol in ("KOSPI", "KOSDAQ"):
        _write_toss_domestic_ur246_current(
            tmp_path,
            symbol=symbol,
            provider_timestamp_utc="2026-08-26T01:00:00+00:00",
        )
    health = _health((
        "kr_index_daily", "2026-08-25", "2026-08-25", "CURRENT",
        "READY_WITH_FINALITY_GATE", "N/A", "PIT_LIMITED", "pykrx",
    ))

    service = DashboardService(tmp_path)
    metrics = service.dashboard_metrics(
        health, now_utc="2026-08-26T01:20:00+00:00",
    )
    series = service.dashboard_series(metrics)

    assert all(metrics[symbol].displays_value for symbol in ("KOSPI", "KOSDAQ"))
    assert "KOSPI" not in series
    assert "KOSDAQ" not in series


def test_yahoo_completed_kr_close_drives_headline_when_no_fresh_toss_snapshot(tmp_path):
    root = tmp_path / "data/state/current_observations/global60m_current"
    root.mkdir(parents=True)
    for coverage_id, symbol, route_symbol, value in (
        ("kospi_current_60m", "^KS11", "KS11", 6717.19),
        ("kosdaq_current_60m", "^KQ11", "KQ11", 813.56),
    ):
        route_id = f"yahoo-market-current:XKRX:{route_symbol}"
        (root / f"{coverage_id}.json").write_text(json.dumps({
            "schema_version": 1,
            "observations": [{
                "route_id": route_id,
                "identity": {
                    "dataset_id": "MARKET_PRICE_CURRENT",
                    "market": "XKRX", "symbol": symbol,
                },
                "interval": "30m", "value": value, "unit": "index points",
                "provider": "YAHOO", "upstream_provider": "YAHOO_CHART_API",
                "source_route": f"YAHOO_CHART_30M:{symbol}",
                "provider_timestamp_utc": "2026-08-24T06:30:00+00:00",
                "retrieved_at_utc": "2026-08-24T07:02:00+00:00",
                "finality": "AS_RETRIEVED", "display_only": True,
                "pit_safe": False, "timestamp_basis": "PROVIDER_TIMESTAMP",
            }],
            "circuits": {}, "decisions": {},
        }), encoding="utf-8")

    metrics = DashboardService(tmp_path).dashboard_metrics(
        _health(), now_utc="2026-08-24T13:00:00+00:00",
    )

    assert metrics["KOSPI"].value == pytest.approx(6717.19)
    assert metrics["KOSDAQ"].value == pytest.approx(813.56)
    for symbol in ("KOSPI", "KOSDAQ"):
        metric = metrics[symbol]
        assert metric.displays_value
        assert metric.freshness == "MARKET_CLOSED_LAST_FINAL"
        assert metric.route.startswith("yahoo-market-current:XKRX:")
        assert metric.pit_status == "PIT_BLOCKED"


def test_ls_current_observation_coverage_is_exact_local_only_and_fail_closed(tmp_path):
    service = DashboardService(tmp_path)

    absent = service.current_observation_coverage()["LS_T8412"]
    assert not absent.displays_value
    assert absent.value is None
    assert absent.route == LS_T8412_CURRENT_ROUTE.route_id
    assert absent.unavailable_reason.startswith(LS_T8412_CURRENT_SAFE_REASON)

    malformed_path = tmp_path / LS_T8412_CURRENT_OBSERVATION_PATH
    malformed_path.parent.mkdir(parents=True, exist_ok=True)
    malformed_path.write_text("{not-json", encoding="utf-8")
    malformed = service.current_observation_coverage()["LS_T8412"]
    assert not malformed.displays_value
    assert "malformed" in malformed.unavailable_reason

    _write_ls_t8412_current(tmp_path, identity={
        "dataset_id": "KR_EQUITY_CURRENT", "market": "XKRX", "symbol": "000660",
    })
    wrong_identity = service.current_observation_coverage()["LS_T8412"]
    assert not wrong_identity.displays_value
    assert wrong_identity.value is None
    assert wrong_identity.unavailable_reason.startswith(LS_T8412_CURRENT_SAFE_REASON)

    _write_ls_t8412_current(tmp_path, provider_timestamp_utc="2026-08-20T05:45:00+00:00")
    wrong_date = service.current_observation_coverage()["LS_T8412"]
    assert not wrong_date.displays_value
    assert "exact 005930 15-minute contract" in wrong_date.unavailable_reason

    _write_ls_t8412_current(tmp_path)
    accepted = service.current_observation_coverage(
        now_utc="2026-08-21T06:00:00Z",
    )["LS_T8412"]
    assert accepted.displays_value
    assert accepted.value == pytest.approx(71_500.0)
    assert (accepted.route, accepted.interval, accepted.as_of, accepted.freshness) == (
        "ls-t8412-current:XKRX:005930", "15m", "2026-08-21", "RETAINED_AS_RETRIEVED",
    )
    assert (accepted.finality, accepted.provider_timestamp_utc, accepted.source_route) == (
        "AS_RETRIEVED", "2026-08-21T05:45:00+00:00", "LS_OPENAPI:/stock/chart:t8412",
    )
    assert accepted.display_only is True and accepted.pit_safe is False

    stale = service.current_observation_coverage(
        now_utc="2026-08-21T07:00:01Z",
    )["LS_T8412"]
    assert not stale.displays_value and stale.value is None
    assert stale.freshness == "CURRENT_GATE_BLOCKED"
    assert "CURRENT_SOURCE_AGE_OVER_60M" in (stale.unavailable_reason or "")


def test_dashboard_uses_exact_naver_web_000660_snapshot_only_while_shared_gate_accepts_it(tmp_path):
    _write_naver_web_000660_current(tmp_path)

    accepted = DashboardService(tmp_path).current_observation_coverage(
        now_utc="2026-08-21T04:31:15Z",
    )["EQUITY_000660"]

    assert accepted.displays_value and accepted.value == pytest.approx(738_000.0)
    assert (
        accepted.provider, accepted.route, accepted.interval, accepted.unit,
        accepted.finality, accepted.provider_timestamp_utc, accepted.source_route,
    ) == (
        "NAVER_FINANCE_WEB", "naver-web-current:XKRX:000660", "snapshot", "KRW per share",
        "PROVISIONAL", "2026-08-21T04:26:15+00:00", "NAVER_WEB:/api/stock/000660/basic",
    )
    assert accepted.unavailable_reason == NAVER_WEB_000660_PROVENANCE_WARNING
    assert accepted.display_only is True and accepted.pit_safe is False

    stale = DashboardService(tmp_path).current_observation_coverage(
        now_utc="2026-08-21T05:26:16Z",
    )["EQUITY_000660"]
    assert not stale.displays_value and stale.value is None
    assert "CURRENT_SOURCE_AGE_OVER_60M" in (stale.unavailable_reason or "")


def test_toss_inferred_nxt_close_rows_are_visible_only_as_explicit_post_close_labels(tmp_path):
    _write_toss_nxt_close_current(tmp_path, symbol="000660")
    _write_toss_nxt_close_current(tmp_path, symbol="005930")

    coverage = DashboardService(tmp_path).current_observation_coverage(
        now_utc="2026-08-21T13:25:00Z",  # 22:25 KST
    )

    for coverage_id, timestamp in (
        ("EQUITY_000660_NXT_CLOSE", "19:59:59"),
        ("EQUITY_005930_NXT_CLOSE", "19:59:59"),
    ):
        row = coverage[coverage_id]
        assert row.displays_value
        assert row.freshness == "NXT_SESSION_CLOSE_INFERRED"
        assert row.visible_label == f"NXT \ub9c8\uac10(\uc2dc\uac04\ucc3d \ucd94\ub860) {timestamp}"
        """Legacy mojibake assertion retained below for patch provenance only.
        assert row.visible_label == f"NXT 마감(시간창 추론) {timestamp}"
        """
        assert row.unavailable_reason and "TOSS_NXT_CLOSE_INFERRED_FROM_EXCLUSIVE_TIME_WINDOW" in row.unavailable_reason

    weekend = DashboardService(tmp_path).current_observation_coverage(
        now_utc="2026-08-22T03:00:00Z",
    )
    for coverage_id in ("EQUITY_000660_NXT_CLOSE", "EQUITY_005930_NXT_CLOSE"):
        row = weekend[coverage_id]
        assert row.displays_value
        assert row.freshness == "MARKET_CLOSED_LAST_FINAL"
        assert row.visible_label and row.visible_label.startswith(
            "장마감 · NXT 마감(시간창 추론) 2026-08-21"
        )
        assert "NXT_MARKET_CLOSED_LAST_FINAL" in (row.unavailable_reason or "")

    next_session = DashboardService(tmp_path).current_observation_coverage(
        now_utc="2026-08-24T00:10:00Z",
    )
    for coverage_id in ("EQUITY_000660_NXT_CLOSE", "EQUITY_005930_NXT_CLOSE"):
        row = next_session[coverage_id]
        assert not row.displays_value and row.value is None
        assert row.unavailable_reason == "NXT_SOURCE_DATE_NOT_TODAY_KST"

    _write_toss_nxt_close_current(
        tmp_path, symbol="000660", provider_timestamp_utc="2026-08-21T10:54:59+00:00",
    )
    rejected = DashboardService(tmp_path).current_observation_coverage(
        now_utc="2026-08-21T13:25:00Z",
    )["EQUITY_000660_NXT_CLOSE"]
    assert not rejected.displays_value
    assert rejected.unavailable_reason == "NXT_CLOSE_TIMESTAMP_OUTSIDE_1955_2000_KST"


def test_ur199_mobile_basic_paths_feed_000660_coverage_and_005930_header_only_while_fresh(tmp_path):
    _write_naver_mobile_basic_ur199_current(tmp_path, symbol="000660", value=251_000.0)
    _write_naver_mobile_basic_ur199_current(tmp_path, symbol="005930", value=72_300.0)
    coverage = DashboardService(tmp_path).current_observation_coverage(
        now_utc="2026-08-24T00:40:00Z",
    )["EQUITY_000660"]
    assert coverage.displays_value and coverage.value == pytest.approx(251_000.0)
    assert (
        coverage.route, coverage.interval, coverage.unit, coverage.finality,
        coverage.provider_timestamp_utc, coverage.source_route,
    ) == (
        "naver-mobile-basic-current:XKRX:000660", "snapshot", "KRW per share", "PROVISIONAL",
        "2026-08-24T00:35:00+00:00",
        "NAVER_FINANCE_WEB:m.stock.naver.com/api/stock/000660/basic",
    )
    assert coverage.display_only is True and coverage.pit_safe is False

    _write_equity_master(tmp_path, "KOSPI", _equity_master_rows("KOSPI"))
    _write_equity_master(tmp_path, "KOSDAQ", _equity_master_rows("KOSDAQ"))
    dates = pd.bdate_range(end="2026-08-19", periods=20)
    close = pd.Series(range(70_000, 70_020), dtype="int64")
    _write(tmp_path / "data", "normalized/kr_equity_price_daily", 2026, pd.DataFrame({
        "date": dates, "market": "KOSPI", "symbol": "005930",
        "open": close - 2, "high": close + 5, "low": close - 5,
        "close": close, "volume": range(1_000_000, 1_000_020),
        "source": "provider-native", "source_operation": "daily", "source_date": dates,
    }), "KOSPI")
    service = EquityChartService(tmp_path)
    view = service.series(
        service.search("005930").matches[0], "20D", health=_equity_health(),
        now_utc="2026-08-24T00:40:00Z",
    )
    assert view.current_refresh_status == "CURRENT_SOURCE_TIMESTAMP_VALID"
    assert view.current_value == pytest.approx(72_300.0)
    assert view.current_route == "naver-mobile-basic-current:XKRX:005930"
    assert view.current_interval == "snapshot"
    assert view.current_finality == "PROVISIONAL"
    assert view.current_display_only is True and view.current_pit_safe is False
    assert view.frame["close"].iloc[-1] == 70_019

    _write_toss_nxt_close_current(tmp_path, symbol="005930")
    nxt_close = service.series(
        service.search("005930").matches[0], "20D", health=_equity_health(),
        now_utc="2026-08-21T13:25:00Z",
    )
    assert nxt_close.current_value == pytest.approx(72_000.0)
    assert nxt_close.current_refresh_status == "NXT_SESSION_CLOSE_INFERRED"
    assert nxt_close.current_visible_label == "NXT \ub9c8\uac10(\uc2dc\uac04\ucc3d \ucd94\ub860) 19:59:59"
    """Legacy mojibake assertion retained below for patch provenance only.
    assert nxt_close.current_visible_label == "NXT 마감(시간창 추론) 19:59:59"
    """
    assert nxt_close.current_unavailable_reason and "NOT_LIVE" in nxt_close.current_unavailable_reason

    weekend_close = service.series(
        service.search("005930").matches[0], "20D", health=_equity_health(),
        now_utc="2026-08-22T03:00:00Z",
    )
    assert weekend_close.current_value == pytest.approx(72_000.0)
    assert weekend_close.current_refresh_status == "MARKET_CLOSED_LAST_FINAL"
    assert weekend_close.current_visible_label and weekend_close.current_visible_label.startswith(
        "장마감 · NXT 마감(시간창 추론) 2026-08-21"
    )

    stale = DashboardService(tmp_path).current_observation_coverage(
        now_utc="2026-08-24T01:35:01Z",
    )["EQUITY_000660"]
    assert not stale.displays_value and stale.value is None
    assert "CURRENT_SOURCE_AGE_OVER_60M" in stale.unavailable_reason
    stale_header = service.series(
        service.search("005930").matches[0], "20D", health=_equity_health(),
        now_utc="2026-08-24T01:35:01Z",
    )
    assert stale_header.current_value is None
    assert "NXT_SOURCE_DATE_NOT_TODAY_KST" in (stale_header.current_unavailable_reason or "")

    _write_naver_mobile_basic_ur199_current(tmp_path, symbol="000660", value=251_000.0, unit="KRW")
    invalid = DashboardService(tmp_path).current_observation_coverage(
        now_utc="2026-08-24T00:40:00Z",
    )["EQUITY_000660"]
    assert not invalid.displays_value
    assert invalid.unavailable_reason.startswith(TOSS_DOMESTIC_UR246_SAFE_REASON)
    assert NAVER_MOBILE_BASIC_UR199_SAFE_REASON in invalid.unavailable_reason


def test_dashboard_uses_exact_nasdaq_soxx_snapshot_only_while_shared_gate_accepts_it(tmp_path):
    service = DashboardService(tmp_path)
    absent = service.current_observation_coverage()["SOXX"]
    assert not absent.displays_value
    assert absent.unavailable_reason.startswith(NASDAQ_SOXX_INFO_CURRENT_SAFE_REASON)

    _write_nasdaq_soxx_current(tmp_path, unit="USD")
    wrong_unit = service.current_observation_coverage()["SOXX"]
    assert not wrong_unit.displays_value
    assert "exact SOXX ETF Nasdaq current contract" in wrong_unit.unavailable_reason

    _write_nasdaq_soxx_current(tmp_path)
    health = _health((
        "global_etf_price_daily", "2026-08-20", "2026-08-20",
        "CURRENT", "READY", "N/A", "PIT_BLOCKED", "Yahoo",
    ))
    accepted = service.current_observation_coverage(
        now_utc="2026-08-21T08:20:00Z",
    )["SOXX"]
    metric = service.dashboard_metrics(health, now_utc="2026-08-21T08:20:00Z")["SOXX"]
    assert accepted.displays_value and accepted.value == pytest.approx(526.6332)
    assert (
        accepted.provider, accepted.route, accepted.interval, accepted.unit,
        accepted.finality, accepted.provider_timestamp_utc, accepted.source_route,
    ) == (
        "NASDAQ_OFFICIAL", "nasdaq-soxx-info-api:NASDAQ:SOXX", "snapshot", "USD per share",
        "PROVISIONAL", "2026-08-21T08:08:00+00:00",
        "NASDAQ_OFFICIAL:api.nasdaq.com/api/quote/SOXX/info?assetclass=etf",
    )
    assert accepted.display_only is True and accepted.pit_safe is False
    assert metric.displays_value and metric.value == pytest.approx(526.6332)
    assert metric.source_timestamp == accepted.provider_timestamp_utc
    assert metric.route == accepted.route

    stale = service.current_observation_coverage(
        now_utc="2026-08-21T09:08:01Z",
    )["SOXX"]
    stale_metric = service.dashboard_metrics(health, now_utc="2026-08-21T09:08:01Z")["SOXX"]
    assert not stale.displays_value and stale.value is None
    assert not stale_metric.displays_value and stale_metric.value is None
    assert "CURRENT_SOURCE_AGE_OVER_60M" in stale.unavailable_reason


def test_dashboard_displays_validated_yahoo_continuous_futures_as_descriptive_only(tmp_path):
    data = tmp_path / "data"
    rows = []
    for symbol, base in (
        ("NASDAQ100_FUTURES", 30_000.0), ("GOLD", 4_400.0),
        ("WTI_CRUDE_OIL", 84.0),
        ):
            rows.extend({
                "date": day, "symbol": symbol,
                "open": base + number - 1, "high": base + number + 2,
                "low": base + number - 2, "close": base + number,
                "volume": 100 + number,
            } for number, day in enumerate(pd.date_range("2026-07-20", periods=20)))
    _write(data, "normalized/global_commodity_futures_daily", 2026, pd.DataFrame(rows))
    health = _health((
        "global_commodity_futures_daily", "2026-08-08", "2026-08-08",
        "EXPECTED_LAG", "READY_WITH_LIMITS", "N/A", "PIT_BLOCKED", "Yahoo",
    ))

    service = DashboardService(tmp_path)
    metrics = service.dashboard_metrics(health)
    assert all(metrics[key].displays_value for key in ("NQ_FUTURES", "GOLD", "WTI"))
    assert all(metrics[key].pit_label == "예측 사용 불가" for key in ("NQ_FUTURES", "GOLD", "WTI"))
    series = service.dashboard_series(metrics)
    assert all(len(series[key].frame) == 20 for key in ("NQ_FUTURES", "GOLD", "WTI"))
    assert list(series["NQ_FUTURES"].frame.columns) == [
        "date", "open", "high", "low", "close", "value",
    ]


def test_dashboard_health_bypass_paths_return_only_typed_fail_closed_views(tmp_path):
    service = DashboardService(tmp_path)
    risk = service._global_risk()
    assert set(risk) == {"USD/KRW", "US 10Y", "S&P 500", "NASDAQ", "Gold", "WTI", "VIX"}
    assert all(hasattr(metric, "display_state") for metric in risk.values())
    assert risk["Gold"].value is None
    assert risk["WTI"].value is None


def test_kospi_investor_flow_requires_exact_price_market_date(tmp_path):
    data = tmp_path / "data"
    _write(data, "normalized/kr_index_daily", 2026, pd.DataFrame({
        "date": [pd.Timestamp("2026-08-18")], "symbol": ["KOSPI"], "close": [3000.0], "volume": [1],
    }), "KOSPI")
    _write(data, "published/kr_market_investor_net_purchase_bridge_daily", 2026, pd.DataFrame({
        "date": [pd.Timestamp("2026-08-11")], "market": ["KOSPI"],
        "value_unit": ["KRW"], "foreign_net_purchase": [10],
        "institution_net_purchase": [20], "individual_net_purchase": [-30],
    }), "KOSPI")
    health = _health(
        ("kr_index_daily", "2026-08-18", "2026-08-18", "CURRENT", "READY", "N/A", "PIT_LIMITED", "pykrx"),
        ("kr_market_investor_net_purchase_bridge_daily", "2026-08-11", "2026-08-11", "CURRENT", "READY", "N/A", "PIT_BLOCKED", "Toss"),
    )
    service = DashboardService(tmp_path)
    kospi = service.dashboard_metrics(health)["KOSPI"]
    flows = service.kospi_investor_flow_metrics(health, kospi)
    assert all(metric.value is None for metric in flows.values())
    assert all("일치하지 않습니다" in (metric.unavailable_reason or "") for metric in flows.values())


def _market_flow_rows(market: str, dates: list[str]) -> pd.DataFrame:
    offset = 100 if market == "KOSDAQ" else 0
    return pd.DataFrame({
        "date": pd.to_datetime(dates),
        "market": market,
        "value_unit": "KRW",
        "foreign_net_purchase": [10 + offset + index * 10 for index in range(len(dates))],
        "institution_net_purchase": [-5 - offset - index for index in range(len(dates))],
        "individual_net_purchase": [-5 - index * 9 for index in range(len(dates))],
        "source_provider": "Toss Securities Open API",
        "source_operation": "getMarketIndicatorInvestorTrading",
        "provider_segment": "TOSS_2014_PRESENT",
        "availability_date": dates,
    })


def test_market_flow_views_keep_markets_units_signs_and_partial_week_separate(tmp_path):
    # 2026-08-17 is an XKRX holiday; only the two retained market sessions count.
    dates = ["2026-08-18", "2026-08-19"]
    for market in ("KOSPI", "KOSDAQ"):
        _write(
            tmp_path / "data",
            "published/kr_market_investor_net_purchase_bridge_daily",
            2026,
            _market_flow_rows(market, dates),
            market,
        )
    health = _health((
        "kr_market_investor_net_purchase_bridge_daily",
        "2026-08-19", "2026-08-19", "CURRENT", "READY", "N/A", "PIT_BLOCKED", "Toss",
    ))

    views = DashboardService(tmp_path).market_investor_flow_views(health)

    assert set(views) == {"KOSPI", "KOSDAQ"}
    assert all(isinstance(view, MarketInvestorFlowView) for view in views.values())
    assert all(view.displays_values for view in views.values())
    assert all(view.value_unit == "KRW" for view in views.values())
    assert all(view.as_of == "2026-08-19" for view in views.values())
    assert all(view.freshness == "CURRENT" and view.finality == "DAILY_FINAL" for view in views.values())
    assert all(view.partial_week and len(view.covered_sessions) == 2 for view in views.values())
    assert views["KOSPI"].values[0].latest_value == 20
    assert views["KOSPI"].values[0].week_to_date_value == 30
    assert views["KOSPI"].values[1].week_to_date_value == -11
    assert views["KOSDAQ"].values[0].week_to_date_value == 230
    assert views["KOSPI"].provider_segment == "TOSS_2014_PRESENT"
    assert views["KOSDAQ"].market == "KOSDAQ"
    gated = {
        market: DashboardService._gate_market_flow(
            view, now_utc=pd.Timestamp("2026-08-20T00:00:00Z"),
        )
        for market, view in views.items()
    }
    assert all(view.displays_values for view in gated.values())
    assert all(view.freshness == "MARKET_CLOSED_LAST_FINAL" for view in gated.values())


def test_market_flow_weekly_sum_is_suppressed_when_required_session_is_missing(tmp_path):
    dates = ["2026-08-19"]
    for market in ("KOSPI", "KOSDAQ"):
        _write(
            tmp_path / "data",
            "published/kr_market_investor_net_purchase_bridge_daily",
            2026,
            _market_flow_rows(market, dates),
            market,
        )
    health = _health((
        "kr_market_investor_net_purchase_bridge_daily",
        "2026-08-19", "2026-08-19", "CURRENT", "READY", "N/A", "PIT_BLOCKED", "Toss",
    ))

    views = DashboardService(tmp_path).market_investor_flow_views(health)

    assert all(view.displays_values for view in views.values())
    assert all(view.missing_sessions == ("2026-08-18",) for view in views.values())
    assert all(not view.weekly_complete_through_as_of for view in views.values())
    assert all(
        value.week_to_date_value is None
        for view in views.values()
        for value in view.values
    )
    assert all("누락" in (view.weekly_unavailable_reason or "") for view in views.values())


def test_dashboard_reads_contracted_treasury_spread_instead_of_recomputing(tmp_path):
    data = tmp_path / "data"
    _write(data, "normalized/fred_treasury_yield_daily", 2026, pd.DataFrame({
        "date": [pd.Timestamp("2026-08-17")], "dgs2": [4.0], "dgs10": [4.5], "dgs30": [5.0],
    }))
    _write(data, "derived/us_treasury_spread_daily", 2026, pd.DataFrame({
        "date": [pd.Timestamp("2026-08-17")], "spread_10y_2y": [0.61],
    }))
    health = _health(
        ("fred_treasury_yield_daily", "2026-08-17", "2026-08-17", "EXPECTED_LAG", "READY", "N/A", "PIT_BLOCKED", "FRED"),
        ("us_treasury_spread_daily", "2026-08-17", "2026-08-17", "EXPECTED_LAG", "READY", "N/A", "PIT_BLOCKED", "derived"),
    )
    metric = DashboardService(tmp_path).dashboard_metrics(health)["UST10_2_SPREAD"]
    assert metric.value == 0.61
    assert metric.route == "DERIVED_CONTRACT"
    assert metric.value != 4.5 - 4.0


def test_dashboard_labels_intraday_treasury_future_as_price_not_yield(tmp_path):
    now = pd.Timestamp.now(tz="UTC").floor("h")
    rows = []
    identities = (
        ("USD_KRW_60M", "GLOBAL_FX", "FOREX", "KRW=X", "Asia/Seoul"),
        ("UST2_FUTURES_60M", "CBOT", "FUTURE_CONTINUOUS", "ZT=F", "America/Chicago"),
        ("UST10_FUTURES_60M", "CBOT", "FUTURE_CONTINUOUS", "ZN=F", "America/Chicago"),
        ("UST30_FUTURES_60M", "CBOT", "FUTURE_CONTINUOUS", "ZB=F", "America/Chicago"),
    )
    for series_id, market, asset_type, provider_symbol, zone in identities:
        for offset, close in ((2, 100.0), (1, 101.0)):
            start = now - timedelta(hours=offset)
            rows.append({
                "market_date": start.tz_convert(zone).date(), "market": market,
                "symbol": series_id, "asset_type": asset_type, "bar_start": start,
                "bar_end": start + timedelta(hours=1), "timezone": zone,
                "session": "GLOBAL_CONTINUOUS", "interval": "60m",
                "actual_duration_minutes": 60, "open": close, "high": close + 1,
                "low": close - 1, "close": close, "volume": None,
                "provider": "yahoo_chart_api", "provider_symbol": provider_symbol,
                "adjustment_status": "PROVIDER_UNADJUSTED_INTRADAY_DELAYED",
                "retrieved_at": now, "fallback_used": False, "fallback_reason": None,
            })
    frame = pd.DataFrame(rows, columns=MARKET_PRICE_60M_OBSERVATION.column_names)
    frame["volume"] = frame["volume"].astype("Int64")
    write_dataset_atomic(
        frame, tmp_path / "data/normalized/market_price_60m_observation",
        MARKET_PRICE_60M_OBSERVATION, validate_market_price_60m,
    )

    service = DashboardService(tmp_path)
    metrics = service.dashboard_metrics(_health())
    future = metrics["UST10_FUTURES_60M"]
    assert future.displays_value and future.value == 101.0
    assert future.unit == "futures price"
    assert future.freshness == "60M_DELAYED"
    assert "not yield" in future.source
    assert len(service.dashboard_series(metrics)["UST10_FUTURES_60M"].frame) == 2


def test_dashboard_routes_fresh_current_treasury_futures_over_stale_60m_fallback(tmp_path):
    root = tmp_path / "data/state/current_observations/global60m_current"
    root.mkdir(parents=True)
    expected = (
        ("UST2_FUTURES_60M", "ZT=F", 102.94140625),
        ("UST10_FUTURES_60M", "ZN=F", 108.484375),
        ("UST30_FUTURES_60M", "ZB=F", 109.65625),
    )
    for coverage_id, symbol, value in expected:
        (root / f"{coverage_id.lower()}.json").write_text(json.dumps({
            "schema_version": 1,
            "observations": [{
                "route_id": f"yahoo-market-current:CBOT:{symbol}",
                "identity": {
                    "dataset_id": "MARKET_PRICE_CURRENT",
                    "market": "CBOT", "symbol": symbol,
                },
                "interval": "30m", "value": value,
                "unit": "provider native continuous futures price",
                "provider": "YAHOO", "upstream_provider": "YAHOO_CHART_API",
                "source_route": f"YAHOO_CHART_30M:{symbol}",
                "provider_timestamp_utc": "2026-08-24T17:00:00+00:00",
                "retrieved_at_utc": "2026-08-24T17:32:00+00:00",
                "finality": "AS_RETRIEVED", "display_only": True,
                "pit_safe": False, "timestamp_basis": "PROVIDER_TIMESTAMP",
            }],
            "circuits": {}, "decisions": {},
        }), encoding="utf-8")

    metrics = DashboardService(tmp_path).dashboard_metrics(
        _health(), now_utc="2026-08-24T17:30:00+00:00",
    )

    for coverage_id, symbol, value in expected:
        metric = metrics[coverage_id]
        assert metric.displays_value and metric.value == pytest.approx(value)
        assert metric.unit == "provider native continuous futures price"
        assert metric.freshness == "CURRENT_COMPLETED_30M"
        assert metric.route == f"yahoo-market-current:CBOT:{symbol}"
        assert metric.pit_status == "PIT_BLOCKED"


@pytest.mark.parametrize(
    ("source_timestamp", "now_utc", "allowed", "reason"),
    (
        ("2026-08-21T00:00:00Z", "2026-08-21T01:00:00Z", True, None),
        ("2026-08-21T00:00:00Z", "2026-08-21T01:00:01Z", False, "CURRENT_SOURCE_AGE_OVER_60M"),
        ("2026-08-20T14:59:00Z", "2026-08-20T15:01:00Z", False, "CURRENT_SOURCE_DATE_NOT_TODAY_KST"),
        ("2026-08-21T01:01:00Z", "2026-08-21T01:00:00Z", False, "CURRENT_SOURCE_TIMESTAMP_FUTURE"),
        ("2026-08-21T00:00:00", "2026-08-21T00:30:00Z", False, "CURRENT_SOURCE_TIMESTAMP_INVALID_OR_NAIVE"),
        (None, "2026-08-21T00:30:00Z", False, "CURRENT_SOURCE_TIMESTAMP_REQUIRED"),
    ),
)
def test_current_display_timestamp_gate_requires_today_kst_and_source_age_at_most_60_minutes(
    source_timestamp, now_utc, allowed, reason,
):
    decision = classify_current_display_timestamp(
        source_timestamp=source_timestamp, now_utc=now_utc,
    )

    assert decision.allow_value is allowed
    assert (reason is None) == (decision.reason is None)
    if reason is not None:
        assert reason in decision.reason


def test_current_display_timestamp_gate_accepts_only_explicit_broker_retrieval_time() -> None:
    accepted = classify_current_display_timestamp(
        source_timestamp=None,
        retrieved_at="2026-08-21T05:59:00Z",
        timestamp_basis="RETRIEVAL_TIMESTAMP",
        now_utc="2026-08-21T06:30:00Z",
    )
    implicit = classify_current_display_timestamp(
        source_timestamp=None,
        retrieved_at="2026-08-21T05:59:00Z",
        now_utc="2026-08-21T06:30:00Z",
    )
    stale = classify_current_display_timestamp(
        source_timestamp=None,
        retrieved_at="2026-08-21T05:29:59Z",
        timestamp_basis="RETRIEVAL_TIMESTAMP",
        now_utc="2026-08-21T06:30:00Z",
    )
    weekend_retrieval = classify_current_display_timestamp(
        source_timestamp=None,
        retrieved_at="2026-08-21T06:01:00Z",
        timestamp_basis="RETRIEVAL_TIMESTAMP",
        now_utc="2026-08-22T03:00:00Z",
        allow_kr_market_closed_last_verified=True,
    )

    assert accepted.allow_value
    assert accepted.freshness == "CURRENT_RETRIEVAL_TIME"
    assert "RETRIEVAL_TIMESTAMP_ACCEPTED" in (accepted.reason or "")
    assert not implicit.allow_value
    assert "CURRENT_SOURCE_TIMESTAMP_REQUIRED" in (implicit.reason or "")
    assert not stale.allow_value
    assert "CURRENT_RETRIEVAL_AGE_OVER_60M" in (stale.reason or "")
    assert not weekend_retrieval.allow_value
    assert "CURRENT_RETRIEVAL_DATE_NOT_TODAY_KST" in (weekend_retrieval.reason or "")


def test_current_coverage_uses_explicit_retrieval_basis_without_relabelling_provider_time() -> None:
    from stock_data.gui.services import CurrentObservationCoverageView

    view = CurrentObservationCoverageView(
        coverage_id="KOSPI", label="KOSPI broker snapshot", value=3000.0,
        unit="index points", provider="KB_SECURITIES",
        route="kbsec:ivu-current:XKRX:KOSPI", interval="snapshot",
        as_of="2026-08-21 14:30 KST",
        retrieved_at_utc="2026-08-21T05:30:00+00:00",
        freshness="PROVISIONAL", finality="PROVISIONAL",
        display_state=DashboardDisplayState.VALUE, unavailable_reason=None,
        provider_timestamp_utc=None,
        timestamp_basis="RETRIEVAL_TIMESTAMP",
    )

    accepted = DashboardService._gate_current_coverage(
        view, now_utc="2026-08-21T06:00:00+00:00",
    )

    assert accepted.displays_value
    assert accepted.freshness == "CURRENT_RETRIEVAL_TIME"
    assert accepted.provider_timestamp_utc is None
    assert accepted.retrieved_at_utc == "2026-08-21T05:30:00+00:00"


def test_kr_current_gate_keeps_only_latest_completed_session_on_market_holiday() -> None:
    weekend = classify_current_display_timestamp(
        source_timestamp="2026-08-21T06:01:00Z",
        now_utc="2026-08-22T03:00:00Z",
        allow_kr_market_closed_last_verified=True,
    )
    old = classify_current_display_timestamp(
        source_timestamp="2026-08-20T06:01:00Z",
        now_utc="2026-08-22T03:00:00Z",
        allow_kr_market_closed_last_verified=True,
    )
    default_gate = classify_current_display_timestamp(
        source_timestamp="2026-08-21T06:01:00Z",
        now_utc="2026-08-22T03:00:00Z",
    )
    assert weekend.allow_value
    assert weekend.freshness == "MARKET_CLOSED_LAST_VERIFIED"
    assert "KR_MARKET_CLOSED_LAST_VERIFIED" in (weekend.reason or "")
    assert not old.allow_value
    assert not default_gate.allow_value


def test_dashboard_treasury_quote_view_preserves_raw_index_and_official_daily_truth(tmp_path):
    _write_treasury_quote_15m(tmp_path)
    data = tmp_path / "data"
    _write(data, "normalized/fred_treasury_yield_daily", 2026, pd.DataFrame({
        "date": [pd.Timestamp("2026-08-17")],
        "dgs2": [4.19], "dgs10": [4.31], "dgs30": [4.89],
    }))
    _write(data, "derived/us_treasury_spread_daily", 2026, pd.DataFrame({
        "date": [pd.Timestamp("2026-08-17")], "spread_10y_2y": [0.12],
    }))
    health = _health(
        ("fred_treasury_yield_daily", "2026-08-17", "2026-08-17", "EXPECTED_LAG", "READY", "N/A", "PIT_BLOCKED", "FRED"),
        ("us_treasury_spread_daily", "2026-08-17", "2026-08-17", "EXPECTED_LAG", "READY", "N/A", "PIT_BLOCKED", "derived"),
    )
    service = DashboardService(tmp_path)
    metrics = service.dashboard_metrics(
        health, now_utc=pd.Timestamp("2026-08-20 12:00", tz="UTC"),
    )

    quote = metrics["UST10_QUOTE_15M"]
    assert not quote.displays_value and quote.value is None
    assert quote.series_id == "^TNX"
    assert quote.unit == "quote index points"
    assert quote.as_of == "2026-08-20 04:05 KST"
    assert quote.source_timestamp == "2026-08-19T19:05:00+00:00"
    assert "CURRENT_SOURCE_AGE_OVER_60M" in (quote.unavailable_reason or "")
    assert quote.expected_as_of == "2026-08-19"
    assert "not an official Treasury yield" in quote.source
    views = service.treasury_rate_views(metrics)
    assert views["UST10"].official_daily is metrics["UST10"]
    assert views["UST10"].intraday_quote is quote
    assert views["UST10"].official_data_type == "OFFICIAL_DAILY_YIELD"
    assert views["UST10"].intraday_data_type == "INDICATIVE_DELAYED_QUOTE_INDEX"
    assert views["UST5_QUOTE"].official_daily is None
    assert len(service.dashboard_series(metrics)["UST10_QUOTE_15M"].frame) == 23


def test_dashboard_treasury_quote_fails_closed_when_expected_session_is_missing(tmp_path):
    _write_treasury_quote_15m(tmp_path, missing_last_tnx=True)
    service = DashboardService(tmp_path)
    metric = service._treasury_quote_metric(
        "^TNX", "미국 10Y quote",
        now_utc=pd.Timestamp("2026-08-20 12:00", tz="UTC"),
    )
    assert metric.value is None
    assert metric.display_state is DashboardDisplayState.REFRESH_REQUIRED
    assert metric.freshness == "STALE"
    assert "23개 native bar" in (metric.unavailable_reason or "")


def test_dashboard_vix_keeps_fred_daily_primary_distinct_from_yahoo_intraday(tmp_path):
    _write_vix_15m(tmp_path)
    _write(tmp_path / "data", "normalized/fred_vix_daily", 2026, pd.DataFrame({
        "date": [pd.Timestamp("2026-08-17")], "vixcls": [15.2],
    }))
    health = _health(
        ("fred_vix_daily", "2026-08-17", "2026-08-17", "EXPECTED_LAG",
         "READY", "N/A", "PIT_LIMITED", "FRED VIXCLS"),
    )
    service = DashboardService(tmp_path)
    metrics = service.dashboard_metrics(
        health, now_utc=pd.Timestamp("2026-08-19T20:30:00Z"),
    )

    official = metrics["VIX"]
    intraday = metrics["VIX_INTRADAY_15M"]
    assert official.displays_value and official.value == pytest.approx(15.2)
    assert official.dataset_id == "fred_vix_daily"
    assert official.route == "NORMALIZED_DAILY"
    assert official.freshness == "EXPECTED_LAG"
    assert official.unavailable_reason is None
    assert intraday.displays_value and intraday.value == pytest.approx(22.5)
    assert intraday.dataset_id == "market_price_15m_observation"
    assert intraday.series_id == "^VIX"
    assert intraday.as_of == "2026-08-20 05:00 KST"
    assert intraday.source_timestamp == "2026-08-19T20:00:00+00:00"
    assert intraday.delay_status == "INDICATIVE_DELAYED_NOT_LICENSED_REALTIME"
    assert intraday.completed_bar is True
    assert "not FRED VIXCLS" in intraday.source
    source_view = service.vix_source_views(metrics)["VIX"]
    assert source_view.official_daily is official
    assert source_view.intraday_quote is intraday
    assert source_view.official_data_type == "COMPLETED_DAILY_PRIMARY"
    assert source_view.intraday_data_type == "INDICATIVE_DELAYED_PROVIDER_SUBSET_15M"
    assert len(service.dashboard_series(metrics)["VIX_INTRADAY_15M"].frame) == 26
    sparklines = service.market_card_sparklines(
        metrics, now_utc=pd.Timestamp("2026-08-20T12:00:00Z"),
    )
    assert set(sparklines) == {
        "KOSPI", "KOSDAQ", "SOXX", "NQ_FUTURES", "NASDAQ",
        "SP500", "GOLD", "VIX", "WTI",
    }
    assert isinstance(sparklines["VIX"], DashboardSparklineView)
    assert sparklines["VIX"].displays_values
    assert len(sparklines["VIX"].frame) == 26
    assert sparklines["VIX"].interval == "15m"
    assert sparklines["VIX"].lane_id == "CBOE_VIX"
    assert sparklines["VIX"].series_id == "^VIX"
    assert sparklines["VIX"].session_label == "직전 완료장 2026-08-19"
    assert sparklines["VIX"].as_of_kst == "2026-08-20 05:00 KST"
    assert sparklines["VIX"].source_timestamp == "2026-08-19T20:00:00+00:00"
    assert "24시간 아님" in sparklines["VIX"].visual_window
    assert not any(view.displays_values for key, view in sparklines.items() if key != "VIX")
    assert "새 날짜 검증 전" in (sparklines["NQ_FUTURES"].unavailable_reason or "")
    assert "roll/maintenance" in (sparklines["WTI"].unavailable_reason or "")


def test_dashboard_vix_current_headline_keeps_completed_session_sparkline_separate(tmp_path):
    _write_vix_15m(tmp_path, session_date="2026-08-21")
    _write_vix_current(tmp_path)
    service = DashboardService(tmp_path)
    now = pd.Timestamp("2026-08-24T18:20:00Z")

    metrics = service.dashboard_metrics(_health(), now_utc=now)
    current = metrics["VIX_INTRADAY_15M"]
    sparkline = service.market_card_sparklines(metrics, now_utc=now)["VIX"]

    assert current.displays_value and current.value == pytest.approx(15.81)
    assert current.dataset_id == "market_price_15m_current"
    assert current.route == "yahoo-market-current:CBOE:VIX"
    assert current.freshness == "CURRENT_COMPLETED_15M"
    assert current.as_of == "2026-08-25 03:00 KST"
    assert current.completed_bar is True and current.pit_status == "PIT_BLOCKED"
    assert sparkline.displays_values and len(sparkline.frame) == 26
    assert sparkline.session_date == "2026-08-21"
    assert sparkline.source_timestamp == "2026-08-21T20:00:00+00:00"
    assert sparkline.frame["value"].iloc[-1] == pytest.approx(22.5)

    after_close = service._gate_current_metric(
        current, now_utc=pd.Timestamp("2026-08-24T21:30:00Z"),
    )
    assert not after_close.displays_value
    assert after_close.display_state is DashboardDisplayState.REFRESH_REQUIRED
    assert after_close.freshness == "CURRENT_GATE_BLOCKED"
    assert "CURRENT_SOURCE_AGE_OVER_60M" in (after_close.unavailable_reason or "")
    assert sparkline.displays_values and len(sparkline.frame) == 26


@pytest.mark.parametrize(
    ("session_date", "now_utc", "expected_label", "expected_as_of"),
    (
        ("2026-08-19", "2026-08-19T21:00:00Z", "완료장 2026-08-19", "2026-08-20 05:00 KST"),
        ("2026-08-19", "2026-08-20T14:00:00Z", "직전 완료장 2026-08-19", "2026-08-20 05:00 KST"),
        ("2026-08-21", "2026-08-22T12:00:00Z", "직전 완료장 2026-08-21", "2026-08-22 05:00 KST"),
        ("2026-01-15", "2026-01-16T18:00:00Z", "직전 완료장 2026-01-15", "2026-01-16 06:00 KST"),
    ),
)
def test_vix_card_sparkline_labels_completed_current_preopen_weekend_and_dst(
    tmp_path, session_date, now_utc, expected_label, expected_as_of,
):
    _write_vix_15m(tmp_path, session_date=session_date)
    service = DashboardService(tmp_path)
    now = pd.Timestamp(now_utc)
    metric = service._vix_intraday_metric(now_utc=now)
    view = service.market_card_sparklines(
        {"VIX_INTRADAY_15M": metric}, now_utc=now,
    )["VIX"]
    assert view.displays_values
    assert view.session_label == expected_label
    assert view.as_of_kst == expected_as_of
    assert pd.to_datetime(view.frame["date"], utc=True).is_monotonic_increasing
    assert pd.to_datetime(view.frame["date"], utc=True).diff().dropna().eq(
        timedelta(minutes=15)
    ).all()


def test_vix_card_sparkline_rejects_missing_bar_and_early_close_grid_mismatch(tmp_path):
    _write_vix_15m(tmp_path)
    service = DashboardService(tmp_path)
    now = pd.Timestamp("2026-08-20T12:00:00Z")
    metric = service._vix_intraday_metric(now_utc=now)
    parquet = next(
        (tmp_path / "data/normalized/market_price_15m_observation").rglob("*.parquet")
    )
    frame = pd.read_parquet(parquet).iloc[:-1]
    frame.to_parquet(parquet, index=False)
    missing = service.market_card_sparklines(
        {"VIX_INTRADAY_15M": metric}, now_utc=now,
    )["VIX"]
    assert not missing.displays_values and missing.frame.empty

    early_root = tmp_path / "early"
    _write_vix_15m(early_root, session_date="2026-11-27")
    early_service = DashboardService(early_root)
    early_now = pd.Timestamp("2026-11-27T19:00:00Z")
    early_metric = early_service._vix_intraday_metric(now_utc=early_now)
    early = early_service.market_card_sparklines(
        {"VIX_INTRADAY_15M": early_metric}, now_utc=early_now,
    )["VIX"]
    assert not early.displays_values
    assert early_metric.value is None


def test_dashboard_vix_intraday_stale_session_is_numeric_free(tmp_path):
    _write_vix_15m(tmp_path, session_date="2026-08-18")
    metric = DashboardService(tmp_path)._vix_intraday_metric(
        now_utc=pd.Timestamp("2026-08-20T12:00:00Z"),
    )
    assert metric.value is None
    assert metric.change is None and metric.change_pct is None
    assert metric.freshness == "STALE"
    assert metric.display_state is DashboardDisplayState.REFRESH_REQUIRED
    assert metric.expected_as_of == "2026-08-19"
    assert metric.source_timestamp == "2026-08-18T20:00:00+00:00"
    assert metric.completed_bar is True


def test_dashboard_vix_intraday_missing_checkpoint_is_numeric_free(tmp_path):
    _write_vix_15m(tmp_path, checkpoint=False)
    metric = DashboardService(tmp_path)._vix_intraday_metric(
        now_utc=pd.Timestamp("2026-08-20T12:00:00Z"),
    )
    assert metric.value is None
    assert metric.freshness == "UNKNOWN"
    assert metric.display_state is DashboardDisplayState.UNAVAILABLE
    assert metric.source_timestamp is None
    assert metric.completed_bar is None


def test_dashboard_vix_intraday_rejects_live_forming_local_row(tmp_path):
    _write_vix_15m(tmp_path)
    parquet = next(
        (tmp_path / "data/normalized/market_price_15m_observation").rglob("*.parquet")
    )
    frame = pd.read_parquet(parquet)
    frame.loc[frame.index[-1], "retrieved_at"] = frame.loc[frame.index[-1], "bar_start"]
    frame.to_parquet(parquet, index=False)

    metric = DashboardService(tmp_path)._vix_intraday_metric(
        now_utc=pd.Timestamp("2026-08-20T12:00:00Z"),
    )
    assert metric.value is None
    assert metric.freshness == "UNKNOWN"
    assert metric.display_state is DashboardDisplayState.UNAVAILABLE
    assert metric.completed_bar is None


@pytest.mark.parametrize(
    ("bar_end", "now_utc", "allowed", "freshness", "reason_fragment"),
    (
        (
            "2026-08-21T21:00:00Z", "2026-08-22T12:00:00Z", True,
            "MARKET_CLOSED_LAST_FINAL", "주말 휴장",
        ),
        (
            "2026-08-24T10:00:00Z", "2026-08-24T15:00:01Z", False,
            "STALE_OR_MISSING", "4시간 이상",
        ),
        (
            "2026-08-24T16:00:00Z", "2026-08-24T15:00:00Z", False,
            "STALE_OR_MISSING", "미래",
        ),
        (
            "2026-08-14T21:00:00Z", "2026-08-22T12:00:00Z", False,
            "STALE_OR_MISSING", "4시간 이상",
        ),
    ),
)
def test_intraday_freshness_policy_is_fail_closed_outside_reviewed_weekly_closure(
    bar_end, now_utc, allowed, freshness, reason_fragment,
):
    decision = classify_intraday_60m_freshness(
        bar_end=pd.Timestamp(bar_end), now_utc=pd.Timestamp(now_utc),
    )
    assert decision.allow_value is allowed
    assert decision.freshness == freshness
    assert reason_fragment in (decision.reason or "")


def test_intraday_metric_keeps_friday_final_bar_during_reviewed_weekend_closure(tmp_path):
    service = DashboardService(tmp_path)
    service._read_intraday_frame = lambda _series_id: pd.DataFrame({
        "bar_start": pd.to_datetime([
            "2026-08-21T19:00:00Z", "2026-08-21T20:00:00Z",
        ]),
        "bar_end": pd.to_datetime([
            "2026-08-21T20:00:00Z", "2026-08-21T21:00:00Z",
        ]),
        "close": [100.0, 101.0],
    })

    metric = service._intraday_metric(
        "UST10_FUTURES_60M", "미국 10Y 선물 60M", "futures price",
        now_utc=pd.Timestamp("2026-08-22T12:00:00Z"),
    )

    assert metric.displays_value
    assert metric.value == 101.0
    assert metric.freshness == "MARKET_CLOSED_LAST_FINAL"
    assert "공휴일 인식 정책은 아닙니다" in metric.source


def test_dashboard_keeps_kospi200_volume_and_oi_pcr_distinct(tmp_path):
    data = tmp_path / "data"
    _write(data, "normalized/kr_kospi200_index_daily", 2026, pd.DataFrame({
        "date": [pd.Timestamp("2026-08-18")], "close": [650.0],
    }))
    _write(data, "derived/kr_kospi200_option_pcr_daily", 2026, pd.DataFrame({
        "date": [pd.Timestamp("2026-08-18")],
        "volume_pcr": [1.0964197868],
        "open_interest_pcr": [1.6364200504],
        "observation_status": ["observed"],
        "source": ["data_go_kr"],
    }))

    metrics = DashboardService(tmp_path).dashboard_metrics(_health((
        "kr_kospi200_option_pcr_daily", "2026-08-18", "2026-08-18",
        "CURRENT", "READY_WITH_FINALITY_GATE", "N/A", "PIT_LIMITED",
        "data.go.kr exact-date KOSPI200 options",
    )))

    assert metrics["VOLUME_PCR"].value == pytest.approx(1.0964197868)
    assert metrics["OI_PCR"].value == pytest.approx(1.6364200504)
    assert metrics["VOLUME_PCR"].label == "KOSPI200 옵션 거래량 P/C"
    assert metrics["OI_PCR"].label == "KOSPI200 옵션 OI P/C"
    assert metrics["VOLUME_PCR"].automation_policy == "DEPENDENCY_DRIVEN"
    assert metrics["VOLUME_PCR"].automation_enabled is True
    assert metrics["OI_PCR"].automation_policy == "DEPENDENCY_DRIVEN"
    assert metrics["OI_PCR"].automation_enabled is True
    assert "PRICE_PCR" not in metrics
    assert metrics["US_OPTION_PCR"].value is None
    assert metrics["US_OPTION_PCR"].display_state is DashboardDisplayState.PROHIBITED
    reason = metrics["US_OPTION_PCR"].unavailable_reason or ""
    assert "ORATS contract-only" in reason
    assert "root 범위" in reason


def test_dashboard_derivatives_use_health_t_plus_1_expected_date_after_midnight(
    tmp_path,
):
    data = tmp_path / "data"
    _write(data, "normalized/kr_kospi200_index_daily", 2026, pd.DataFrame({
        "date": [pd.Timestamp("2026-08-26")], "close": [650.0],
    }))
    _write(data, "derived/kr_kospi200_option_pcr_daily", 2026, pd.DataFrame({
        "date": [pd.Timestamp("2026-08-25")],
        "volume_pcr": [1.25],
        "open_interest_pcr": [1.5],
        "observation_status": ["observed"],
        "source": ["data_go_kr"],
    }))
    health = _health((
        "kr_kospi200_option_pcr_daily", "2026-08-25", "2026-08-25",
        "EXPECTED_LAG", "READY_WITH_FINALITY_GATE", "N/A", "PIT_LIMITED",
        "data.go.kr exact-date KOSPI200 options",
    ))

    metrics = DashboardService(tmp_path).dashboard_metrics(health)

    assert metrics["VOLUME_PCR"].value == 1.25
    assert metrics["OI_PCR"].value == 1.5
    assert metrics["VOLUME_PCR"].as_of == "2026-08-25"
    assert metrics["VOLUME_PCR"].expected_as_of == "2026-08-25"
    assert metrics["VOLUME_PCR"].freshness == "EXPECTED_LAG"
    assert metrics["VOLUME_PCR"].display_state is DashboardDisplayState.VALUE


def test_dashboard_automated_derivatives_fail_closed_without_health_expected_date(
    tmp_path, monkeypatch,
):
    service = DashboardService(tmp_path)
    monkeypatch.setattr(
        service, "_read_volume_pcr_metric",
        lambda: (1.25, "2026-08-26", "data_go_kr"),
    )
    monkeypatch.setattr(
        service, "_read_oi_pcr_metric",
        lambda: (1.5, "2026-08-26", "data_go_kr"),
    )

    metrics = service.dashboard_metrics(_health())

    for series_id in ("VOLUME_PCR", "OI_PCR"):
        metric = metrics[series_id]
        assert metric.value is None
        assert metric.expected_as_of is None
        assert metric.display_state is DashboardDisplayState.UNAVAILABLE
        assert metric.unavailable_reason == (
            "Health V2의 완료 거래일을 확인할 수 없어 자동 관리 파생지표를 "
            "표시할 수 없습니다."
        )


def test_missing_and_stale_automated_option_walls_are_numeric_free_verification_gates(
    tmp_path,
):
    service = DashboardService(tmp_path)
    service._expected_derivative_date = lambda: "2026-08-24"

    for series_id in ("CALL_WALL", "PUT_WALL"):
        metric = service._local_derivative_metric(
            series_id, series_id, "kr_kospi200_option_walls_daily", "points",
            lambda: (None, None, "local persisted data"),
            automation_policy="DEPENDENCY_DRIVEN", automation_enabled=True,
        )

        assert metric.value is None
        assert metric.freshness == "STALE_OR_MISSING"
        assert metric.display_state is DashboardDisplayState.UNAVAILABLE
        assert metric.automation_policy == "DEPENDENCY_DRIVEN"
        assert metric.automation_enabled is True
        assert metric.unavailable_reason == (
            "완료 거래일 2026-08-24와 파생 기준일 N/A가 일치하지 않습니다."
        )

        stale = service._local_derivative_metric(
            series_id, series_id, "kr_kospi200_option_walls_daily", "points",
            lambda: (1597.5, "2026-08-19", "local persisted data"),
            automation_policy="DEPENDENCY_DRIVEN", automation_enabled=True,
        )
        assert stale.value is None
        assert stale.as_of == "2026-08-19"
        assert stale.expected_as_of == "2026-08-24"
        assert stale.freshness == "STALE"
        assert stale.display_state is DashboardDisplayState.UNAVAILABLE
        assert stale.automation_policy == "DEPENDENCY_DRIVEN"
        assert stale.automation_enabled is True
        assert stale.unavailable_reason == (
            "최근 검증 장마감 2026-08-19; 완료 거래일 2026-08-24 데이터는 아직 "
            "없으며 20:30 KST 자동 복구 대상이며 기준일이 일치하기 전에는 "
            "표시할 수 없습니다."
        )

        current = service._local_derivative_metric(
            series_id, series_id, "kr_kospi200_option_walls_daily", "points",
            lambda: (1597.5, "2026-08-24", "local persisted data"),
            automation_policy="DEPENDENCY_DRIVEN", automation_enabled=True,
        )
        assert current.value == 1597.5
        assert current.display_state is DashboardDisplayState.VALUE
        assert current.automation_policy == "DEPENDENCY_DRIVEN"
        assert current.automation_enabled is True


def test_dashboard_automates_price_derivatives_but_keeps_ls_t8462_manual(
    tmp_path, monkeypatch,
):
    service = DashboardService(tmp_path)
    monkeypatch.setattr(service, "_expected_derivative_date", lambda: "2026-08-24")
    monkeypatch.setattr(
        service, "_read_basis_metric",
        lambda: (1.25, "2026-08-24", "data_go_kr"),
    )
    monkeypatch.setattr(
        service, "_read_volume_pcr_metric",
        lambda: (1.1, "2026-08-24", "data_go_kr"),
    )
    monkeypatch.setattr(
        service, "_read_oi_pcr_metric",
        lambda: (1.2, "2026-08-24", "data_go_kr"),
    )
    monkeypatch.setattr(
        service, "_read_wall_metric",
        lambda side: (1600.0 if side == "call" else 700.0, "2026-08-24", "data_go_kr"),
    )
    monkeypatch.setattr(
        service, "_read_ls_futures_foreign_net_metric",
        lambda: (25.0, "2026-08-24", "LS OpenAPI t8462"),
    )

    metrics = service.dashboard_metrics(_health(
        (
            "kr_kospi200_futures_nearest_listed_daily", "2026-08-24",
            "2026-08-24", "CURRENT", "READY_WITH_FINALITY_GATE", "N/A",
            "PIT_LIMITED", "data.go.kr exact-date KOSPI200 futures",
        ),
        (
            "kr_kospi200_option_pcr_daily", "2026-08-24", "2026-08-24",
            "CURRENT", "READY_WITH_FINALITY_GATE", "N/A", "PIT_LIMITED",
            "data.go.kr exact-date KOSPI200 options",
        ),
        (
            "kr_kospi200_option_walls_daily", "2026-08-24", "2026-08-24",
            "CURRENT", "READY_WITH_FINALITY_GATE", "N/A", "PIT_LIMITED",
            "data.go.kr exact-date KOSPI200 option walls",
        ),
    ))

    for series_id in (
        "KOSPI200_BASIS", "VOLUME_PCR", "OI_PCR", "CALL_WALL", "PUT_WALL",
    ):
        metric = metrics[series_id]
        assert metric.display_state is DashboardDisplayState.VALUE
        assert metric.automation_policy == "DEPENDENCY_DRIVEN"
        assert metric.automation_enabled is True

    ls_metric = metrics["LS_FUTURES_FOREIGN_NET"]
    assert ls_metric.display_state is DashboardDisplayState.VALUE
    assert ls_metric.automation_policy == "MANUAL_BOUNDED"
    assert ls_metric.automation_enabled is False


def _write_kospi200_breadth(root: Path, *, date: str = "2026-08-12", scope_status: str = "COMPLETE_EXACT_DATE") -> None:
    frame = pd.DataFrame({
        "date": [date], "membership_observation_date": [date],
        "previous_session_date": ["2026-08-11"], "index_symbol": ["KOSPI200"],
        "index_ticker": ["1028"], "advancing": [81], "declining": [111],
        "unchanged": [8], "total": [200], "missing_price_count": [0],
        "scope_status": [scope_status],
    })
    write_dataset_atomic(
        frame, root / "data/derived/kr_kospi200_breadth_daily",
        KR_KOSPI200_BREADTH_DAILY, validate_kospi200_breadth_daily,
    )


def test_dashboard_kospi200_breadth_is_health_gated_exact_date_only(tmp_path):
    _write_kospi200_breadth(tmp_path)
    health = _health((
        "kr_kospi200_breadth_daily", "2026-08-12", "2026-08-12",
        "CURRENT", "MANUAL_READY", "N/A", "PIT_SAFE_EOD_T_PLUS_1", "retained exact scope",
    ))

    metrics = DashboardService(tmp_path).dashboard_metrics(health)

    assert [metrics[key].value for key in (
        "KOSPI200_ADVANCING", "KOSPI200_DECLINING", "KOSPI200_UNCHANGED",
    )] == [81.0, 111.0, 8.0]
    assert {metrics[key].dataset_id for key in (
        "KOSPI200_ADVANCING", "KOSPI200_DECLINING", "KOSPI200_UNCHANGED",
    )} == {"kr_kospi200_breadth_daily"}
    assert {metrics[key].as_of for key in (
        "KOSPI200_ADVANCING", "KOSPI200_DECLINING", "KOSPI200_UNCHANGED",
    )} == {"2026-08-12"}
    assert all(metrics[key].route == "DERIVED_EXACT_DATE_CONTRACT" for key in (
        "KOSPI200_ADVANCING", "KOSPI200_DECLINING", "KOSPI200_UNCHANGED",
    ))


def test_dashboard_kospi200_breadth_selects_latest_retained_exact_date(tmp_path):
    frame = pd.DataFrame({
        "date": ["2026-08-12", "2026-08-25"],
        "membership_observation_date": ["2026-08-12", "2026-08-25"],
        "previous_session_date": ["2026-08-11", "2026-08-24"],
        "index_symbol": ["KOSPI200", "KOSPI200"],
        "index_ticker": ["1028", "1028"],
        "advancing": [81, 127], "declining": [111, 66],
        "unchanged": [8, 7], "total": [200, 200],
        "missing_price_count": [0, 0],
        "scope_status": ["COMPLETE_EXACT_DATE", "COMPLETE_EXACT_DATE"],
    })
    write_dataset_atomic(
        frame, tmp_path / "data/derived/kr_kospi200_breadth_daily",
        KR_KOSPI200_BREADTH_DAILY, validate_kospi200_breadth_daily,
    )
    health = _health((
        "kr_kospi200_breadth_daily", "2026-08-25", "2026-08-25",
        "EXPECTED_LAG", "READY_WITH_FINALITY_GATE", "N/A",
        "PIT_SAFE_EOD_T_PLUS_1", "exact-date automated scope",
    ))

    metrics = DashboardService(tmp_path).dashboard_metrics(health)

    assert [metrics[key].value for key in (
        "KOSPI200_ADVANCING", "KOSPI200_DECLINING", "KOSPI200_UNCHANGED",
    )] == [127.0, 66.0, 7.0]
    assert {metrics[key].as_of for key in (
        "KOSPI200_ADVANCING", "KOSPI200_DECLINING", "KOSPI200_UNCHANGED",
    )} == {"2026-08-25"}


def test_dashboard_reads_unified_yahoo_treasury_current_projection(tmp_path):
    path = (
        tmp_path / "data/state/current_observations/yahoo_native15m_current/idxtnx.json"
    )
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "schema_version": 1,
        "observations": [{
            "route_id": "yahoo-market-current:CBOE:TNX",
            "identity": {
                "dataset_id": "MARKET_PRICE_CURRENT", "market": "CBOE", "symbol": "^TNX",
            },
            "interval": "15m", "value": 4.738,
            "unit": "provider native quote index points",
            "provider": "YAHOO", "upstream_provider": "YAHOO_CHART_API",
            "source_route": "YAHOO_CHART_15M:^TNX",
            "provider_timestamp_utc": "2026-08-21T19:05:00+00:00",
            "retrieved_at_utc": "2026-08-22T09:02:00+00:00",
            "finality": "AS_RETRIEVED", "display_only": True, "pit_safe": False,
            "timestamp_basis": "PROVIDER_TIMESTAMP",
        }],
        "circuits": {}, "decisions": {},
    }), encoding="utf-8")

    metric = DashboardService(tmp_path).dashboard_metrics(
        _health(), now_utc="2026-08-22T12:00:00+00:00",
    )["UST10_QUOTE_15M"]

    assert metric.displays_value and metric.value == pytest.approx(4.738)
    assert metric.route == "yahoo-market-current:CBOE:TNX"
    assert metric.freshness == "MARKET_CLOSED_LAST_VERIFIED"
    assert metric.completed_bar is True
    assert "not an official Treasury yield" in metric.source


def test_market_funding_view_keeps_stale_retained_values_and_missing_credit_explicit(tmp_path):
    _write(tmp_path / "data", "normalized/kr_market_liquidity_daily", 2026, pd.DataFrame({
        "date": [pd.Timestamp("2026-08-06")],
        "investor_deposits": [62_000_000_000_000],
        "brokerage_receivables": [540_000_000_000],
        "forced_sale_amount": [8_000_000_000],
    }))
    health = _health(
        (
            "kr_market_liquidity_daily", "2026-08-06", "2026-08-21", "STALE",
            "IMPLEMENTATION_READY", "N/A", "PIT_BLOCKED", "data.go.kr/KOFIA",
        ),
        (
            "kr_credit_balance_daily", "N/A", "2026-08-21", "UNKNOWN",
            "IMPLEMENTATION_READY", "N/A", "PIT_BLOCKED", "data.go.kr/KOFIA",
        ),
    )

    view = DashboardService(tmp_path).market_funding_view(health)
    values = {item.value_id: item for item in view.values}

    assert values["INVESTOR_DEPOSITS"].value == 62_000_000_000_000
    assert values["INVESTOR_DEPOSITS"].as_of == "2026-08-06"
    assert values["INVESTOR_DEPOSITS"].freshness == "STALE"
    assert values["CREDIT_FINANCING"].value is None
    assert values["CREDIT_FINANCING"].unavailable_reason


def test_derivative_summary_exposes_same_date_official_short_selling_amount(tmp_path):
    for market, amounts in (
        ("KOSPI", (100_000_000, 200_000_000)),
        ("KOSDAQ", (30_000_000, 70_000_000)),
    ):
        _write(
            tmp_path / "data", "normalized/kr_short_selling_trading_daily", 2026,
            pd.DataFrame({
                "date": [pd.Timestamp("2026-08-21")] * 2,
                "market": [market] * 2,
                "short_trading_value": list(amounts),
            }),
            market,
        )
    health = _health((
        "kr_short_selling_trading_daily", "2026-08-21", "2026-08-21", "CURRENT",
        "READY_WITH_FINALITY_GATE", "N/A", "PIT_LIMITED", "data.go.kr/KRX",
    ))

    metric = DashboardService(tmp_path).dashboard_metrics(
        health, now_utc="2026-08-22T12:00:00+00:00",
    )["SHORT_SELLING_VALUE"]

    assert metric.displays_value
    assert metric.value == 400_000_000
    assert metric.as_of == "2026-08-21"
    assert metric.route == "OFFICIAL_DAILY_MARKET_AGGREGATE"


@pytest.mark.parametrize("freshness, state", [
    ("STALE", DashboardDisplayState.REFRESH_REQUIRED),
    ("UNKNOWN", DashboardDisplayState.UNAVAILABLE),
])
def test_dashboard_kospi200_breadth_hides_counts_when_health_is_not_displayable(tmp_path, freshness, state):
    _write_kospi200_breadth(tmp_path)
    health = _health((
        "kr_kospi200_breadth_daily", "2026-08-12", "2026-08-13",
        freshness, "MANUAL_READY", "N/A", "PIT_SAFE_EOD_T_PLUS_1", "retained exact scope",
    ))

    metrics = DashboardService(tmp_path).dashboard_metrics(health)

    assert all(metrics[key].value is None and metrics[key].display_state is state for key in (
        "KOSPI200_ADVANCING", "KOSPI200_DECLINING", "KOSPI200_UNCHANGED",
    ))


def test_dashboard_kospi200_breadth_fails_closed_on_invalid_scope_contract(tmp_path):
    _write_kospi200_breadth(tmp_path)
    path = tmp_path / "data/derived/kr_kospi200_breadth_daily/year=2026/data.parquet"
    frame = pd.read_parquet(path)
    frame.loc[0, "index_ticker"] = "9999"
    frame.to_parquet(path, index=False)
    health = _health((
        "kr_kospi200_breadth_daily", "2026-08-12", "2026-08-12",
        "CURRENT", "MANUAL_READY", "N/A", "PIT_SAFE_EOD_T_PLUS_1", "retained exact scope",
    ))

    metrics = DashboardService(tmp_path).dashboard_metrics(health)

    assert all(metrics[key].value is None for key in (
        "KOSPI200_ADVANCING", "KOSPI200_DECLINING", "KOSPI200_UNCHANGED",
    ))
    assert all("계약 오류" in (metrics[key].unavailable_reason or "") for key in (
        "KOSPI200_ADVANCING", "KOSPI200_DECLINING", "KOSPI200_UNCHANGED",
    ))


def test_malformed_commodity_landing_fails_soft_without_dashboard_crash(tmp_path):
    call = tmp_path / "data/landing/yahoo/global_commodity_futures_daily/run/call.json"
    call.parent.mkdir(parents=True)
    call.write_text("{not-json", encoding="utf-8")

    commodity = DashboardService(tmp_path)._commodity_raw_latest("GOLD")

    assert commodity["value"] is None
    assert commodity["status"] == "RAW_MISSING"


def test_short_selling_exact_date_never_falls_back(tmp_path):
    data = tmp_path / "data"
    _write(data, "normalized/kr_short_selling_trading_daily", 2026, _official("2026-08-07"), "KOSPI")
    _write_ls_t1716(tmp_path, "2026-08-07")
    view = MarketMicrostructureService(tmp_path, LocalParquetQuery(data)).short_selling(market_date="2026-08-08")
    assert view["official"] is None
    assert view["provider"] is None
    assert view["inferred_additional_venue"] is None
def test_program_summary_never_falls_back_to_retained_raw(tmp_path):
    raw = tmp_path / "data/landing/ls/t1633_program_trading_raw/run/response.json"
    raw.parent.mkdir(parents=True)
    raw.write_text(json.dumps({"t1633OutBlock1": [{"date": "20260819", "tot3": "20"}]}), encoding="utf-8")
    result = MarketMicrostructureService(tmp_path, LocalParquetQuery(tmp_path / "data")).program(
        expected_date="2026-08-19", finality_accepted=True,
    )
    assert result == {"status": "NOT_AVAILABLE", "reason": "NORMALIZED_DATASET_MISSING"}


def test_program_summary_hides_stale_and_unaccepted_finality(tmp_path):
    _write_ls_t1633_normalized(tmp_path)
    service = MarketMicrostructureService(tmp_path, LocalParquetQuery(tmp_path / "data"))
    stale = service.program(expected_date="2026-08-20", finality_accepted=True)
    blocked = service.program(expected_date="2026-08-19", finality_accepted=False)
    assert stale["status"] == "REFRESH_REQUIRED" and "markets" not in stale
    assert blocked["status"] == "NOT_AVAILABLE" and "markets" not in blocked
    assert blocked["reason"] == "PUBLICATION_AND_REVISION_FINALITY_REQUIRED"


def test_program_summary_exposes_both_markets_only_after_explicit_gates(tmp_path):
    _write_ls_t1633_normalized(tmp_path)
    result = MarketMicrostructureService(tmp_path, LocalParquetQuery(tmp_path / "data")).program(
        expected_date="2026-08-19", finality_accepted=True,
    )
    assert result["status"] == "CURRENT"
    assert set(result["markets"]) == {"KOSPI", "KOSDAQ"}
    assert result["markets"]["KOSPI"] == {
        "total_net_amount_krw": 20_000_000,
        "arbitrage_net_amount_krw": -5_000_000,
        "non_arbitrage_net_amount_krw": 25_000_000,
    }


def _average_metric(
    value: float | None = 119.0,
    *,
    freshness: str = "CURRENT",
    state: DashboardDisplayState = DashboardDisplayState.VALUE,
    unit: str = "index points",
) -> DashboardMetricView:
    return DashboardMetricView(
        dataset_id="fixture_daily", series_id="FIXTURE", label="fixture",
        value=value, unit=unit, as_of="2026-08-20",
        expected_as_of="2026-08-20", source="fixture completed daily",
        freshness=freshness, pit_status="PIT_LIMITED", pit_label="설명용",
        automation_policy="MANUAL", automation_enabled=False,
        display_state=state,
        unavailable_reason=(None if value is not None else "fixture unavailable"),
        route="NORMALIZED_DAILY",
    )


def test_completed_daily_average_comparison_reconciles_exact_price_windows():
    frame = pd.DataFrame({
        "date": pd.date_range("2026-08-01", periods=20, freq="D"),
        "value": [float(value) for value in range(100, 120)],
    })

    view = DashboardService.build_daily_average_comparison(
        _average_metric(), frame, comparison_kind="relative_percent",
    )

    assert view.mean_5 == 117.0
    assert view.comparison_5 == pytest.approx((119.0 / 117.0 - 1.0) * 100.0)
    assert view.coverage_5 == ("2026-08-16", "2026-08-20", 5)
    assert view.mean_20 == 109.5
    assert view.comparison_20 == pytest.approx((119.0 / 109.5 - 1.0) * 100.0)
    assert view.coverage_20 == ("2026-08-01", "2026-08-20", 20)


@pytest.mark.parametrize("interval", ["30m", "60m"])
def test_current_card_session_sparkline_accepts_supported_completed_intervals(
    tmp_path, interval,
):
    root = tmp_path / "data/state/current_observations/global60m_current"
    root.mkdir(parents=True)
    payload = {
        "schema_version": 1,
        "series_id": "KOSPI_CURRENT_60M",
        "provider_symbol": "^KS11",
        "interval": interval,
        "session_date": "2026-08-20",
        "session_semantics": "CASH_REGULAR",
        "session_start_local": "09:00",
        "session_end_local": "15:30",
        "source_timezone": "Asia/Seoul",
        "completed_bars_only": True,
        "points": [
            {"bar_end_utc": "2026-08-20T05:30:00+00:00", "value": 118.0},
            {"bar_end_utc": "2026-08-20T06:00:00+00:00", "value": 119.0},
        ],
    }
    (root / "kospi_current_60m.session.json").write_text(
        json.dumps(payload), encoding="utf-8",
    )
    metric = replace(
        _average_metric(), series_id="KOSPI", as_of="2026-08-20",
        freshness="MARKET_CLOSED_LAST_FINAL",
    )

    view = DashboardService(tmp_path).current_session_card_sparklines(
        {"KOSPI": metric}
    )["KOSPI"]

    assert view.displays_values
    assert view.interval == interval
    assert view.frame["value"].tolist() == [118.0, 119.0]


def test_yield_average_comparison_uses_absolute_basis_points_with_sign():
    values = [4.00 + number / 100 for number in range(20)]
    frame = pd.DataFrame({
        "date": pd.date_range("2026-08-01", periods=20, freq="D"),
        "value": values,
    })

    view = DashboardService.build_daily_average_comparison(
        _average_metric(4.19, unit="percent"), frame,
        comparison_kind="basis_points",
    )

    assert view.mean_5 == pytest.approx(4.17)
    assert view.comparison_5 == pytest.approx(2.0)
    assert view.mean_20 == pytest.approx(4.095)
    assert view.comparison_20 == pytest.approx(9.5)
    assert view.comparison_kind == "basis_points"


def test_completed_daily_average_accepts_verified_market_close_and_keeps_daily_basis():
    frame = pd.DataFrame({
        "date": pd.date_range("2026-08-01", periods=20, freq="D"),
        "value": [float(value) for value in range(100, 120)],
    })
    metric = _average_metric(
        999.0,
        freshness="MARKET_CLOSED_LAST_FINAL",
        state=DashboardDisplayState.VALUE,
    )
    metric = replace(metric, route="yahoo-market-current:XNAS:TEST")

    view = DashboardService.build_daily_average_comparison(
        metric, frame, comparison_kind="relative_percent",
        require_metric_match=False,
    )

    assert view.as_of == "2026-08-20"
    assert view.latest_value == 119.0
    assert view.comparison_5 == pytest.approx((119.0 / 117.0 - 1.0) * 100.0)


@pytest.mark.parametrize(
    ("symbol", "headline"),
    (("KOSPI", 2810.25), ("KOSDAQ", 901.75)),
)
def test_toss_clock_headline_daily_average_uses_typed_source_date_without_parsing_label(
    tmp_path, symbol, headline,
):
    daily = pd.DataFrame({
        "date": pd.bdate_range(end="2026-08-25", periods=20),
        "value": [800.0 + offset for offset in range(20)],
    })
    metric = replace(
        _average_metric(headline),
        dataset_id="TOSS_MARKET_PRICE_SNAPSHOT",
        series_id=symbol,
        as_of="08-26 10:00 KST",
        route=_toss_domestic_ur246_route(symbol).route_id,
        source_timestamp="2026-08-26T01:00:00+00:00",
        retrieved_at_utc="2026-08-26T01:00:00+00:00",
        timestamp_basis="RETRIEVAL_TIMESTAMP",
    )
    series = {symbol: DashboardSeriesView(metric, daily)}

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        view = DashboardService(tmp_path).daily_average_comparisons(
            {symbol: metric}, series,
        )[symbol]

    assert not captured
    assert view.displays_5 and view.displays_20
    assert view.as_of == "2026-08-25"
    assert view.latest_value == pytest.approx(819.0)
    assert view.latest_value != headline
    assert view.comparison_5 == pytest.approx((819.0 / 817.0 - 1.0) * 100.0)
    assert headline not in daily["value"].tolist()


@pytest.mark.parametrize(
    ("as_of", "source_timestamp"),
    (
        ("malformed KST label", "2026-08-26T01:00:00+00:00"),
        ("08-26 10:00 KST", None),
        ("08-24 10:00 KST", "2026-08-24T01:00:00+00:00"),
    ),
)
def test_toss_daily_average_malformed_or_older_typed_boundary_is_numeric_free(
    tmp_path, monkeypatch, as_of, source_timestamp,
):
    daily = pd.DataFrame({
        "date": pd.bdate_range(end="2026-08-25", periods=20),
        "value": [800.0 + offset for offset in range(20)],
    })
    metric = replace(
        _average_metric(2810.25),
        dataset_id="TOSS_MARKET_PRICE_SNAPSHOT",
        series_id="KOSPI",
        as_of=as_of,
        route=_toss_domestic_ur246_route("KOSPI").route_id,
        source_timestamp=source_timestamp,
        retrieved_at_utc=source_timestamp,
        timestamp_basis="RETRIEVAL_TIMESTAMP",
    )

    service = DashboardService(tmp_path)
    raw_daily = daily.rename(columns={"value": "close"}).assign(symbol="KOSPI")
    monkeypatch.setattr(
        service.index, "asset_series", lambda *_args, **_kwargs: raw_daily.copy(),
    )
    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        graph = service.dashboard_series({"KOSPI": metric})
        view = service.daily_average_comparisons(
            {"KOSPI": metric},
            {"KOSPI": DashboardSeriesView(metric, daily)},
        )["KOSPI"]

    assert not captured
    assert metric.displays_value and metric.value == pytest.approx(2810.25)
    assert "KOSPI" not in graph
    assert not view.displays_5 and not view.displays_20
    assert view.latest_value is None
    assert "검증" in (view.unavailable_reason or "")


@pytest.mark.parametrize("symbol", ["KOSPI", "KOSDAQ"])
@pytest.mark.parametrize("timestamp_basis", ["PROVIDER_TIMESTAMP", None])
def test_toss_daily_graph_and_average_reject_wrong_or_missing_timestamp_basis(
    tmp_path, monkeypatch, symbol, timestamp_basis,
):
    daily = pd.DataFrame({
        "date": pd.bdate_range(end="2026-08-25", periods=20),
        "value": [800.0 + offset for offset in range(20)],
    })
    metric = replace(
        _average_metric(2810.25),
        dataset_id="TOSS_MARKET_PRICE_SNAPSHOT",
        series_id=symbol,
        as_of="08-26 10:00 KST",
        route=_toss_domestic_ur246_route(symbol).route_id,
        source_timestamp="2026-08-26T01:00:00+00:00",
        retrieved_at_utc="2026-08-26T01:00:00+00:00",
        timestamp_basis=timestamp_basis,
    )
    service = DashboardService(tmp_path)
    raw_daily = daily.rename(columns={"value": "close"}).assign(symbol=symbol)
    monkeypatch.setattr(
        service.index, "asset_series", lambda *_args, **_kwargs: raw_daily.copy(),
    )

    with warnings.catch_warnings(record=True) as captured:
        warnings.simplefilter("always")
        graph = service.dashboard_series({symbol: metric})
        comparison = service.daily_average_comparisons(
            {symbol: metric},
            {symbol: DashboardSeriesView(metric, daily)},
        )[symbol]

    assert not captured
    assert metric.displays_value and metric.value == pytest.approx(2810.25)
    assert symbol not in graph
    assert not comparison.displays_5 and not comparison.displays_20
    assert comparison.latest_value is None
    assert "검증" in (comparison.unavailable_reason or "")


def test_daily_average_requires_each_actual_window_independently():
    frame = pd.DataFrame({
        "date": pd.date_range("2026-08-14", periods=7, freq="D"),
        "value": [113.0, 114.0, 115.0, 116.0, 117.0, 118.0, 119.0],
    })

    view = DashboardService.build_daily_average_comparison(
        _average_metric(), frame, comparison_kind="relative_percent",
    )

    assert view.displays_5
    assert not view.displays_20
    assert view.comparison_20 is None
    assert view.reason_20 == "실제 완료 일봉 20개가 필요합니다."


@pytest.mark.parametrize("mutation", ["duplicate", "intraday", "partial", "latest_mismatch"])
def test_daily_average_rejects_duplicate_mixed_partial_and_mismatched_rows(mutation):
    frame = pd.DataFrame({
        "date": pd.date_range("2026-08-01", periods=20, freq="D"),
        "value": [float(value) for value in range(100, 120)],
    })
    if mutation == "duplicate":
        frame.loc[18, "date"] = frame.loc[19, "date"]
    elif mutation == "intraday":
        frame.loc[10, "date"] = frame.loc[10, "date"] + timedelta(hours=12)
    elif mutation == "partial":
        frame["is_partial"] = False
        frame.loc[19, "is_partial"] = True
    else:
        frame.loc[19, "value"] = 999.0

    view = DashboardService.build_daily_average_comparison(
        _average_metric(), frame, comparison_kind="relative_percent",
    )

    assert not view.displays_5
    assert not view.displays_20
    assert view.latest_value is None
    assert "계약" in (view.unavailable_reason or "")


@pytest.mark.parametrize(
    ("freshness", "state"),
    [
        ("STALE", DashboardDisplayState.REFRESH_REQUIRED),
        ("UNKNOWN", DashboardDisplayState.UNAVAILABLE),
        ("PARTIAL", DashboardDisplayState.UNAVAILABLE),
        ("READ_FAILURE", DashboardDisplayState.UNAVAILABLE),
    ],
)
def test_ineligible_latest_state_suppresses_daily_average_numbers(freshness, state):
    frame = pd.DataFrame({
        "date": pd.date_range("2026-08-01", periods=20, freq="D"),
        "value": [float(value) for value in range(100, 120)],
    })
    metric = _average_metric(None, freshness=freshness, state=state)

    view = DashboardService.build_daily_average_comparison(
        metric, frame, comparison_kind="relative_percent",
    )

    assert view.comparison_5 is None
    assert view.comparison_20 is None
    assert not view.displays_5 and not view.displays_20


def _synthetic_account_view(
    *, freshness: str = "LOCAL_VALIDATED", history_points: int = 2,
) -> AccountSnapshotView:
    market_values = (400.0, 250.0, 150.0, 100.0, 80.0, 20.0)
    positions = tuple(
        AccountPositionView(
            symbol=f"SYN{number}", name=f"Synthetic {number}", quantity=float(number),
            market_value=value, realized_pnl=None,
            unrealized_pnl=value * 0.1, purchase_amount=value * 0.9,
            currency="KRW",
        )
        for number, value in enumerate(market_values, start=1)
    )
    history = tuple(
        AccountAssetPoint(f"2026-08-{18 + index:02d}", 1_100.0 + 100 * index)
        for index in range(history_points)
    )
    return AccountSnapshotView(
        state=AccountSnapshotState.LOCAL_MOCK,
        provider="SYNTHETIC_LOCAL", source_mode="LOCAL_TEST",
        registered_holder_scope="SELF", economic_attribution_scope="SELF",
        include_in_user_fund_total=True, as_of="2026-08-20T09:00:00+09:00",
        last_reconciled_at="2026-08-20T09:00:00+09:00", currency="KRW",
        total_assets=1_200.0, securities_value=1_000.0, cash_balance=200.0,
        available_cash=150.0, unrealized_pnl=100.0,
        positions=positions, asset_history=history, freshness=freshness,
    )


def test_account_portfolio_projection_maps_headlines_holdings_other_and_history():
    portfolio = AccountPortfolioView(entries=(
        AccountPortfolioEntryView("synthetic", "Synthetic account", _synthetic_account_view()),
    ), user_fund_totals=())

    result = build_account_portfolio_presentation(portfolio)

    assert result.available and result.displayable_accounts == 1
    assert result.currencies[0].total_assets == 1_200.0
    assert result.currencies[0].securities_value == 1_000.0
    assert len(result.holdings) == 6
    assert result.holdings[0].weight_pct == pytest.approx(40.0)
    assert [item.label for item in result.allocations][-1] == "기타"
    assert result.allocations[-1].weight_pct == pytest.approx(2.0)
    assert result.allocations[-1].exact_breakdown == (
        "Synthetic account · Synthetic 6 (SYN6)",
    )
    assert len(result.histories) == 1 and len(result.histories[0].points) == 2


def test_account_projection_stale_partial_provider_suppresses_aggregate_and_history():
    current = _synthetic_account_view(history_points=1)
    stale = _synthetic_account_view(freshness="STALE")
    portfolio = AccountPortfolioView(entries=(
        AccountPortfolioEntryView("current", "Current synthetic", current),
        AccountPortfolioEntryView("stale", "Stale synthetic", stale),
    ), user_fund_totals=())

    result = build_account_portfolio_presentation(portfolio)

    assert result.displayable_accounts == 1 and result.unavailable_accounts == 1
    assert result.currencies[0].total_assets is None
    assert result.currencies[0].securities_value is None
    assert "일부 계좌" in (result.currencies[0].reason or "")
    assert result.holdings == ()
    assert result.allocations == ()
    assert result.histories == ()
    assert [option.source_id for option in result.source_options] == ["current", "stale"]
    assert not result.scope_complete and result.selected_source_id is None

    current_only = build_account_portfolio_presentation(
        portfolio, selected_source_id="current",
    )
    assert current_only.scope_complete and current_only.selected_source_id == "current"
    assert current_only.currencies[0].total_assets == 1_200.0
    assert current_only.currencies[0].securities_value == 1_000.0
    assert len(current_only.holdings) == 6

    stale_only = build_account_portfolio_presentation(
        portfolio, selected_source_id="stale",
    )
    assert not stale_only.available and not stale_only.scope_complete
    assert stale_only.currencies == () and stale_only.holdings == ()
    assert stale_only.scope_reason == "STALE"


def test_account_projection_unknown_source_selection_fails_closed_without_defaulting():
    portfolio = AccountPortfolioView(entries=(
        AccountPortfolioEntryView(
            "current", "Current synthetic", _synthetic_account_view(),
        ),
    ), user_fund_totals=())

    result = build_account_portfolio_presentation(
        portfolio, selected_source_id="missing-source",
    )

    assert not result.available and not result.scope_complete
    assert result.selected_source_id == "missing-source"
    assert result.currencies == () and result.holdings == ()
    assert "현재 구성에 없습니다" in (result.scope_reason or "")


def test_account_projection_preserves_mixed_currencies_without_cross_sum():
    summaries = (
        AccountCurrencySummaryView(
            "KRW", 900.0, 1_000.0, 990.0, 100.0, 90.0, 5.0, 500.0,
        ),
        AccountCurrencySummaryView(
            "USD", 180.0, 200.0, 198.0, 20.0, 18.0, 1.0, 35.5,
        ),
    )
    view = AccountSnapshotView(
        state=AccountSnapshotState.TOSS_READ_ONLY,
        provider="SYNTHETIC_TOSS", source_mode="SANITIZED_READ_ONLY",
        registered_holder_scope="SELF", economic_attribution_scope="SELF",
        include_in_user_fund_total=True, as_of="2026-08-20T00:00:00+00:00",
        currency=None, total_assets=None, securities_value=None,
        positions=(
            AccountPositionView("SYNKR", "Synthetic KR", 1.0, 1_000.0, None, 100.0, purchase_amount=900.0, currency="KRW"),
            AccountPositionView("SYNUS", "Synthetic US", 1.0, 200.0, None, 20.0, purchase_amount=180.0, currency="USD"),
        ),
        currency_summaries=summaries,
    )

    result = build_account_portfolio_presentation(AccountPortfolioView(
        entries=(AccountPortfolioEntryView("toss", "Synthetic Toss", view),),
        user_fund_totals=(),
    ))

    assert [row.currency for row in result.currencies] == ["KRW", "USD"]
    assert [row.securities_value for row in result.currencies] == [1_000.0, 200.0]
    assert [row.available_cash for row in result.currencies] == [500.0, 35.5]
    assert [row.unrealized_pnl_after_cost for row in result.currencies] == [90.0, 18.0]
    assert [row.daily_pnl for row in result.currencies] == [5.0, 1.0]
    assert all(row.total_assets is None for row in result.currencies)
    assert {holding.currency for holding in result.holdings} == {"KRW", "USD"}
def test_korean_index_final_daily_close_remains_visible_after_session_close() -> None:
    metric = DashboardMetricView(
        dataset_id="kr_index_daily", series_id="KOSPI", label="KOSPI",
        value=6912.95, unit="index points", as_of="2026-08-21",
        expected_as_of="2026-08-21", source="KRX", freshness="CURRENT",
        pit_status="PIT_LIMITED", pit_label="descriptive", automation_policy="DAILY",
        automation_enabled=True, display_state=DashboardDisplayState.VALUE,
        unavailable_reason=None, route="NORMALIZED_DAILY", change=60.37,
        change_pct=0.880982,
    )
    gated = DashboardService._gate_current_metric(
        metric, now_utc="2026-08-22T01:00:00+09:00",
        allow_kr_market_closed_last_verified=True,
    )

    assert gated.displays_value and gated.value == pytest.approx(6912.95)
    assert gated.freshness == "MARKET_CLOSED_LAST_FINAL"
    assert gated.change == pytest.approx(60.37)


def test_global_current_cash_and_futures_keep_last_close_but_bitcoin_expires() -> None:
    now = pd.Timestamp("2026-08-21T22:10:00Z")

    def metric(series_id: str, source: str) -> DashboardMetricView:
        return DashboardMetricView(
            dataset_id="market_price_60m_current", series_id=series_id,
            label=series_id, value=100.0, unit="index points",
            as_of="08-21 16:00 KST", expected_as_of=None, source="Yahoo",
            freshness="CURRENT_COMPLETED_60M", pit_status="PIT_BLOCKED",
            pit_label="display only", automation_policy="EVERY_30_MIN_CURRENT_ONLY",
            automation_enabled=True, display_state=DashboardDisplayState.VALUE,
            unavailable_reason=None,
            route=f"yahoo-global60m-current:X:{series_id}",
            source_timestamp=source, completed_bar=True,
        )

    cash = DashboardService._gate_current_metric(
        metric("SP500", "2026-08-21T20:00:00Z"), now_utc=now,
    )
    futures = DashboardService._gate_current_metric(
        metric("NQ_FUTURES", "2026-08-21T20:00:00Z"), now_utc=now,
    )
    bitcoin = DashboardService._gate_current_metric(
        metric("BITCOIN", "2026-08-21T21:00:00Z"), now_utc=now,
    )

    assert cash.displays_value and cash.freshness == "MARKET_CLOSED_LAST_FINAL"
    assert futures.displays_value and futures.freshness == "MARKET_CLOSED_LAST_FINAL"
    assert bitcoin.value is None
    assert bitcoin.freshness == "CURRENT_GATE_BLOCKED"
    assert "CURRENT_SOURCE_AGE_OVER_60M" in (bitcoin.unavailable_reason or "")


def test_current_card_stage_is_provider_parquet_health_zero_and_fails_closed(tmp_path, monkeypatch):
    root = tmp_path / "data/state/current_observations/global60m_current"
    root.mkdir(parents=True)
    path = root / "usd_krw_60m.json"
    path.write_text(json.dumps({
        "schema_version": 1,
        "observations": [{
            "route_id": "yahoo-market-current:GLOBAL_FX:KRW=X",
            "identity": {
                "dataset_id": "MARKET_PRICE_CURRENT",
                "market": "GLOBAL_FX", "symbol": "KRW=X",
            },
            "interval": "30m", "value": 1385.18, "unit": "KRW per USD",
            "provider": "YAHOO", "upstream_provider": "YAHOO_CHART_API",
            "source_route": "YAHOO_CHART_30M:KRW=X",
            "provider_timestamp_utc": "2026-08-24T17:00:00+00:00",
            "retrieved_at_utc": "2026-08-24T17:20:00+00:00",
            "finality": "AS_RETRIEVED", "display_only": True,
            "pit_safe": False, "timestamp_basis": "PROVIDER_TIMESTAMP",
        }],
        "circuits": {}, "decisions": {},
    }), encoding="utf-8")
    service = DashboardService(tmp_path)
    monkeypatch.setattr(
        service.query, "read",
        lambda *_args, **_kwargs: pytest.fail("current stage touched Parquet read"),
    )
    monkeypatch.setattr(
        service.query, "tail",
        lambda *_args, **_kwargs: pytest.fail("current stage touched Parquet tail"),
    )

    started = pd.Timestamp.now(tz="UTC")
    stage = service.current_card_stage(now_utc="2026-08-24T17:30:00Z")
    elapsed = (pd.Timestamp.now(tz="UTC") - started).total_seconds()

    metric = stage.metrics["USD_KRW_60M"]
    assert elapsed < 2.0
    assert metric.displays_value and metric.value == pytest.approx(1385.18)
    assert metric.source_timestamp == "2026-08-24T17:00:00+00:00"
    assert stage.treasury_rate_views.keys() == {
        "UST2", "UST5_QUOTE", "UST10", "UST30", "UST10_2_SPREAD",
    }

    stale = service.current_card_stage(now_utc="2026-08-24T19:00:01Z")
    assert stale.metrics["USD_KRW_60M"].value is None
    assert stale.metrics["USD_KRW_60M"].display_state is DashboardDisplayState.REFRESH_REQUIRED

    path.write_text("{malformed", encoding="utf-8")
    malformed = service.current_card_stage(now_utc="2026-08-24T17:30:00Z")
    assert malformed.metrics["USD_KRW_60M"].value is None
    assert not malformed.metrics["USD_KRW_60M"].displays_value


def test_dashboard_snapshot_stops_between_sections_with_truthful_degraded_result(
    tmp_path, monkeypatch,
):
    service = DashboardService(tmp_path)
    empty_stage = DashboardCurrentStageView(
        as_of_utc="2026-08-24T17:30:00+00:00", metrics={},
        treasury_rate_views={},
    )
    monkeypatch.setattr(service, "current_card_stage", lambda **_kwargs: empty_stage)
    monkeypatch.setattr(
        DailyHealthArtifactService, "load",
        lambda _self: HealthArtifactView("READY", "fixture", ()),
    )

    calls: list[str] = []

    def slow_metrics(*_args, **_kwargs):
        import time
        calls.append("dashboard_metrics")
        time.sleep(0.03)
        return {}

    monkeypatch.setattr(service, "dashboard_metrics", slow_metrics)
    monkeypatch.setattr(
        service, "dashboard_series",
        lambda *_args, **_kwargs: pytest.fail("budgeted snapshot started a later section"),
    )
    started = pd.Timestamp.now(tz="UTC")
    snapshot = service.snapshot(
        now_utc="2026-08-24T17:30:00Z", max_seconds=0.01,
    )
    elapsed = (pd.Timestamp.now(tz="UTC") - started).total_seconds()

    assert calls == ["dashboard_metrics"]
    assert elapsed < 0.2
    assert snapshot["snapshot_state"] == "DEGRADED_BOUNDED"
    assert snapshot["dashboard_series"] == {}
    assert any(
        "SNAPSHOT_TIME_BUDGET" in reason
        for reason in snapshot["snapshot_degraded_reasons"]
    )
