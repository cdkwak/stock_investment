from datetime import date

import pytest

from stock_data.orchestration.core_daily_incremental import (
    IncrementalReadiness,
    plan_missing_finalized_dates,
    require_executable_gate,
)


def test_planner_uses_only_explicit_finalized_trading_dates():
    plan = plan_missing_finalized_dates(
        last_accepted=date(2026, 8, 12),
        latest_finalized=date(2026, 8, 14),
        accepted_trading_dates=[date(2026, 8, 12), date(2026, 8, 14), date(2026, 8, 13)],
        calls_per_date=2,
    )
    assert plan.missing_dates == (date(2026, 8, 13), date(2026, 8, 14))
    assert plan.expected_calls == 4


def test_planner_does_not_infer_weekdays_or_accept_future_dates():
    plan = plan_missing_finalized_dates(
        last_accepted=date(2026, 8, 7),
        latest_finalized=date(2026, 8, 14),
        accepted_trading_dates=[date(2026, 8, 10), date(2026, 8, 12)],
        calls_per_date=1,
    )
    assert plan.missing_dates == (date(2026, 8, 10), date(2026, 8, 12))
    with pytest.raises(ValueError, match="exceeds"):
        plan_missing_finalized_dates(
            last_accepted=date(2026, 8, 7), latest_finalized=date(2026, 8, 14),
            accepted_trading_dates=[date(2026, 8, 17)], calls_per_date=1,
        )


def test_live_gate_requires_ready_status_and_nonempty_plan():
    plan = plan_missing_finalized_dates(
        last_accepted=date(2026, 8, 12), latest_finalized=date(2026, 8, 14),
        accepted_trading_dates=[date(2026, 8, 13)], calls_per_date=1,
    )
    with pytest.raises(RuntimeError, match="NEW_INCREMENTAL_WRAPPER_NEEDED"):
        require_executable_gate(IncrementalReadiness.NEW_INCREMENTAL_WRAPPER_NEEDED, plan)
