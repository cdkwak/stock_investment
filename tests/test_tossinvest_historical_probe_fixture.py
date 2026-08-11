from __future__ import annotations

import json
from pathlib import Path


FIXTURE = Path(__file__).parent / "fixtures" / "tossinvest_historical_probe.json"


def test_historical_probe_fixture_is_sanitized_bounded_and_date_safe():
    text = FIXTURE.read_text(encoding="utf-8")
    for forbidden in (
        "client_id",
        "client_secret",
        "access_token",
        "Authorization",
        "Bearer ",
    ):
        assert forbidden not in text

    report = json.loads(text)
    assert report["stopped_on_429"] is False
    assert report["total_token_calls"] == 2
    assert report["total_market_calls"] == 76
    assert report["anchors"] == [
        "2025-01-02",
        "2023-01-02",
        "2020-01-02",
        "2018-01-02",
        "2015-01-02",
        "2010-01-02",
    ]
    assert {name: series["first_data_year"] for name, series in report["series"].items()} == {
        "market_index_kospi": 2014,
        "market_index_kosdaq": 2014,
        "investor_kospi": 2014,
        "investor_kosdaq": 2014,
        "program_005930": 2019,
        "short_selling_005930": 2019,
        "credit_005930": 2023,
        "securities_lending_005930": 2021,
        "treasury_10y": 2019,
    }

    for series in report["series"].values():
        assert len(series["probes"]) == 6
        for probe in series["probes"] + series["refinement_probes"]:
            assert probe["http_status"] == 200
            assert probe["no_future_rows"] is True
            assert probe["rate_limit"]["remaining"] >= 0


def test_probe_preserves_verified_old_samples_and_valid_zeroes():
    report = json.loads(FIXTURE.read_text(encoding="utf-8"))
    series = report["series"]

    short = series["short_selling_005930"]["refinement_probes"][0]["sample"]
    assert set(short) == {
        "date",
        "updatedAt",
        "shortSellingVolume",
        "shortSellingAmount",
        "shortSellingVolumeRate",
        "shortSellingAmountRate",
    }
    assert short["date"] == "2019-12-30"

    credit = next(
        probe["sample"]
        for probe in series["credit_005930"]["refinement_probes"]
        if probe.get("sample") and probe["sample"]["date"] == "2023-12-29"
    )
    assert credit["marginLoan"]["newQuantity"] == "0"
    assert credit["stockLoan"]["returnQuantity"] == "0"

    lending = next(
        probe["sample"]
        for probe in series["securities_lending_005930"]["refinement_probes"]
        if probe.get("sample")
    )
    assert lending["date"] == "2021-12-31"
    assert lending["executionQuantity"] == "0"
