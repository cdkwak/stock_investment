from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from stock_data.orchestration import derivatives_daily_live as live


def _prior_frame() -> pd.DataFrame:
    return pd.DataFrame({
        "date": ["2026-09-01", "2026-09-02"],
        "call_wall_strike": [1500.0, 1597.5],
        "put_wall_strike": [700.0, 700.0],
    })


def _rebuilt(dates: tuple[date, ...], *, extra: bool) -> pd.DataFrame:
    frame = pd.DataFrame({
        "date": pd.to_datetime([value.isoformat() for value in dates]),
        "call_wall_strike": [1597.5] * len(dates),
        "put_wall_strike": [700.0] * len(dates),
    })
    if extra:
        frame["near_call_wall_strike"] = [1100.0] * len(dates)
        frame["near_wall_window_pct"] = [10.0] * len(dates)
    return frame


def test_build_wall_extends_prior_artifact_with_additive_columns(tmp_path, monkeypatch) -> None:
    output = tmp_path / "artifacts/analysis/kospi200_option_wall_recent_250.csv"
    output.parent.mkdir(parents=True)
    _prior_frame().to_csv(output, index=False)
    monkeypatch.setattr(
        live, "_wall_rows_for_dates",
        lambda _root, _options, dates: _rebuilt(tuple(dates), extra=True),
    )

    rows = live._build_wall(tmp_path, tmp_path / "bridge", date(2026, 9, 3), output)

    restored = pd.read_csv(output)
    assert rows == 3
    assert list(restored.columns) == [
        "date", "call_wall_strike", "put_wall_strike",
        "near_call_wall_strike", "near_wall_window_pct",
    ]
    assert restored["near_call_wall_strike"].isna().tolist() == [True, True, False]
    assert restored["date"].tolist()[-1] == "2026-09-03"


def test_build_wall_still_rejects_a_rebuild_that_drops_prior_columns(tmp_path, monkeypatch) -> None:
    output = tmp_path / "artifacts/analysis/kospi200_option_wall_recent_250.csv"
    output.parent.mkdir(parents=True)
    _prior_frame().assign(extra_prior_only=[1.0, 2.0]).to_csv(output, index=False)
    monkeypatch.setattr(
        live, "_wall_rows_for_dates",
        lambda _root, _options, dates: _rebuilt(tuple(dates), extra=False),
    )

    with pytest.raises(live.DerivativesDailyLiveError, match="prior Wall schema differs"):
        live._build_wall(tmp_path, tmp_path / "bridge", date(2026, 9, 3), output)
