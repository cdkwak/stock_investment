"""Deterministic next-session-open execution accounting.

The engine is deliberately I/O-free.  It models a single executable instrument
and cash, but it does not claim that historical open prices were obtainable in
size.  Decisions observed after a retained session close can only fill at the
next retained session open.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as calendar_date
from math import isclose, isfinite, sqrt
import re

import numpy as np
import pandas as pd


EXECUTION_CONTRACT_VERSION = "historical-next-open/v1"
EXECUTION_STATUS = "DEVELOPMENT_ONLY_EXECUTION_MODEL"
EXECUTION_CLAIM = "NEXT_RETAINED_SESSION_OPEN_ASSUMPTION"

_DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")
_CURRENCY_PATTERN = re.compile(r"[A-Z]{3}")
_FORBIDDEN_DECISION_COLUMNS = frozenset({
    "forward_return", "forward_return_5d", "forward_return_20d",
    "future_close", "future_open", "label", "outcome",
})


@dataclass(frozen=True, slots=True)
class ExecutionAssumptions:
    initial_cash: float = 1.0
    one_way_cost_bps: float = 10.0
    annualization_sessions: int = 252
    fill_price: str = "NEXT_RETAINED_SESSION_OPEN"
    long_only: bool = True
    leverage_allowed: bool = False
    fractional_units: bool = True
    dividends_included: bool = False
    taxes_included: bool = False
    financing_included: bool = False
    volume_capacity_modelled: bool = False

    def __post_init__(self) -> None:
        numeric = (self.initial_cash, self.one_way_cost_bps)
        if (
            any(type(value) is not float or not isfinite(value) for value in numeric)
            or self.initial_cash <= 0.0
            or not 0.0 <= self.one_way_cost_bps < 10_000.0
            or type(self.annualization_sessions) is not int
            or self.annualization_sessions < 1
            or self.fill_price != "NEXT_RETAINED_SESSION_OPEN"
            or self.long_only is not True
            or self.leverage_allowed is not False
            or self.fractional_units is not True
            or self.dividends_included is not False
            or self.taxes_included is not False
            or self.financing_included is not False
            or self.volume_capacity_modelled is not False
        ):
            raise ValueError("execution assumptions are invalid")


NEXT_OPEN_V1 = ExecutionAssumptions()


@dataclass(frozen=True, slots=True)
class ExecutionLedgerRow:
    session_date: str
    open: float
    close: float
    decision_session: str | None
    position_before: int
    target_position: int
    trade_side: str
    fill_price: float | None
    trade_notional: float
    transaction_cost: float
    cash: float
    units: float
    asset_value_close: float
    nav_pre_trade: float
    nav_post_trade: float
    nav_close: float
    turnover: float
    net_return: float
    drawdown: float


@dataclass(frozen=True, slots=True)
class ExecutionMetrics:
    observations: int
    initial_cash: float
    ending_nav: float
    total_return: float
    annualized_return: float
    annualized_volatility: float
    max_drawdown: float
    trade_count: int
    total_turnover: float
    average_long_exposure: float
    transaction_cost_paid: float


@dataclass(frozen=True, slots=True)
class ExecutionSimulation:
    contract_version: str
    status: str
    execution_claim: str
    instrument_id: str
    currency: str
    assumptions: ExecutionAssumptions
    ledger: tuple[ExecutionLedgerRow, ...]
    metrics: ExecutionMetrics


def _canonical_date(value: object, *, artifact: str) -> str:
    if type(value) is not str or _DATE_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{artifact} session date is invalid")
    try:
        parsed = calendar_date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{artifact} session date is invalid") from error
    if parsed.isoformat() != value:
        raise ValueError(f"{artifact} session date is invalid")
    return value


def _validate_frame(
    frame: pd.DataFrame,
    *,
    required: frozenset[str],
    artifact: str,
    allow_empty: bool = False,
) -> None:
    if not isinstance(frame, pd.DataFrame) or (frame.empty and not allow_empty):
        raise ValueError(f"{artifact} schema/content is invalid")
    columns = frame.columns.tolist()
    if (
        frame.columns.has_duplicates
        or any(type(column) is not str or not column for column in columns)
        or not required.issubset(columns)
    ):
        raise ValueError(f"{artifact} schema/content is invalid")


def _ordered_dates(series: pd.Series, *, artifact: str) -> tuple[str, ...]:
    dates = tuple(_canonical_date(value, artifact=artifact) for value in series)
    if len(set(dates)) != len(dates) or any(
        current >= following for current, following in zip(dates, dates[1:])
    ):
        raise ValueError(f"{artifact} session dates must be unique and sorted")
    return dates


def _prices(series: pd.Series, *, field: str) -> tuple[float, ...]:
    if (
        pd.api.types.is_bool_dtype(series.dtype)
        or pd.api.types.is_complex_dtype(series.dtype)
        or not pd.api.types.is_numeric_dtype(series.dtype)
    ):
        raise ValueError(f"market {field} must be real numeric, finite, and positive")
    values = series.to_numpy(dtype="float64", na_value=np.nan)
    if not np.isfinite(values).all() or (values <= 0.0).any():
        raise ValueError(f"market {field} must be real numeric, finite, and positive")
    return tuple(float(value) for value in values)


def _exact_identity(series: pd.Series, *, field: str) -> str:
    values = series.tolist()
    if not values or any(
        type(value) is not str or not value or value != value.strip()
        for value in values
    ):
        raise ValueError(f"market {field} identity is invalid")
    first = values[0]
    if any(value != first for value in values):
        raise ValueError(f"market {field} identity must be constant")
    return first


def _decisions(
    decisions: pd.DataFrame, market_dates: tuple[str, ...],
) -> dict[str, bool]:
    forbidden = {
        column for column in decisions.columns
        if column in _FORBIDDEN_DECISION_COLUMNS
        or column.startswith(("forward_", "future_", "label_", "outcome_"))
    }
    if forbidden:
        raise ValueError(f"outcome namespace is forbidden in decisions: {sorted(forbidden)}")
    decision_dates = _ordered_dates(
        decisions["decision_session"], artifact="decision",
    )
    targets = decisions["target_long"]
    if not pd.api.types.is_bool_dtype(targets.dtype) or targets.isna().any():
        raise ValueError("decision target_long must be non-null boolean")
    market_index = {value: index for index, value in enumerate(market_dates)}
    for decision_date in decision_dates:
        index = market_index.get(decision_date)
        if index is None:
            raise ValueError("decision session must be an exact retained market session")
        if index == len(market_dates) - 1:
            raise ValueError("decision session has no next retained session to fill")
    return dict(zip(decision_dates, (bool(value) for value in targets), strict=True))


def _trade(
    *, cash: float, units: float, price: float, target: int, cost_rate: float,
) -> tuple[str, float, float, float, float]:
    position = int(units > 0.0)
    if target == position:
        return "NONE", 0.0, 0.0, cash, units
    if position == 0 and target == 1:
        notional = cash / (1.0 + cost_rate)
        cost = notional * cost_rate
        return "BUY", notional, cost, 0.0, notional / price
    if position == 1 and target == 0:
        notional = units * price
        cost = notional * cost_rate
        return "SELL", notional, cost, cash + notional - cost, 0.0
    raise RuntimeError("execution position transition is invalid")


def simulate_next_open_execution(
    market: pd.DataFrame,
    decisions: pd.DataFrame,
    assumptions: ExecutionAssumptions = NEXT_OPEN_V1,
) -> ExecutionSimulation:
    """Simulate long/cash instructions without same-session execution."""
    if type(assumptions) is not ExecutionAssumptions:
        raise ValueError("only typed execution assumptions are supported")
    ExecutionAssumptions.__post_init__(assumptions)
    _validate_frame(
        market,
        required=frozenset({
            "session_date", "open", "close", "instrument_id", "currency",
        }),
        artifact="market",
    )
    _validate_frame(
        decisions,
        required=frozenset({"decision_session", "target_long"}),
        artifact="decision",
        allow_empty=True,
    )
    market_dates = _ordered_dates(market["session_date"], artifact="market")
    opens = _prices(market["open"], field="open")
    closes = _prices(market["close"], field="close")
    instrument_id = _exact_identity(market["instrument_id"], field="instrument_id")
    currency = _exact_identity(market["currency"], field="currency")
    if _CURRENCY_PATTERN.fullmatch(currency) is None:
        raise ValueError("market currency identity is invalid")
    targets_by_date = _decisions(decisions, market_dates)

    cash = assumptions.initial_cash
    units = 0.0
    target = 0
    cost_rate = assumptions.one_way_cost_bps / 10_000.0
    rows: list[ExecutionLedgerRow] = []
    total_cost = 0.0
    trade_count = 0
    running_peak = assumptions.initial_cash

    for index, (session_date, open_price, close_price) in enumerate(
        zip(market_dates, opens, closes, strict=True)
    ):
        position_before = int(units > 0.0)
        nav_pre_trade = cash + units * open_price
        decision_session = market_dates[index - 1] if index else None
        pending = targets_by_date.get(decision_session) if decision_session else None
        if pending is not None:
            target = int(pending)
        side, notional, cost, cash, units = _trade(
            cash=cash,
            units=units,
            price=open_price,
            target=target,
            cost_rate=cost_rate,
        )
        nav_post_trade = cash + units * open_price
        asset_value_close = units * close_price
        nav_close = cash + asset_value_close
        prior_nav = rows[-1].nav_close if rows else assumptions.initial_cash
        try:
            turnover = notional / nav_pre_trade
            net_return = nav_close / prior_nav - 1.0
            running_peak = max(running_peak, nav_close)
            drawdown = nav_close / running_peak - 1.0
        except (OverflowError, ZeroDivisionError):
            raise ValueError("execution derived values must remain finite") from None
        values = (
            nav_pre_trade, notional, cost, cash, units, nav_post_trade,
            asset_value_close, nav_close, turnover, net_return, running_peak,
            drawdown,
        )
        if not all(isfinite(value) for value in values) or any(
            value < 0.0 for value in (
                nav_pre_trade, notional, cost, cash, units, nav_post_trade,
                asset_value_close, nav_close, turnover, running_peak,
            )
        ):
            raise ValueError(
                "execution derived values must remain finite and non-negative"
            )
        if not isclose(
            nav_post_trade,
            nav_pre_trade - cost,
            rel_tol=1e-12,
            abs_tol=1e-15,
        ):
            raise RuntimeError("execution self-financing conservation failed")
        if side != "NONE":
            trade_count += 1
            total_cost += cost
        rows.append(ExecutionLedgerRow(
            session_date=session_date,
            open=open_price,
            close=close_price,
            decision_session=decision_session if pending is not None else None,
            position_before=position_before,
            target_position=target,
            trade_side=side,
            fill_price=open_price if side != "NONE" else None,
            trade_notional=notional,
            transaction_cost=cost,
            cash=cash,
            units=units,
            asset_value_close=asset_value_close,
            nav_pre_trade=nav_pre_trade,
            nav_post_trade=nav_post_trade,
            nav_close=nav_close,
            turnover=turnover,
            net_return=net_return,
            drawdown=drawdown,
        ))

    ending_nav = rows[-1].nav_close
    intervals = len(rows) - 1
    if intervals:
        try:
            annualized_return = (
                (ending_nav / assumptions.initial_cash)
                ** (assumptions.annualization_sessions / intervals)
                - 1.0
            )
        except (OverflowError, ZeroDivisionError):
            raise ValueError("execution metrics must remain finite") from None
        returns = np.asarray(
            [row.net_return for row in rows[1:]], dtype="float64",
        )
        with np.errstate(over="ignore", invalid="ignore"):
            annualized_volatility = (
                float(
                    returns.std(ddof=1)
                    * sqrt(assumptions.annualization_sessions)
                )
                if intervals >= 2
                else 0.0
            )
    else:
        annualized_return = 0.0
        annualized_volatility = 0.0
    metric_values = (
        ending_nav,
        annualized_return,
        annualized_volatility,
        total_cost,
        *(row.turnover for row in rows),
        *(row.drawdown for row in rows),
    )
    if not all(isfinite(value) for value in metric_values):
        raise ValueError("execution metrics must remain finite")
    return ExecutionSimulation(
        contract_version=EXECUTION_CONTRACT_VERSION,
        status=EXECUTION_STATUS,
        execution_claim=EXECUTION_CLAIM,
        instrument_id=instrument_id,
        currency=currency,
        assumptions=assumptions,
        ledger=tuple(rows),
        metrics=ExecutionMetrics(
            observations=len(rows),
            initial_cash=assumptions.initial_cash,
            ending_nav=ending_nav,
            total_return=ending_nav / assumptions.initial_cash - 1.0,
            annualized_return=annualized_return,
            annualized_volatility=annualized_volatility,
            max_drawdown=min(row.drawdown for row in rows),
            trade_count=trade_count,
            total_turnover=sum(row.turnover for row in rows),
            average_long_exposure=(
                sum(row.target_position for row in rows) / len(rows)
            ),
            transaction_cost_paid=total_cost,
        ),
    )


__all__ = [
    "EXECUTION_CLAIM",
    "EXECUTION_CONTRACT_VERSION",
    "EXECUTION_STATUS",
    "NEXT_OPEN_V1",
    "ExecutionAssumptions",
    "ExecutionLedgerRow",
    "ExecutionMetrics",
    "ExecutionSimulation",
    "simulate_next_open_execution",
]
