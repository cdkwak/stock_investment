from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from stock_web.api import regime
from stock_web.api.regime import market_score, score_label
from tests.unit.web import new_temp_root


@pytest.mark.parametrize(
    ("rsi", "trend_percentile", "volatility", "expected"),
    [
        (48.0, 68.0, 11.0, 1),
        (42.0, 96.0, 15.0, 1),
        (82.0, 98.0, None, 2),
        (25.0, 2.0, None, -2),
        (None, None, 10.0, 1),
        (None, None, 90.0, -1),
        (70.0, 90.0, 50.0, 2),
        (80.0, 50.0, 50.0, 1),
        (20.0, 50.0, 50.0, -1),
        (None, None, None, None),
    ],
)
def test_market_score_thresholds_corroboration_and_missing_inputs(
    rsi: float | None,
    trend_percentile: float | None,
    volatility: float | None,
    expected: int | None,
) -> None:
    assert market_score(rsi, trend_percentile, volatility) == expected


@pytest.mark.parametrize(
    ("rsi", "trend", "volatility", "expected"),
    [
        (80.0, 50.0, 50.0, (2, 0, 0)),
        (70.0, 97.0, 20.0, (1, 2, 1)),
        (30.0, 90.0, 80.0, (-1, 1, -1)),
        (20.0, 10.0, 50.0, (-2, -1, 0)),
        (50.0, 3.0, 50.0, (0, -2, 0)),
    ],
)
def test_market_score_component_thresholds_are_inclusive(
    rsi: float, trend: float, volatility: float,
    expected: tuple[int, int, int],
) -> None:
    assert regime._market_score_components(rsi, trend, volatility) == expected


def test_market_verdict_records_raw_capped_score_note_and_trend_evidence() -> None:
    verdict = regime._market_verdict(
        42.0, 96.0, 15.0, distance_pct=16.0,
        trend_name="200일선", volatility_name="실현변동성 20일 백분위",
    )

    assert verdict["market_score_raw"] == 2
    assert verdict["market_score"] == verdict["score"] == 1
    assert verdict["score_note"] == "과열은 RSI14와 추세의 서로 다른 두 근거가 필요"
    assert regime._trend_evidence_value(
        {"value": 68.0, "distance_pct": 7.1, "contribution": 0}, "200일선",
    ) == "200일선 +7.1% (10년 백분위 68%) → 0"
    assert regime._trend_evidence_value(
        {"value": 4.9, "distance_pct": -7.4, "contribution": -1}, "60일선",
    ) == "60일선 −7.4% (10년 백분위 5%) → −1"


def test_volatility_only_records_the_one_point_cap_note() -> None:
    verdict = regime._market_verdict(
        None, None, 11.0, distance_pct=None,
        trend_name="200일선", volatility_name="VIX",
    )

    assert verdict["market_score_raw"] == verdict["market_score"] == 1
    assert verdict["score_note"] == "변동성 단독으로는 ±1까지"


def test_trend_requires_750_distance_sessions_and_reports_no_data() -> None:
    # 808 closes yield exactly 749 valid MA60 distances.
    frame = pd.DataFrame({"close": [100.0 + index * 0.1 for index in range(808)]})
    metrics = regime._price_regime_metrics(frame, 60)
    verdict = regime._market_verdict(
        metrics["rsi"], metrics["trend_percentile"], None,
        distance_pct=metrics["distance_pct"],
        trend_name="60일선", volatility_name="VKOSPI",
    )

    assert metrics["trend_percentile"] is None
    assert metrics["realized_volatility_percentile"] is not None
    assert verdict["components"][1]["value"] is None
    assert regime._trend_evidence_value(verdict["components"][1], "60일선") == "자료 없음"


def test_latest_percentile_uses_only_the_last_2520_sessions() -> None:
    values = pd.Series([10_000.0, *range(2_520)], dtype=float)

    assert regime._latest_percentile(values) == 100.0


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
        key: korea[key] for key in (
            "score", "market_score_raw", "market_score", "score_note",
            "score_max", "temperature", "hot", "cold",
        )
    } == {
        "score": 0,
        "market_score_raw": 0,
        "market_score": 0,
        "score_note": None,
        "score_max": 2,
        "temperature": "중립",
        "hot": False,
        "cold": False,
    }
    assert korea["components"] == [
        {"name": "RSI14", "value": 48.6, "contribution": 0},
        {
            "name": "60일선 이격 10년 백분위",
            "value": None,
            "distance_pct": 0.0,
            "contribution": None,
        },
        {"name": "VKOSPI", "value": 40.0, "contribution": 0},
    ]
    assert korea["evidence"][0]["value"] == "48.6 → 0"
    assert korea["evidence"][1]["value"] == "자료 없음"
    assert korea["evidence"][1]["evidence"] is False

    assert united_states["score"] == -1
    assert united_states["temperature"] == "약세"
    assert united_states["subtitle"] == "점수 −1 · 기술 0 · 반도체 0 · 자료 2/3"
    assert united_states["sub_verdicts"]["NASDAQ100"]["score"] == 0
    assert united_states["sub_verdicts"]["SOX"]["components"][2] == {
        "name": "실현변동성 20일 백분위", "value": None, "contribution": None,
    }
    assert global_risk["temperature"] == "중립"
