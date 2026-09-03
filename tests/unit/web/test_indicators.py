from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

import pytest

from stock_web.api.indicators import rsi_latest, rsi_wilder


CLOSES = [
    44.0, 44.15, 43.9, 44.35, 44.8, 44.6, 45.1, 45.55, 45.2, 44.95,
    45.4, 45.9, 45.7, 46.2, 46.6, 46.1, 45.8, 46.4, 46.9, 47.3,
    47.0, 47.6, 47.2, 46.8, 47.5, 48.0, 47.7, 48.3, 48.9, 48.4,
    49.0, 49.5, 49.1, 49.7, 50.2, 49.8, 50.5, 51.0, 50.6, 51.3,
]
EXPECTED = [
    None, None, None, None, None, None, None, None, None, None,
    None, None, None, None,
    75.49019607843152, 68.28103683492506, 64.3125432440447,
    68.28293408115692, 71.16220872117346, 73.2540234523794,
    69.19976183859711, 72.48025657900538, 67.33155919330697,
    62.54672489881399, 66.97031964431378, 69.72125306113978,
    66.16074456901259, 69.51396751388289, 72.4535885345387,
    66.68313580095435, 69.7922345600498, 72.12657489626237,
    67.62438729409175, 70.58990578751178, 72.823858011023,
    68.35088500136631, 71.63438845778194, 73.73081278531977,
    69.31711885870845, 72.42774752757322,
]


def test_rsi_wilder_matches_fixed_series_and_latest() -> None:
    result = rsi_wilder(CLOSES)

    assert result[:14] == [None] * 14
    assert result[14:] == pytest.approx(EXPECTED[14:])
    assert rsi_latest(CLOSES) == pytest.approx(EXPECTED[-1])


def test_rsi_wilder_zero_loss_and_invalid_gap_rules() -> None:
    assert rsi_wilder([5.0] * 15)[-1] == 100.0
    assert rsi_wilder(range(15))[-1] == 100.0
    assert rsi_wilder(range(14))[-1] is None
    assert rsi_wilder([*range(15), None, *range(15)])[-1] == 100.0
    with pytest.raises(ValueError, match="period"):
        rsi_wilder([1.0, 2.0], 0)


def test_javascript_rsi_matches_python_expected_values() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    app_js = Path(__file__).parents[3] / "src/stock_web/static/app.js"
    script = (
        f"global.window={{}};require({json.dumps(str(app_js))});"
        f"console.log(JSON.stringify(window.SIIndicators.rsiWilder({json.dumps(CLOSES)},14)));"
    )
    completed = subprocess.run(
        [node, "-e", script], check=True, capture_output=True, text=True,
        encoding="utf-8",
    )

    result = json.loads(completed.stdout)
    assert result[:14] == [None] * 14
    assert result[14:] == pytest.approx(EXPECTED[14:])
