from __future__ import annotations

from dataclasses import FrozenInstanceError

import numpy as np
import pandas as pd
import pytest

from market_backtest.execution import (
    EXECUTION_CLAIM,
    EXECUTION_CONTRACT_VERSION,
    EXECUTION_STATUS,
    NEXT_OPEN_V1,
    simulate_next_open_execution,
)


def market(
    opens: tuple[float, ...] = (100.0, 110.0, 90.0, 120.0),
    closes: tuple[float, ...] = (105.0, 100.0, 100.0, 125.0),
) -> pd.DataFrame:
    return pd.DataFrame({
        "session_date": pd.bdate_range("2020-01-02", periods=len(opens)).strftime(
            "%Y-%m-%d"
        ),
        "open": opens,
        "close": closes,
        "instrument_id": "KRX:069500",
        "currency": "KRW",
    })


def decisions(
    frame: pd.DataFrame,
    indexes: tuple[int, ...] = (0, 2),
    targets: tuple[bool, ...] = (True, False),
) -> pd.DataFrame:
    return pd.DataFrame({
        "decision_session": frame["session_date"].iloc[list(indexes)].tolist(),
        "target_long": pd.Series(targets, dtype="bool"),
    })


def test_result_is_typed_immutable_versioned_and_candid_about_fill_assumption():
    frame = market()
    result = simulate_next_open_execution(frame, decisions(frame))

    assert result.contract_version == EXECUTION_CONTRACT_VERSION
    assert result.status == EXECUTION_STATUS == "DEVELOPMENT_ONLY_EXECUTION_MODEL"
    assert result.execution_claim == EXECUTION_CLAIM
    assert result.assumptions == NEXT_OPEN_V1
    assert result.instrument_id == "KRX:069500"
    assert result.currency == "KRW"
    assert isinstance(result.ledger, tuple)
    with pytest.raises(FrozenInstanceError):
        result.ledger[1].cash = 2.0


def test_close_decision_fills_only_at_exact_next_retained_session_open():
    frame = market()
    result = simulate_next_open_execution(frame, decisions(frame, (0,), (True,)))

    observation, fill = result.ledger[:2]
    assert observation.trade_side == "NONE"
    assert observation.session_date == "2020-01-02"
    assert fill.session_date == "2020-01-03"
    assert fill.decision_session == observation.session_date
    assert fill.trade_side == "BUY"
    assert fill.fill_price == 110.0
    assert fill.units == pytest.approx(1.0 / 1.001 / 110.0)
    assert fill.nav_close == pytest.approx(1.0 / 1.001 * 100.0 / 110.0)


def test_long_cash_round_trip_charges_cost_and_conserves_every_fill():
    frame = market()
    result = simulate_next_open_execution(frame, decisions(frame))

    assert [row.trade_side for row in result.ledger] == [
        "NONE", "BUY", "NONE", "SELL",
    ]
    assert result.metrics.trade_count == 2
    for row in result.ledger:
        assert row.nav_post_trade == pytest.approx(
            row.nav_pre_trade - row.transaction_cost
        )
        assert row.nav_close == pytest.approx(row.cash + row.asset_value_close)
        if row.trade_side != "NONE":
            assert row.transaction_cost / row.trade_notional == pytest.approx(0.001)
            assert row.turnover == pytest.approx(
                row.trade_notional / row.nav_pre_trade
            )


def test_metrics_include_drawdown_volatility_turnover_and_long_exposure():
    frame = market()
    result = simulate_next_open_execution(frame, decisions(frame))
    returns = pd.Series([row.net_return for row in result.ledger[1:]])

    assert result.metrics.max_drawdown == min(
        row.drawdown for row in result.ledger
    )
    assert result.metrics.total_turnover == pytest.approx(
        sum(row.turnover for row in result.ledger)
    )
    assert result.metrics.average_long_exposure == pytest.approx(0.5)
    assert result.metrics.annualized_volatility == pytest.approx(
        returns.std(ddof=1) * (252.0 ** 0.5)
    )
    assert result.metrics.annualized_return == pytest.approx(
        result.metrics.ending_nav ** (252.0 / 3.0) - 1.0
    )


def test_sparse_decisions_hold_the_prior_target_without_phantom_trades():
    frame = market()
    result = simulate_next_open_execution(frame, decisions(frame, (0,), (True,)))

    assert [row.target_position for row in result.ledger] == [0, 1, 1, 1]
    assert result.metrics.trade_count == 1
    assert result.ledger[2].trade_side == "NONE"
    assert result.ledger[2].decision_session is None


def test_empty_typed_decision_set_is_a_valid_all_cash_scenario():
    frame = market()
    instruction_frame = pd.DataFrame({
        "decision_session": pd.Series(dtype="object"),
        "target_long": pd.Series(dtype="bool"),
    })

    result = simulate_next_open_execution(frame, instruction_frame)

    assert result.metrics.ending_nav == 1.0
    assert result.metrics.trade_count == 0
    assert all(row.target_position == 0 for row in result.ledger)


def test_simulation_is_deterministic_and_does_not_mutate_inputs():
    frame = market()
    instruction_frame = decisions(frame)
    original_market = frame.copy(deep=True)
    original_decisions = instruction_frame.copy(deep=True)

    first = simulate_next_open_execution(frame, instruction_frame)
    second = simulate_next_open_execution(frame, instruction_frame)

    assert first == second
    pd.testing.assert_frame_equal(frame, original_market)
    pd.testing.assert_frame_equal(instruction_frame, original_decisions)


@pytest.mark.parametrize("field", ["open", "close"])
@pytest.mark.parametrize("bad", [0.0, -1.0, float("nan"), float("inf")])
def test_invalid_prices_fail_closed(field, bad):
    frame = market()
    frame.loc[1, field] = bad

    with pytest.raises(ValueError, match=field):
        simulate_next_open_execution(frame, decisions(frame))


def test_duplicate_unsorted_or_missing_market_sessions_fail_closed():
    duplicate = market()
    duplicate.loc[1, "session_date"] = duplicate.loc[0, "session_date"]
    with pytest.raises(ValueError, match="unique and sorted"):
        simulate_next_open_execution(duplicate, decisions(market()))

    missing = market()
    instruction_frame = decisions(missing, (0,), (True,))
    instruction_frame.loc[0, "decision_session"] = "2020-01-01"
    with pytest.raises(ValueError, match="exact retained"):
        simulate_next_open_execution(missing, instruction_frame)


def test_last_session_decision_is_rejected_instead_of_same_day_or_no_fill():
    frame = market()
    instruction_frame = decisions(frame, (3,), (True,))

    with pytest.raises(ValueError, match="no next retained session"):
        simulate_next_open_execution(frame, instruction_frame)


def test_outcome_columns_are_forbidden_from_decision_boundary():
    frame = market()
    instruction_frame = decisions(frame)
    instruction_frame["forward_return_20d"] = 0.5

    with pytest.raises(ValueError, match="outcome namespace"):
        simulate_next_open_execution(frame, instruction_frame)


def test_seeded_252_session_stress_path_preserves_accounting_and_finite_nav():
    generator = np.random.default_rng(20260826)
    dates = pd.bdate_range("2019-01-02", periods=252)
    overnight = generator.normal(0.0, 0.012, len(dates))
    intraday = generator.normal(0.0, 0.018, len(dates))
    opens = 100.0 * np.exp(np.cumsum(overnight))
    closes = opens * np.exp(intraday)
    frame = pd.DataFrame({
        "session_date": dates.strftime("%Y-%m-%d"),
        "open": opens,
        "close": closes,
        "instrument_id": "SYNTHETIC:STRESS",
        "currency": "KRW",
    })
    indexes = tuple(range(0, 250, 10))
    instruction_frame = pd.DataFrame({
        "decision_session": frame["session_date"].iloc[list(indexes)].tolist(),
        "target_long": pd.Series(
            [index % 20 == 0 for index in indexes], dtype="bool",
        ),
    })

    result = simulate_next_open_execution(frame, instruction_frame)

    assert result.metrics.observations == 252
    assert result.metrics.trade_count == len(indexes)
    assert np.isfinite(result.metrics.ending_nav)
    assert result.metrics.ending_nav > 0.0
    for row in result.ledger:
        assert np.isfinite(row.nav_close)
        assert row.cash >= 0.0
        assert row.units >= 0.0
        assert row.nav_post_trade == pytest.approx(
            row.nav_pre_trade - row.transaction_cost
        )
