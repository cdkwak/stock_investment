from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "scripts"
    / "manual"
    / "research"
    / "probe_tossinvest_historical_coverage.py"
)
SPEC = importlib.util.spec_from_file_location("toss_historical_probe", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)


def test_probe_is_bounded_and_uses_only_documented_cursor_parameters():
    assert len(probe.ANCHOR_DATES) == 6
    assert len(probe.SERIES) * len(probe.ANCHOR_DATES) == 54
    assert all("/orders" not in path and "/accounts" not in path for _, path, _ in probe.SERIES)

    candle = probe._series_params("market_index_kospi", "before", "2020-01-02")
    assert candle == {
        "interval": "1d",
        "count": 1,
        "before": "2020-01-02T23:59:59+09:00",
    }
    investor = probe._series_params("investor_kospi", "until", "2020-01-02")
    assert investor == {"count": 1, "until": "2020-01-02", "interval": "1d"}
    stock = probe._series_params("short_selling_005930", "until", "2020-01-02")
    assert stock == {"count": 1, "until": "2020-01-02"}


def test_probe_extracts_rows_and_terminal_cursor_without_transforming_sample():
    row = {"date": "2020-01-02", "shortSellingVolume": "0"}
    rows, cursor = probe._extract(
        {"result": {"records": [row], "nextUntil": None}}
    )
    assert rows == [row]
    assert cursor is None
    assert probe._row_date(row) == "2020-01-02"
