from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from stock_web.api import regime
from stock_web.api.regime import market_score, score_label
from tests.unit.web import new_temp_root


@pytest.mark.parametrize(
    ("rsi", "distance", "volatility", "expected"),
    [
        (71.0, 5.0, 20.0, 2),
        (29.0, -5.0, 80.0, -2),
        (70.0, 4.9, 20.1, 0),
        (30.0, -4.9, 79.9, 0),
        (71.0, -5.0, 50.0, 0),
        (None, 5.0, 20.0, 2),
        (29.0, None, 20.0, 0),
        (None, None, 10.0, None),
        (None, None, None, None),
    ],
)
def test_market_score_arithmetic_and_missing_inputs(
    rsi: float | None,
    distance: float | None,
    volatility: float | None,
    expected: int | None,
) -> None:
    assert market_score(rsi, distance, volatility) == expected


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (-3, "침체"),
        (-2, "침체"),
        (-1, "약세"),
        (0, "중립"),
        (1, "강세"),
        (2, "과열"),
        (3, "과열"),
        (None, "자료 없음"),
    ],
)
def test_score_label_mapping(score: int | None, expected: str) -> None:
    assert score_label(score) == expected


def test_build_regime_exposes_scores_components_and_us_subscores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dates = pd.date_range("2025-09-01", periods=252)
    index_frame = pd.DataFrame({
        "date": dates,
        "close": [100.0] * len(dates),
        "disparity60": [92.6] * len(dates),
    })

    class Query:
        def tail(self, dataset: str, **_kwargs: object) -> pd.DataFrame:
            if dataset.endswith("kr_credit_balance_daily"):
                return pd.DataFrame({
                    "date": dates,
                    "credit_financing_total": range(len(dates)),
                })
            if dataset.endswith("kr_market_investor_trading_daily"):
                return pd.DataFrame({
                    "date": dates[-2:],
                    "market": ["KOSPI", "KOSPI"],
                    "foreigner_buy_amount": [1, 1],
                    "foreigner_sell_amount": [2, 2],
                })
            return pd.DataFrame({
                "date": dates[-64:],
                "dgs10": [4.0] * 64,
                "dgs2": [3.5] * 64,
            })

    class Index:
        def series(self, *_args: object) -> pd.DataFrame:
            return index_frame

        def asset_series(self, *_args: object) -> pd.DataFrame:
            return index_frame

    class Service:
        def __init__(self, _root: Path) -> None:
            self.query = Query()
            self.index = Index()

        def volatility(self, **_kwargs: object) -> dict[str, object]:
            return {
                "VKOSPI": {"percentile_250d": 40.0},
                "VIX": {"percentile_250d": 85.0},
            }

        def market_valuation_views(self) -> dict[str, object]:
            return {"KOSPI": SimpleNamespace(rolling_windows=())}

    root = new_temp_root()
    monkeypatch.setattr(regime, "DashboardService", Service)
    monkeypatch.setattr(regime, "rsi_latest", lambda _series: 48.6)
    monkeypatch.setenv("STOCK_WEB_RULES_PATH", str(root / "missing-rules.md"))

    result = regime.build_regime(root, {})
    korea, united_states, global_risk = result["markets"]

    assert {
        key: korea[key]
        for key in ("score", "score_max", "temperature", "hot", "cold")
    } == {
        "score": -1,
        "score_max": 2,
        "temperature": "약세",
        "hot": False,
        "cold": False,
    }
    assert korea["components"] == [
        {"name": "RSI14", "value": 48.6, "contribution": 0},
        {"name": "60일선", "value": pytest.approx(-7.4), "contribution": -1},
        {"name": "VKOSPI", "value": 40.0, "contribution": 0},
    ]
    assert korea["evidence"][0]["value"] == "48.6 → 0"
    assert korea["evidence"][1]["value"] == "−7.4% → −1"

    assert united_states["score"] == -1
    assert united_states["temperature"] == "약세"
    assert united_states["subtitle"] == "점수 −1 · 기술 −1 · 반도체 −1 · 자료 3/3"
    assert united_states["sub_verdicts"]["NASDAQ100"]["score"] == -1
    assert united_states["sub_verdicts"]["SOX"]["components"][2] == {
        "name": "VIX", "value": 85.0, "contribution": -1,
    }
    assert global_risk["temperature"] == "중립"
