from __future__ import annotations

import math

import pandas as pd
import pytest

from stock_web.api.indicators import calculate_indicators, resample_ohlcv
from stock_web.api.market_page import build_market_page_payload
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
