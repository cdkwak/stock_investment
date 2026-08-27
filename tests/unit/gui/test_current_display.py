from __future__ import annotations

from dataclasses import replace

import pytest

from stock_data.gui.current_display import (
    CurrentDisplayObservation,
    DashboardCurrentObservation,
    load_dashboard_current,
    load_current_display,
    promote_dashboard_current,
    promote_current_display,
)


def _observation() -> CurrentDisplayObservation:
    return CurrentDisplayObservation(
        symbol="000660", value=1_691_000.0, unit="KRW",
        source_date="2026-08-20",
        retrieved_at_utc="2026-08-20T16:38:18.252506+00:00",
        provider="FinanceDataReader 0.9.202 / Naver daily",
        interval="1d", finality="POLLABLE_DAILY_AS_RETRIEVED",
    )


def test_current_display_promotes_atomically_and_replays_without_rewrite(tmp_path):
    observation = _observation()
    assert promote_current_display(tmp_path, observation) == "UPDATED"
    assert load_current_display(tmp_path) == observation
    assert promote_current_display(tmp_path, observation) == "NOOP_CURRENT"


def test_current_display_preserves_newer_prior_value(tmp_path):
    observation = _observation()
    promote_current_display(tmp_path, observation)
    older = replace(observation, retrieved_at_utc="2026-08-20T16:00:00+00:00")
    with pytest.raises(ValueError, match="must be newer"):
        promote_current_display(tmp_path, older)
    assert load_current_display(tmp_path) == observation


def test_current_display_rejects_non_updated_or_invalid_values(tmp_path):
    with pytest.raises(ValueError, match="validated promotion"):
        promote_current_display(tmp_path, replace(_observation(), refresh_status="FAILED"))
    with pytest.raises(ValueError, match="positive"):
        promote_current_display(tmp_path, replace(_observation(), value=0))


def test_dashboard_current_batch_is_atomic_and_identity_scoped(tmp_path):
    observation = DashboardCurrentObservation(
        identity="SP500", value=7684.13, unit="index points",
        source_date="2026-08-20",
        retrieved_at_utc="2026-08-20T16:50:44+00:00",
        provider="FinanceDataReader 0.9.202 / Yahoo daily",
        route="YAHOO:^GSPC",
    )
    assert promote_dashboard_current(tmp_path, [observation]) == "UPDATED"
    assert load_dashboard_current(tmp_path) == {"SP500": observation}
    assert promote_dashboard_current(tmp_path, [observation]) == "NOOP_CURRENT"
