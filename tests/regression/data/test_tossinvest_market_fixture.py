from __future__ import annotations

import json
from pathlib import Path


FIXTURE = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "tossinvest_market_live.json"


def test_live_market_fixture_is_sanitized_and_has_verified_shapes():
    text = FIXTURE.read_text(encoding="utf-8")
    for forbidden in (
        "client_id",
        "client_secret",
        "access_token",
        "Authorization",
        "Bearer ",
    ):
        assert forbidden not in text

    fixture = json.loads(text)
    assert set(fixture) == {
        "fixture_version",
        "collected_at",
        "source",
        "operations",
    }
    assert fixture["source"] == "toss_securities_open_api"

    operations = fixture["operations"]
    expected = {
        "market_indicator_prices": ("MARKET_INDICATOR", 8),
        "market_indicator_daily_candles": ("MARKET_INDICATOR_CHART", 2),
        "market_investor_trading": ("MARKET_INDICATOR", 2),
        "stock_program_trades": ("STOCK_TRADING_TREND", 2),
        "stock_short_selling": ("STOCK_TRADING_TREND", 2),
        "stock_credit_trades": ("STOCK_TRADING_TREND", 2),
        "stock_securities_lending": ("STOCK_TRADING_TREND", 2),
    }
    assert set(operations) == set(expected)

    for name, (rate_group, expected_rows) in expected.items():
        operation = operations[name]
        assert operation["http_status"] == 200
        assert operation["rate_limit"]["group"] == rate_group
        result = operation["response"]["result"]
        if isinstance(result, list):
            rows = result
        else:
            rows = result.get("records", result.get("candles"))
        assert isinstance(rows, list)
        assert len(rows) == expected_rows

    prices = operations["market_indicator_prices"]["response"]["result"]
    assert {row["symbol"] for row in prices} == {
        "KOSPI",
        "KOSDAQ",
        "KR_BOND_2Y",
        "KR_BOND_3Y",
        "KR_BOND_5Y",
        "KR_BOND_10Y",
        "KR_BOND_20Y",
        "KR_BOND_30Y",
    }
    assert all(set(row) == {"symbol", "timestamp", "lastPrice"} for row in prices)

    records = operations["market_investor_trading"]["response"]["result"]
    assert set(records) == {"records", "nextUntil"}
    assert set(records["records"][0]) == {
        "date",
        "updatedAt",
        "individual",
        "foreigner",
        "institution",
        "otherCorporation",
    }
