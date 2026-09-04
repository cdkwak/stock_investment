from __future__ import annotations

from datetime import date
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
    _localized_warning,
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
    assert constant["rsi14"].iloc[14] == pytest.approx(100.0)
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
    assert "SIIndicators.rsiWilder" in script
    assert script.count("LightweightCharts.createChart") == 1
    assert "SIChart.renderLineChart" in script
    assert 'href="/static/market.css?v={{ static_version }}"' in template
    assert ".market-valuation-panel" in market_css
    assert 'fetch(`/api/market?flows_range=${encodeURIComponent(range)}`)' in script
    assert script.count('fetch(`/api/market?flows_range=${encodeURIComponent(range)}`)') == 2
    assert "Math.abs(Number(value)) >= 10000" in script
    assert 'id="breadth-rows"' in template
    assert 'id="lending-summary-rows"' in template
    valuation_markup = template.split('id="valuation-section"', 1)[1]
    assert valuation_markup.count('data-explanation="valuation_panel"') == 2
    assert 'data-explanation="weighted_per"' not in valuation_markup
    assert 'data-explanation="weighted_pbr"' not in valuation_markup
    assert script.count("ⓘ") == 1
    assert "5년 백분위 ${percentilePosition(current.per_percentile)}" in script


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
    monkeypatch.setattr(market_page, "build_derivatives", lambda _root, **_kwargs: {"status": "UNAVAILABLE", "reason": "fixture"})
    monkeypatch.setattr(market_page, "build_valuation", lambda _root, **_kwargs: {"status": "UNAVAILABLE", "reason": "fixture"})

    def fake_flows(
        _root: Path, *, range_key: str, history_range_key: str,
    ) -> dict[str, object]:
        assert history_range_key in {"1Y", "ALL"}
        calls.append(range_key)
        return {"status": "VALUE", "range": range_key, "markets": [_flow_market(frame, "KOSPI", range_key=range_key)]}

    monkeypatch.setattr(market_page, "build_flows_and_balances", fake_flows)

    default = build_market_page_payload(root)
    default_again = build_market_page_payload(root)
    full = build_market_page_payload(root, flows_range="ALL")

    assert default["flows_range"] == "60D"
    assert default["history_range"] == "1Y"
    assert len(default["sections"]["flows"]["markets"][0]["series"]["foreigner"]["daily_points"]) == 60
    assert default_again is default
    assert full["flows_range"] == "ALL"
    assert full["history_range"] == "ALL"
    assert len(full["sections"]["flows"]["markets"][0]["series"]["foreigner"]["daily_points"]) == 70
    assert calls == ["60D", "ALL"]


def test_market_payload_bounds_all_default_series_and_all_returns_longer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = new_temp_root()
    flow_frame = _synthetic_flows(400)

    def history_points(range_key: str) -> list[dict[str, object]]:
        count = 400 if range_key == "ALL" else 252
        return [{"t": f"2026-{index // 28 + 1:02d}-{index % 28 + 1:02d}", "v": index} for index in range(count)]

    def fake_derivatives(
        _root: Path, *, range_key: str, public_mode: bool = False,
    ) -> dict[str, object]:
        points = history_points(range_key)
        return {
            "status": "VALUE", "basis": {"status": "VALUE", "series": points},
            "pcr": {
                "volume": {"status": "VALUE", "series": points},
                "oi": {"status": "VALUE", "series": points},
            },
        }

    def fake_flows(
        _root: Path, *, range_key: str, history_range_key: str,
    ) -> dict[str, object]:
        points = history_points(history_range_key)
        return {
            "status": "VALUE", "range": range_key,
            "markets": [_flow_market(flow_frame, "KOSPI", range_key=range_key)],
            "credit": {"status": "VALUE", "series": points},
            "lending": {"status": "VALUE", "series": points},
        }

    def fake_valuation(_root: Path, *, range_key: str) -> dict[str, object]:
        points = history_points(range_key)
        return {
            "status": "VALUE",
            "markets": [{
                "status": "VALUE", "market": "KOSPI",
                "series": {"per": points, "pbr": points},
            }],
        }

    monkeypatch.setattr(market_page, "build_chart_symbols", lambda _root: [])
    monkeypatch.setattr(market_page, "build_derivatives", fake_derivatives)
    monkeypatch.setattr(market_page, "build_flows_and_balances", fake_flows)
    monkeypatch.setattr(market_page, "build_valuation", fake_valuation)

    default = build_market_page_payload(root)
    full = build_market_page_payload(root, flows_range="ALL")

    default_flows = default["sections"]["flows"]
    full_flows = full["sections"]["flows"]
    assert len(default_flows["markets"][0]["series"]["foreigner"]["daily_points"]) == 60
    assert len(default_flows["markets"][0]["series"]["foreigner"]["cumulative_points"]) == 60
    assert len(default_flows["credit"]["series"]) == 252
    assert len(default_flows["lending"]["series"]) == 252
    assert len(default["sections"]["derivatives"]["basis"]["series"]) == 252
    assert len(default["sections"]["valuation"]["markets"][0]["series"]["per"]) == 252
    assert len(full_flows["markets"][0]["series"]["foreigner"]["daily_points"]) == 400
    assert len(full_flows["credit"]["series"]) == 400
    assert len(full["sections"]["derivatives"]["basis"]["series"]) == 400
    assert len(full["sections"]["valuation"]["markets"][0]["series"]["per"]) == 400


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
    full = build_valuation(Path("unused"), range_key="ALL")

    for item in payload["markets"]:
        assert set(item["series"]) == {"per", "pbr"}
        assert item["secondary_axis"] is True
        assert item["axes"]["per"] == {"side": "left", "minimum": 0}
        assert item["axes"]["pbr"] == {"side": "right", "minimum": 0}
    assert len(payload["markets"][0]["series"]["per"]) < len(full["markets"][0]["series"]["per"])


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
        dates = pd.date_range(end="2026-09-02", periods=800)
        if dataset.endswith("kr_credit_balance_daily"):
            return pd.DataFrame({"date": dates, "credit_financing_total": range(800)})
        if dataset.endswith("kr_stock_lending_market_daily"):
            return pd.DataFrame({"date": dates, "balance_amount": range(800)})
        return None

    import stock_data.gui.services as services
    monkeypatch.setattr(services, "DashboardService", FakeDashboardService)
    monkeypatch.setattr(market_page.dsx, "load", fake_load)

    default = build_flows_and_balances(Path("unused"))
    full = build_flows_and_balances(Path("unused"), history_range_key="ALL")
    micro = default["microstructure"]

    assert micro["breadth"]["rows"][0]["advancing"] == 510
    assert micro["lending_summary"]["rows"][0]["value"] == 10e12
    assert "rows" not in micro
    assert len(default["credit"]["series"]) < len(full["credit"]["series"]) == 800
    assert len(default["lending"]["series"]) < len(full["lending"]["series"]) == 800


def test_metric_explanations_cover_every_requested_head() -> None:
    assert set(METRIC_EXPLANATIONS) == {
        "rsi14",
        "futures_basis", "volume_pcr", "oi_pcr", "ls_futures_foreign_net",
        "call_wall", "put_wall", "credit_balance", "lending_balance",
        "market_breadth", "ad_ratio", "weighted_per", "weighted_pbr",
        "five_year_percentile", "valuation_panel",
    }
    assert all(len(text) >= 25 for text in METRIC_EXPLANATIONS.values())
    assert all(term in METRIC_EXPLANATIONS["valuation_panel"] for term in ("PER", "PBR", "5년 백분위", "상위 비율"))


def test_derivatives_payload_uses_near_wall_columns(monkeypatch: pytest.MonkeyPatch) -> None:
    def metric(value: float, as_of: str = "2026-09-02") -> SimpleNamespace:
        return SimpleNamespace(displays_value=True, value=value, as_of=as_of, unit="", source="fixture")

    class FakeQuery:
        def read(self, dataset: str, **_kwargs) -> pd.DataFrame:
            dates = pd.date_range(end="2026-09-02", periods=800)
            if dataset.endswith("nearest_listed_daily"):
                return pd.DataFrame({
                    "date": dates,
                    "session": "REGULAR_DAY", "settlement_basis": range(800),
                    "basis_status": "SAME_ROW_REGULAR_SESSION_SOURCE_NATIVE_DIFFERENCE",
                })
            return pd.DataFrame({
                "date": dates,
                "volume_pcr": [0.9] * 800, "open_interest_pcr": [1.1] * 800,
                "observation_status": "AVAILABLE",
            })

    class FakeDerivatives:
        def option_wall(self):
            return pd.DataFrame([
                {
                    "date": pd.Timestamp("2026-09-02"), "maturity_month": "2026-09",
                    "underlying_price": 1030.0, "near_wall_window_pct": 15.0,
                    "near_call_wall_strike": float("nan"), "near_call_wall_oi": float("nan"),
                    "near_call_wall_distance_pct": float("nan"), "near_call_wall_status": float("nan"),
                    "near_put_wall_strike": float("nan"), "near_put_wall_oi": float("nan"),
                    "near_put_wall_distance_pct": float("nan"), "near_put_wall_status": float("nan"),
                }, {
                    "date": pd.Timestamp("2026-09-03"), "maturity_month": "2026-09",
                    "underlying_price": 1030.0, "near_wall_window_pct": 15.0,
                    "near_call_wall_strike": 1050.0, "near_call_wall_oi": 8000.0,
                    "near_call_wall_distance_pct": 1.94, "near_call_wall_status": "WALL_AVAILABLE",
                    "near_put_wall_strike": 1000.0, "near_put_wall_oi": 9000.0,
                    "near_put_wall_distance_pct": -2.91, "near_put_wall_status": "WALL_AVAILABLE",
                }, {
                    "date": pd.Timestamp("2026-09-04"), "maturity_month": "2026-09",
                    "underlying_price": 1030.0, "near_wall_window_pct": 15.0,
                    "near_call_wall_strike": float("nan"), "near_call_wall_oi": 0.0,
                    "near_call_wall_distance_pct": float("nan"), "near_call_wall_status": "NO_NEAR_WINDOW_OI",
                    "near_put_wall_strike": float("nan"), "near_put_wall_oi": 0.0,
                    "near_put_wall_distance_pct": float("nan"), "near_put_wall_status": "NO_NEAR_WINDOW_OI",
                },
            ]), {"status": "RAW"}

        def ls_flow(self):
            return {
                "status": "RAW_DESCRIPTIVE_ONLY",
                "warning": "Raw provider observation; no Normalized/PIT-safe claim",
            }

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
    monkeypatch.setattr(
        market_page, "_market_derivative_metrics",
        lambda service, _root: service.dashboard_metrics(),
    )

    payload = build_derivatives(Path("unused"))
    full = build_derivatives(Path("unused"), range_key="ALL")
    rows = {row["date"]: row for row in payload["wall"]["rows"]}
    row = rows["2026-09-03"]

    assert row["near_call_wall_strike"] == 1050.0
    assert row["near_put_wall_strike"] == 1000.0
    assert row["near_call_wall_distance_pct"] == pytest.approx(1.94)
    assert payload["wall"]["near_window_available"] is True
    assert payload["basis"]["basis_label"] == "기준일 2026-09-02 · D+1 공개"
    assert payload["wall"]["basis_label"] == "기준일 2026-09-04 · D+1 공개"
    assert rows["2026-09-02"]["near_wall_note"] == "근접 Wall은 2026-09-03부터 계산 (이전 행은 미계산)"
    assert rows["2026-09-04"]["near_wall_note"] == "±15% 창 안에 양의 미결제약정이 없습니다."
    assert payload["ls_flow"]["warning"] == "원시 관측값 · 정규화 전 · 수동 검증 전에는 표시하지 않습니다"
    assert len(payload["basis"]["series"]) < len(full["basis"]["series"]) == 800
    assert len(payload["pcr"]["volume"]["series"]) < len(full["pcr"]["volume"]["series"]) == 800

    market_js = (Path(__file__).parents[3] / "src/stock_web/static/market.js").read_text(encoding="utf-8")
    assert 'row.near_call_wall_status === "NO_NEAR_WINDOW_OI" ? "창 내 OI 없음"' in market_js
    assert '<span class="muted">미계산</span>' in market_js


def test_unknown_ascii_warning_uses_generic_korean_note() -> None:
    assert _localized_warning("Unrecognized provider warning") == "원시 관측값 · 정규화·검증 상태를 확인할 수 없습니다"


def test_market_derivatives_expose_cboe_scope_table_only_in_private_mode() -> None:
    root = new_temp_root()
    path = root / "data/normalized/cboe_daily_pcr_daily/year=2026/data.parquet"
    path.parent.mkdir(parents=True)
    pd.DataFrame({
        "date": [date(2026, 9, 4)] * 5,
        "scope": ["TOTAL", "INDEX", "ETP", "EQUITY", "VIX"],
        "call_volume": [100, 80, 60, 40, 20],
        "put_volume": [120, 72, 66, 52, 30],
        "volume_pcr": [1.2, 0.9, 1.1, 1.3, 1.5],
        "call_oi": [200, 160, 120, 80, 40],
        "put_oi": [220, 144, 132, 104, 60],
        "oi_pcr": [1.1, 0.9, 1.1, 1.3, 1.5],
    }).to_parquet(path, index=False)

    private = build_derivatives(root, public_mode=False)
    public = build_derivatives(root, public_mode=True)

    assert private["cboe_pcr"]["scope_label"] == "Cboe 거래소 합계 · 지수 · ETP · 개별주 · VIX"
    assert [row["label"] for row in private["cboe_pcr"]["rows"]] == [
        "Cboe 거래소 합계", "지수", "ETP", "개별주", "VIX",
    ]
    assert "cboe_pcr" not in public


def test_market_template_and_script_render_the_cboe_pcr_panel_with_reason() -> None:
    root = Path(__file__).parents[3]
    template = root.joinpath("src/stock_web/templates/market.html").read_text(encoding="utf-8")
    script = root.joinpath("src/stock_web/static/market.js").read_text(encoding="utf-8")
    assert 'id="cboe-pcr-panel"' in template
    assert "renderCboePcr(section.cboe_pcr)" in script
    assert 'unavailable(view.reason || "보존된 Cboe 일별 통계가 없습니다.")' in script

