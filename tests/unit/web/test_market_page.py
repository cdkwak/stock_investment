from __future__ import annotations

import math
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

import stock_web.api.market_page as market_page
from stock_web.api.indicators import calculate_indicators, resample_ohlcv
from stock_web.api.market_page import (
    METRIC_EXPLANATIONS,
    _cumulative_points,
    _flow_market,
    _range_view,
    build_derivatives,
    build_flows_and_balances,
    build_market_page_payload,
    build_valuation,
    format_compact_kr,
)
from stock_web.app import create_app
from tests.unit.web import ASGITestClient, make_project, new_temp_root


def _synthetic_ohlcv(values: list[float]) -> pd.DataFrame:
    close = pd.Series(values, dtype=float)
    return pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=len(close), freq="D"),
        "open": close - 0.25,
        "high": close + 1.0,
        "low": close - 1.0,
        "close": close,
        "volume": [100 + index for index in range(len(close))],
    })


def test_indicator_moving_averages_and_bollinger_match_hand_calculation() -> None:
    result = calculate_indicators(_synthetic_ohlcv(list(range(1, 31))))

    assert result["ma5"].iloc[-1] == pytest.approx((26 + 27 + 28 + 29 + 30) / 5)
    assert result["ma20"].iloc[-1] == pytest.approx(sum(range(11, 31)) / 20)
    expected_std = math.sqrt(sum((value - 10.5) ** 2 for value in range(1, 21)) / 20)
    assert result["bollinger_mid"].iloc[19] == pytest.approx(10.5)
    assert result["bollinger_upper"].iloc[19] == pytest.approx(10.5 + 2 * expected_std)
    assert result["bollinger_lower"].iloc[19] == pytest.approx(10.5 - 2 * expected_std)


def test_indicator_rsi_macd_and_stochastic_match_hand_calculation() -> None:
    rising = calculate_indicators(_synthetic_ohlcv(list(range(1, 31))))
    constant = calculate_indicators(_synthetic_ohlcv([5.0] * 30))

    assert rising["rsi14"].iloc[14] == pytest.approx(100.0)
    assert constant["rsi14"].iloc[14] == pytest.approx(50.0)
    assert constant["macd"].iloc[-1] == pytest.approx(0.0)
    assert constant["macd_signal"].iloc[-1] == pytest.approx(0.0)
    assert constant["macd_histogram"].iloc[-1] == pytest.approx(0.0)
    # At close 14, the 14-bar low/high are 0 and 15: (14 - 0) / 15 * 100.
    assert rising["stochastic_k"].iloc[13] == pytest.approx(14 / 15 * 100)
    assert rising["stochastic_d"].iloc[15] == pytest.approx(14 / 15 * 100)


def test_weekly_and_monthly_resample_use_ohlc_volume_and_actual_last_session() -> None:
    close = pd.Series([1, 2, 3, 4, 5, 6], dtype=float)
    daily = pd.DataFrame({
        "date": pd.date_range("2024-01-29", periods=6, freq="D"),
        "open": close - 0.1,
        "high": close + 0.5,
        "low": close - 0.5,
        "close": close,
        "volume": [10, 20, 30, 40, 50, 60],
    })

    weekly = resample_ohlcv(daily, "1w")
    monthly = resample_ohlcv(daily, "1M")

    assert weekly["date"].dt.strftime("%Y-%m-%d").tolist() == ["2024-02-02", "2024-02-03"]
    assert weekly.iloc[0].to_dict() == {
        "date": pd.Timestamp("2024-02-02"), "open": pytest.approx(0.9),
        "high": pytest.approx(5.5), "low": pytest.approx(0.5),
        "close": pytest.approx(5.0), "volume": pytest.approx(150.0),
    }
    assert monthly["date"].dt.strftime("%Y-%m-%d").tolist() == ["2024-01-31", "2024-02-03"]
    assert monthly["close"].tolist() == [3.0, 6.0]
    assert monthly["volume"].tolist() == [60.0, 150.0]


def test_market_page_and_chart_api_render_with_selected_indicators() -> None:
    root = make_project(new_temp_root())
    client = ASGITestClient(create_app(root))

    page = client.get("/market")
    chart = client.get(
        "/api/market/chart",
        params={
            "symbol": "KOSPI", "interval": "1w", "range": "1Y",
            "indicators": "ma20,rsi14",
        },
    )

    assert page.status_code == 200
    assert "파생 상세" in page.text
    assert "수급 · 잔고 상세" in page.text
    assert "선행 PER·PBR — 소스 검증 전" in page.text
    assert chart.status_code == 200
    payload = chart.json()
    assert payload["symbol"] == "KOSPI"
    assert payload["interval"] == "1w"
    assert payload["active_indicators"] == ["ma20", "rsi14"]
    assert payload["candles"]
    assert set(payload["indicators"]) == {"ma20", "rsi14"}


def test_market_sections_with_missing_data_return_reasons() -> None:
    root = new_temp_root()
    payload = build_market_page_payload(root)

    assert payload["chart_symbols"] == []
    for name in ("derivatives", "flows", "valuation"):
        section = payload["sections"][name]
        assert section["status"] == "UNAVAILABLE"
        assert section["reason"]
    assert payload["sections"]["derivatives"]["basis"]["reason"]
    assert payload["sections"]["flows"]["credit"]["reason"]
    assert all(item["reason"] for item in payload["sections"]["valuation"]["markets"])


def test_market_api_sections_with_missing_data_are_json_reasons() -> None:
    response = ASGITestClient(create_app(new_temp_root())).get("/api/market")

    assert response.status_code == 200
    assert response.json()["sections"]["flows"]["reason"]


def test_market_chart_uses_compact_height_and_panel_proportions() -> None:
    web_root = Path(__file__).parents[3] / "src/stock_web"
    css = (web_root / "static/app.css").read_text(encoding="utf-8")
    market_css = (web_root / "static/market.css").read_text(encoding="utf-8")
    script = (web_root / "static/market.js").read_text(encoding="utf-8")
    template = (web_root / "templates/market.html").read_text(encoding="utf-8")

    assert "height: 440px" in css
    assert "height: 360px" in css
    assert "height: 280px" in css
    assert "Math.min(0.14, 0.58 / panels.length)" in script
    assert script.count("LightweightCharts.createChart") == 1
    assert "SIChart.renderLineChart" in script
    assert 'href="/static/market.css?v={{ static_version }}"' in template
    assert ".market-valuation-panel" in market_css
    assert 'fetch(`/api/market?flows_range=${encodeURIComponent(range)}`)' in script
    assert "Math.abs(Number(value)) >= 10000" in script
    assert 'id="breadth-rows"' in template
    assert 'id="lending-summary-rows"' in template


@pytest.mark.parametrize(("value", "expected"), [
    (28_800_000_000_000, "28.8조"),
    (423_200_000_000, "4,232억"),
    (32_000, "3.2만"),
    (999, "999"),
])
def test_compact_korean_number_formatter(value: float, expected: str) -> None:
    assert format_compact_kr(value) == expected


def test_all_range_returns_full_history() -> None:
    frame = pd.DataFrame({
        "date": pd.date_range("2010-01-01", periods=20, freq="YE"),
        "value": range(20),
    })

    pd.testing.assert_frame_equal(_range_view(frame, "ALL"), frame)


def _synthetic_flows(periods: int = 70) -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=periods, freq="D"),
        "market": "KOSPI",
        "foreigner_buy_amount": [12.6e8] * periods,
        "foreigner_sell_amount": [10e8] * periods,
        "institution_buy_amount": [8e8] * periods,
        "institution_sell_amount": [10.4e8] * periods,
        "individual_buy_amount": [10e8] * periods,
        "individual_sell_amount": [9.5e8] * periods,
    })


def test_flow_payload_defaults_to_60_sessions_and_all_returns_full_history() -> None:
    frame = _synthetic_flows()

    default = _flow_market(frame, "KOSPI")
    full = _flow_market(frame, "KOSPI", range_key="ALL")

    assert default["range"] == "60D"
    assert len(default["series"]["foreigner"]["daily_points"]) == 60
    assert full["range"] == "ALL"
    assert len(full["series"]["foreigner"]["daily_points"]) == 70
    assert all(isinstance(point["v"], int) for point in full["series"]["foreigner"]["daily_points"])


def test_cumulative_flow_includes_day_zero_value() -> None:
    result = _cumulative_points([
        {"t": "2026-01-02", "v": 125},
        {"t": "2026-01-05", "v": -25},
    ])

    assert result == [
        {"t": "2026-01-02", "v": 125},
        {"t": "2026-01-05", "v": 100},
    ]


def test_market_payload_uses_requested_flow_range_and_caches_per_range(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frame = _synthetic_flows()
    calls: list[str] = []
    root = new_temp_root()

    monkeypatch.setattr(market_page, "build_chart_symbols", lambda _root: [])
    monkeypatch.setattr(market_page, "build_derivatives", lambda _root: {"status": "UNAVAILABLE", "reason": "fixture"})
    monkeypatch.setattr(market_page, "build_valuation", lambda _root: {"status": "UNAVAILABLE", "reason": "fixture"})

    def fake_flows(_root: Path, *, range_key: str) -> dict[str, object]:
        calls.append(range_key)
        return {"status": "VALUE", "range": range_key, "markets": [_flow_market(frame, "KOSPI", range_key=range_key)]}

    monkeypatch.setattr(market_page, "build_flows_and_balances", fake_flows)

    default = build_market_page_payload(root)
    default_again = build_market_page_payload(root)
    full = build_market_page_payload(root, flows_range="ALL")

    assert default["flows_range"] == "60D"
    assert len(default["sections"]["flows"]["markets"][0]["series"]["foreigner"]["daily_points"]) == 60
    assert default_again is default
    assert full["flows_range"] == "ALL"
    assert len(full["sections"]["flows"]["markets"][0]["series"]["foreigner"]["daily_points"]) == 70
    assert calls == ["60D", "ALL"]


def test_flow_series_are_integer_100m_krw_for_client_views() -> None:
    frame = pd.DataFrame({
        "date": pd.date_range("2025-01-01", periods=70, freq="D"),
        "market": "KOSPI",
        "foreigner_buy_amount": [12.6e8] * 70,
        "foreigner_sell_amount": [10e8] * 70,
        "institution_buy_amount": [8e8] * 70,
        "institution_sell_amount": [10.4e8] * 70,
        "individual_buy_amount": [10e8] * 70,
        "individual_sell_amount": [9.5e8] * 70,
    })

    result = _flow_market(frame, "KOSPI")

    assert result["presentation"] == "CUMULATIVE_FROM_RANGE_START"
    assert result["unit"] == "억원"
    assert result["as_of"] == "2025-03-11"
    for item in result["series"].values():
        assert len(item["daily_points"]) == 60
        assert all(isinstance(point["v"], int) for point in item["daily_points"])


def test_valuation_payload_separates_per_and_pbr_axes(monkeypatch: pytest.MonkeyPatch) -> None:
    frame = pd.DataFrame({
        "date": pd.date_range("2021-01-04", periods=8, freq="365D"),
        "weighted_per": [0.0, 9.0, 12.0, 18.0, 24.0, 30.0, 36.0, 27.0],
        "weighted_pbr": [1.1, 1.2, 1.4, 1.5, 1.7, 1.8, 1.9, 1.85],
    })
    monkeypatch.setattr(market_page.dsx, "load", lambda *_args, **_kwargs: frame)

    payload = build_valuation(Path("unused"))

    for item in payload["markets"]:
        assert set(item["series"]) == {"per", "pbr"}
        assert item["secondary_axis"] is True
        assert item["axes"]["per"] == {"side": "left", "minimum": 0}
        assert item["axes"]["pbr"] == {"side": "right", "minimum": 0}


def test_breadth_and_lending_summary_use_separate_payload_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeMicro:
        @staticmethod
        def lending_market() -> dict[str, object]:
            return {"date": "2026-09-02", "balance_amount": 10e12, "change_1d": 2e8, "change_5d": -3e8}

        @staticmethod
        def breadth() -> list[dict[str, object]]:
            return [{"date": "2026-09-02", "market": "KOSPI", "advancing": 510, "declining": 380, "unchanged": 40, "ad_ratio": 1.34}]

    class FakeDashboardService:
        def __init__(self, _root: Path):
            self.micro = FakeMicro()

    def fake_load(_root: Path, dataset: str, **_kwargs) -> pd.DataFrame | None:
        if dataset.endswith("kr_market_investor_trading_daily"):
            return _synthetic_flows()
        return None

    import stock_data.gui.services as services
    monkeypatch.setattr(services, "DashboardService", FakeDashboardService)
    monkeypatch.setattr(market_page.dsx, "load", fake_load)

    micro = build_flows_and_balances(Path("unused"))["microstructure"]

    assert micro["breadth"]["rows"][0]["advancing"] == 510
    assert micro["lending_summary"]["rows"][0]["value"] == 10e12
    assert "rows" not in micro


def test_metric_explanations_cover_every_requested_head() -> None:
    assert set(METRIC_EXPLANATIONS) == {
        "futures_basis", "volume_pcr", "oi_pcr", "ls_futures_foreign_net",
        "call_wall", "put_wall", "credit_balance", "lending_balance",
        "market_breadth", "ad_ratio", "weighted_per", "weighted_pbr",
        "five_year_percentile",
    }
    assert all(len(text) >= 25 for text in METRIC_EXPLANATIONS.values())


def test_derivatives_payload_uses_near_wall_columns(monkeypatch: pytest.MonkeyPatch) -> None:
    def metric(value: float, as_of: str = "2026-09-02") -> SimpleNamespace:
        return SimpleNamespace(displays_value=True, value=value, as_of=as_of, unit="", source="fixture")

    class FakeQuery:
        def read(self, dataset: str, **_kwargs) -> pd.DataFrame:
            if dataset.endswith("nearest_listed_daily"):
                return pd.DataFrame({
                    "date": pd.date_range("2026-08-31", periods=3),
                    "session": "REGULAR_DAY", "settlement_basis": [1.0, 1.5, 2.0],
                    "basis_status": "SAME_ROW_REGULAR_SESSION_SOURCE_NATIVE_DIFFERENCE",
                })
            return pd.DataFrame({
                "date": pd.date_range("2026-08-31", periods=3),
                "volume_pcr": [0.9, 1.0, 1.1], "open_interest_pcr": [1.1, 1.2, 1.3],
                "observation_status": "AVAILABLE",
            })

    class FakeDerivatives:
        def option_wall(self):
            return pd.DataFrame([{
                "date": pd.Timestamp("2026-09-02"), "maturity_month": "2026-09",
                "underlying_price": 1030.0, "near_wall_window_pct": 15.0,
                "near_call_wall_strike": 1050.0, "near_call_wall_oi": 8000.0,
                "near_call_wall_distance_pct": 1.94, "near_call_wall_status": "WALL_AVAILABLE",
                "near_put_wall_strike": 1000.0, "near_put_wall_oi": 9000.0,
                "near_put_wall_distance_pct": -2.91, "near_put_wall_status": "WALL_AVAILABLE",
            }]), {"status": "RAW"}

        def ls_flow(self):
            return {"status": "RAW_DESCRIPTIVE_ONLY", "warning": "fixture"}

    class FakeDashboardService:
        def __init__(self, _root: Path):
            self.query = FakeQuery()
            self.derivatives = FakeDerivatives()

        def dashboard_metrics(self):
            return {
                "KOSPI200_BASIS": metric(2.0), "VOLUME_PCR": metric(1.1),
                "OI_PCR": metric(1.3), "CALL_WALL": metric(1597.5),
                "PUT_WALL": metric(700.0), "LS_FUTURES_FOREIGN_NET": metric(1234.4),
            }

    import stock_data.gui.services as services
    monkeypatch.setattr(services, "DashboardService", FakeDashboardService)

    payload = build_derivatives(Path("unused"))
    row = payload["wall"]["rows"][0]

    assert row["near_call_wall_strike"] == 1050.0
    assert row["near_put_wall_strike"] == 1000.0
    assert row["near_call_wall_distance_pct"] == pytest.approx(1.94)
    assert payload["wall"]["near_window_available"] is True
    assert payload["basis"]["basis_label"] == "기준일 2026-09-02 · D+1 공개"
    assert payload["wall"]["basis_label"] == "기준일 2026-09-02 · D+1 공개"
