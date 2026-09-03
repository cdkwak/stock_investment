from __future__ import annotations

import json

import pandas as pd

import stock_data.research.forward_test as forward


def _candidate() -> dict[str, object]:
    return {
        "id": "daily_rule",
        "name": "daily",
        "side": "drawdown",
        "basket": "KR",
        "status": "active",
        "definition": {
            "type": "ladder",
            "indicators": [
                {"key": "drawdown252", "op": "<=", "threshold": -0.20},
                {"key": "disp60", "op": "<=", "threshold": -0.10},
            ],
            "levels": 2,
        },
        "added_on": "2026-09-04",
        "reason": "test",
    }


def test_forward_log_is_idempotent_and_joins_realised_returns(
    tmp_path, monkeypatch,
) -> None:
    dates = pd.bdate_range("2026-01-02", periods=100)
    prices = pd.DataFrame({
        "date": dates,
        "series_id": "KOSPI200",
        "basket": "KR",
        "close": 100.0 + pd.Series(range(100), dtype="float64").to_numpy(),
    })
    indicators = prices.copy()
    indicators["observation_date"] = indicators["date"].dt.strftime("%Y-%m-%d")
    indicators["drawdown252"] = -0.30
    indicators["disp60"] = -0.15
    indicators["rsi14"] = 25.0
    indicators["vol_index_percentile252"] = 0.8
    indicators["realized_volatility_20d"] = 0.20
    registry = {
        "schema_version": 1,
        "attempt_count": 1,
        "history": [{"date": "2026-09-04", "action": "add", "id": "daily_rule", "reason": "test"}],
        "candidates": [_candidate()],
    }
    monkeypatch.setattr(forward, "load_candidates", lambda _root: registry)
    monkeypatch.setattr(forward, "rules_version", lambda _root: "b" * 64)
    monkeypatch.setattr(forward, "load_indicator_frame", lambda _root: indicators)
    monkeypatch.setattr(forward, "load_primary_indices", lambda _root: prices)
    as_of = dates[0].date().isoformat()

    first = forward.record_forward_signals(tmp_path, as_of=as_of)
    second = forward.record_forward_signals(tmp_path, as_of=as_of)
    assert first["appended"] == 1
    assert second["status"] == "NOOP_IDEMPOTENT"
    path = tmp_path / forward.FORWARD_LOG
    lines = path.read_text("utf-8").splitlines()
    assert len(lines) == 1
    assert tuple(json.loads(lines[0])) == forward.ROW_KEYS

    frame, summary = forward.load_forward_test(tmp_path)
    assert frame.loc[0, "realised_return_20"] == 1.2 - 1.0
    assert frame.loc[0, "realised_return_60"] == 1.6 - 1.0
    assert frame.loc[0, "realised_return_90"] == 1.9 - 1.0
    assert summary.loc[0, "candidate_id"] == "daily_rule"
    assert summary.loc[0, "n_90"] == 1
    assert summary.loc[0, "hit_90"] == 1.0
