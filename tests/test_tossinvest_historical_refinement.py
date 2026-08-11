from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = (
    Path(__file__).parents[1]
    / "scripts"
    / "manual"
    / "refine_tossinvest_historical_coverage.py"
)
SPEC = importlib.util.spec_from_file_location("toss_historical_refinement", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
refine = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(refine)


def test_refinement_finds_empty_to_data_year_bracket():
    probes = [
        {"anchor": "2020-01-02", "row_count": 1, "valid_empty": False},
        {"anchor": "2018-01-02", "row_count": 0, "valid_empty": True},
        {"anchor": "2015-01-02", "row_count": 0, "valid_empty": True},
    ]
    assert refine._year_bounds(probes) == (2018, 2020)


def test_refinement_uses_documented_year_end_cursor_shapes():
    assert refine._params("market_index_kospi", "before", 2019) == {
        "interval": "1d",
        "count": 1,
        "before": "2019-12-31T23:59:59+09:00",
    }
    assert refine._params("investor_kosdaq", "until", 2019) == {
        "count": 1,
        "until": "2019-12-31",
        "interval": "1d",
    }
    assert refine._params("program_005930", "until", 2019) == {
        "count": 1,
        "until": "2019-12-31",
    }
