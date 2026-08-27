"""Pure development-only KOSPI200 final-close portfolio accounting.

This module performs no I/O and makes no executable-instrument claim.  It is a
strict accounting projection for the retained KOSPI200 index close proxy only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as calendar_date
from math import isclose, isfinite, sqrt
import re

import numpy as np
import pandas as pd

from .holdout import CoverageHoldout
from .labels import LABEL_NAMESPACE


PORTFOLIO_STATUS = "DEVELOPMENT_ONLY_CLOSE_PROXY"
INSTRUMENT_CLAIM = "NOT_EXECUTABLE_INSTRUMENT"

_SOURCE_DATASET = "kr_kospi200_index_daily"
_SOURCE_CONTRACT_VERSION = 1
_TICKER = "1028"
_DATE_SEMANTICS = "KRX_TRADING_DATE_DAILY_FINAL"
_PIT_STATUS = "PIT_SAFE_EOD_T_PLUS_1"
_DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")
_USABLE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T09:00:00\+09:00")
_FORBIDDEN_OUTCOME_COLUMNS = frozenset({
    *LABEL_NAMESPACE,
    "mae_20d",
    "mfe_20d",
})

KOSPI200_FROZEN_HOLDOUT_V1 = CoverageHoldout(
    policy_id="UNTOUCHED_FINAL_5_CALENDAR_YEARS",
    coverage_start="1990-01-03",
    coverage_end="2026-08-14",
    holdout_start="2021-08-17",
    development_observations=8225,
    holdout_observations=1222,
    results_reviewed=False,
)


@dataclass(frozen=True, slots=True)
class PortfolioAssumptions:
    initial_nav: float = 1.0
    long_position: int = 1
    cash_position: int = 0
    cash_yield_rate: float = 0.0
    one_way_transaction_cost_rate: float = 0.001
    annualization_sessions: int = 252
    execution_price: str = "RETAINED_DAILY_FINAL_CLOSE_PROXY"
    timing_policy: str = (
        "T_CLOSE_SIGNAL_T_PLUS_1_0900_USABLE_EXECUTE_T_PLUS_1_FINAL_CLOSE"
    )
    leverage_allowed: bool = False
    shorting_allowed: bool = False
    forced_liquidation: bool = False

    def __post_init__(self) -> None:
        if (
            type(self.initial_nav) is not float
            or type(self.long_position) is not int
            or type(self.cash_position) is not int
            or type(self.cash_yield_rate) is not float
            or type(self.one_way_transaction_cost_rate) is not float
            or type(self.annualization_sessions) is not int
            or type(self.execution_price) is not str
            or type(self.timing_policy) is not str
            or type(self.leverage_allowed) is not bool
            or type(self.shorting_allowed) is not bool
            or type(self.forced_liquidation) is not bool
        ):
            raise ValueError("portfolio assumptions are invalid")
        if (
            not all(isfinite(value) for value in (
                self.initial_nav,
                self.cash_yield_rate,
                self.one_way_transaction_cost_rate,
            ))
            or self.initial_nav <= 0.0
            or self.cash_yield_rate != 0.0
            or not 0.0 <= self.one_way_transaction_cost_rate < 1.0
            or self.annualization_sessions < 1
            or self.long_position != 1
            or self.cash_position != 0
            or not self.execution_price
            or not self.timing_policy
            or any((
                self.leverage_allowed,
                self.shorting_allowed,
                self.forced_liquidation,
            ))
        ):
            raise ValueError("portfolio assumptions are invalid")


CLOSE_PROXY_V1 = PortfolioAssumptions()


@dataclass(frozen=True, slots=True)
class PortfolioLedgerRow:
    date: str
    close: float
    signal_observation_date: str | None
    usable_from: str | None
    risk_off_signal: bool | None
    position_before: int
    target_position: int
    market_return: float
    gross_portfolio_return: float
    trade_notional: float
    turnover: float
    transaction_cost: float
    cash: float
    units: float
    asset_value: float
    nav_before_cost: float
    nav: float
    net_return: float
    drawdown: float


@dataclass(frozen=True, slots=True)
class PortfolioMetrics:
    observations: int
    intervals: int
    initial_nav: float
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
class PortfolioSimulation:
    status: str
    instrument_claim: str
    source_dataset: str
    source_contract_version: int
    holdout_policy_id: str
    holdout_start: str
    assumptions: PortfolioAssumptions
    ledger: tuple[PortfolioLedgerRow, ...]
    metrics: PortfolioMetrics


def _validate_frame_schema(
    frame: pd.DataFrame, *, required: frozenset[str], artifact: str,
) -> None:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise ValueError(f"{artifact} schema/content is invalid")
    columns = frame.columns.tolist()
    if (
        any(not isinstance(column, str) or not column for column in columns)
        or frame.columns.has_duplicates
        or not required.issubset(columns)
    ):
        raise ValueError(f"{artifact} schema/content is invalid")
    forbidden = {
        column for column in columns
        if column in _FORBIDDEN_OUTCOME_COLUMNS
        or column.startswith(("forward_", "label_", "outcome_"))
    }
    if forbidden:
        raise ValueError(
            f"outcome namespace is forbidden in {artifact}: {sorted(forbidden)}"
        )


def _canonical_date(value: object, *, artifact: str) -> str:
    if type(value) is not str or _DATE_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{artifact} date key is invalid")
    try:
        parsed = calendar_date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{artifact} date key is invalid") from error
    if parsed.isoformat() != value:
        raise ValueError(f"{artifact} date key is invalid")
    return value


def _canonical_dates(series: pd.Series, *, artifact: str) -> tuple[str, ...]:
    dates = tuple(_canonical_date(value, artifact=artifact) for value in series)
    if len(set(dates)) != len(dates) or any(
        current >= following for current, following in zip(dates, dates[1:])
    ):
        raise ValueError(f"{artifact} dates must be unique and sorted")
    return dates


def _validate_holdout(policy: CoverageHoldout) -> tuple[str, str, str]:
    if (
        type(policy) is not CoverageHoldout
        or type(policy.results_reviewed) is not bool
        or policy.results_reviewed is not False
    ):
        raise ValueError("an exact untouched CoverageHoldout is required")
    if type(policy.policy_id) is not str or not policy.policy_id.strip():
        raise ValueError("holdout policy identity is invalid")
    coverage_start = _canonical_date(
        policy.coverage_start, artifact="holdout coverage_start",
    )
    coverage_end = _canonical_date(
        policy.coverage_end, artifact="holdout coverage_end",
    )
    holdout_start = _canonical_date(
        policy.holdout_start, artifact="holdout holdout_start",
    )
    counts = (policy.development_observations, policy.holdout_observations)
    if (
        any(type(value) is not int for value in counts)
        or min(counts) < 1
        or not coverage_start < holdout_start <= coverage_end
    ):
        raise ValueError("holdout policy coverage is invalid")
    if policy != KOSPI200_FROZEN_HOLDOUT_V1:
        raise ValueError("holdout policy differs from fixed KOSPI200 frozen slice")
    return coverage_start, coverage_end, holdout_start


def _require_exact_text(
    series: pd.Series, expected: str, *, artifact: str, field: str,
) -> None:
    if not series.map(lambda value: type(value) is str).all() or not series.eq(
        expected
    ).all():
        raise ValueError(f"{artifact} {field} must equal {expected}")


def _require_exact_integer(
    series: pd.Series, expected: int, *, artifact: str, field: str,
) -> None:
    if (
        pd.api.types.is_bool_dtype(series.dtype)
        or not pd.api.types.is_integer_dtype(series.dtype)
        or series.isna().any()
        or not series.eq(expected).all()
    ):
        raise ValueError(f"{artifact} {field} must equal integer {expected}")


def _validated_closes(series: pd.Series) -> tuple[float, ...]:
    if (
        pd.api.types.is_bool_dtype(series.dtype)
        or pd.api.types.is_complex_dtype(series.dtype)
        or not pd.api.types.is_numeric_dtype(series.dtype)
    ):
        raise ValueError("price close must be real numeric, finite, and positive")
    values = series.to_numpy(dtype="float64", na_value=np.nan)
    if not np.isfinite(values).all() or (values <= 0.0).any():
        raise ValueError("price close must be real numeric, finite, and positive")
    return tuple(float(value) for value in values)


def _validated_signal_decisions(signals: pd.DataFrame) -> tuple[bool, ...]:
    decisions = signals["risk_off_signal"]
    if not pd.api.types.is_bool_dtype(decisions.dtype) or decisions.isna().any():
        raise ValueError("signal risk_off_signal must be non-null boolean")
    scores = signals["risk_score"]
    if (
        pd.api.types.is_bool_dtype(scores.dtype)
        or not pd.api.types.is_integer_dtype(scores.dtype)
        or scores.isna().any()
        or scores.lt(0).any()
        or scores.gt(4).any()
    ):
        raise ValueError("signal risk_score must be non-null integer in [0, 4]")
    return tuple(bool(value) for value in decisions)


def _validated_usable_clocks(series: pd.Series) -> tuple[str, ...]:
    clocks: list[str] = []
    for value in series:
        if type(value) is not str or _USABLE_PATTERN.fullmatch(value) is None:
            raise ValueError(
                "signal usable_from must be exact T+1 09:00:00+09:00"
            )
        _canonical_date(value[:10], artifact="signal usable_from")
        clocks.append(value)
    return tuple(clocks)


def _validate_source_contracts(
    prices: pd.DataFrame, signals: pd.DataFrame,
) -> None:
    for frame, artifact in ((prices, "price"), (signals, "signal")):
        _require_exact_text(
            frame["ticker"], _TICKER, artifact=artifact, field="ticker",
        )
        _require_exact_text(
            frame["date_semantics"], _DATE_SEMANTICS,
            artifact=artifact, field="date_semantics",
        )
    _require_exact_text(
        signals["source_dataset"], _SOURCE_DATASET,
        artifact="signal", field="source_dataset",
    )
    _require_exact_integer(
        signals["source_contract_version"], _SOURCE_CONTRACT_VERSION,
        artifact="signal", field="source_contract_version",
    )
    _require_exact_text(
        signals["pit_status"], _PIT_STATUS,
        artifact="signal", field="pit_status",
    )
    _require_exact_integer(
        signals["signal_version"], 1,
        artifact="signal", field="signal_version",
    )


def _aligned_execution_dates(
    price_dates: tuple[str, ...],
    signal_dates: tuple[str, ...],
    usable_clocks: tuple[str, ...],
) -> tuple[int, tuple[str, ...]]:
    try:
        first_signal = price_dates.index(signal_dates[0])
    except ValueError as error:
        raise ValueError("signal/price retained-date alignment differs") from error
    if tuple(price_dates[first_signal:-1]) != signal_dates:
        raise ValueError(
            "signals must be a contiguous retained-price suffix with one next close"
        )
    execution_dates = tuple(price_dates[first_signal + 1:])
    expected_clocks = tuple(
        f"{execution_date}T09:00:00+09:00"
        for execution_date in execution_dates
    )
    if usable_clocks != expected_clocks:
        raise ValueError("signal usable_from differs from exact next retained date")
    return first_signal, execution_dates


def _ledger(
    *,
    price_dates: tuple[str, ...],
    closes: tuple[float, ...],
    signal_dates: tuple[str, ...],
    usable_clocks: tuple[str, ...],
    risk_off: tuple[bool, ...],
    first_signal: int,
    assumptions: PortfolioAssumptions,
) -> tuple[PortfolioLedgerRow, ...]:
    initial_close = closes[first_signal]
    rows = [PortfolioLedgerRow(
        date=price_dates[first_signal],
        close=initial_close,
        signal_observation_date=None,
        usable_from=None,
        risk_off_signal=None,
        position_before=assumptions.cash_position,
        target_position=assumptions.cash_position,
        market_return=0.0,
        gross_portfolio_return=0.0,
        trade_notional=0.0,
        turnover=0.0,
        transaction_cost=0.0,
        cash=assumptions.initial_nav,
        units=0.0,
        asset_value=0.0,
        nav_before_cost=assumptions.initial_nav,
        nav=assumptions.initial_nav,
        net_return=0.0,
        drawdown=0.0,
    )]
    running_peak = assumptions.initial_nav
    cost_rate = assumptions.one_way_transaction_cost_rate

    for offset, signal_date in enumerate(signal_dates):
        price_index = first_signal + offset + 1
        close = closes[price_index]
        prior_close = closes[price_index - 1]
        prior = rows[-1]
        position_before = prior.target_position
        cash_before = prior.cash
        units_before = prior.units
        try:
            asset_before = units_before * close
            nav_before_cost = cash_before + asset_before
            market_return = close / prior_close - 1.0
            gross_return = nav_before_cost / prior.nav - 1.0
        except (OverflowError, ZeroDivisionError):
            raise ValueError("portfolio derived values must remain finite") from None
        if (
            not all(isfinite(value) for value in (
                asset_before, nav_before_cost, market_return, gross_return,
                prior.nav,
            ))
            or nav_before_cost <= 0.0
            or prior.nav <= 0.0
        ):
            raise ValueError("portfolio derived values must remain finite")
        decision = risk_off[offset]
        target_position = (
            assumptions.cash_position if decision else assumptions.long_position
        )
        trade_notional = 0.0
        transaction_cost = 0.0
        cash = cash_before
        units = units_before

        if target_position != position_before:
            if (
                position_before == assumptions.cash_position
                and target_position == assumptions.long_position
            ):
                trade_notional = nav_before_cost / (1.0 + cost_rate)
                transaction_cost = trade_notional * cost_rate
                cash = 0.0
                units = trade_notional / close
            elif (
                position_before == assumptions.long_position
                and target_position == assumptions.cash_position
            ):
                trade_notional = asset_before
                transaction_cost = trade_notional * cost_rate
                cash = cash_before + trade_notional - transaction_cost
                units = 0.0
            else:
                raise RuntimeError("portfolio position transition is invalid")

        asset_value = units * close
        nav = cash + asset_value
        if (
            not all(isfinite(value) for value in (
                trade_notional, transaction_cost, cash, units,
                asset_value, nav_before_cost, nav,
            ))
            or nav <= 0.0
        ):
            raise ValueError("portfolio derived values must remain finite")
        if (
            any(value < 0.0 for value in (
                trade_notional, transaction_cost, cash, units,
                asset_value, nav_before_cost,
            ))
            or not isclose(
                nav,
                nav_before_cost - transaction_cost,
                rel_tol=1e-12,
                abs_tol=1e-15,
            )
        ):
            raise RuntimeError("portfolio self-financing conservation failed")
        try:
            turnover = trade_notional / nav_before_cost
            net_return = nav / prior.nav - 1.0
            running_peak = max(running_peak, nav)
            drawdown = nav / running_peak - 1.0
        except (OverflowError, ZeroDivisionError):
            raise ValueError("portfolio derived values must remain finite") from None
        if not all(isfinite(value) for value in (
            turnover, net_return, running_peak, drawdown,
        )):
            raise ValueError("portfolio derived values must remain finite")
        rows.append(PortfolioLedgerRow(
            date=price_dates[price_index],
            close=close,
            signal_observation_date=signal_date,
            usable_from=usable_clocks[offset],
            risk_off_signal=decision,
            position_before=position_before,
            target_position=target_position,
            market_return=market_return,
            gross_portfolio_return=gross_return,
            trade_notional=trade_notional,
            turnover=turnover,
            transaction_cost=transaction_cost,
            cash=cash,
            units=units,
            asset_value=asset_value,
            nav_before_cost=nav_before_cost,
            nav=nav,
            net_return=net_return,
            drawdown=drawdown,
        ))
    return tuple(rows)


def _metrics(
    ledger: tuple[PortfolioLedgerRow, ...],
    assumptions: PortfolioAssumptions,
) -> PortfolioMetrics:
    intervals = len(ledger) - 1
    ending_nav = ledger[-1].nav
    try:
        total_return = ending_nav / assumptions.initial_nav - 1.0
        annualized_return = (
            (ending_nav / assumptions.initial_nav)
            ** (assumptions.annualization_sessions / intervals)
            - 1.0
        )
    except (OverflowError, ZeroDivisionError):
        raise ValueError("portfolio metrics must remain finite") from None
    returns = np.asarray(
        [row.net_return for row in ledger[1:]], dtype="float64",
    )
    with np.errstate(over="ignore", invalid="ignore"):
        annualized_volatility = (
            float(returns.std(ddof=1) * sqrt(assumptions.annualization_sessions))
            if intervals >= 2
            else 0.0
        )
    max_drawdown = min(row.drawdown for row in ledger)
    total_turnover = sum(row.turnover for row in ledger)
    average_long_exposure = (
        sum(row.position_before for row in ledger[1:]) / intervals
    )
    transaction_cost_paid = sum(row.transaction_cost for row in ledger)
    if not all(isfinite(value) for value in (
        ending_nav,
        total_return,
        annualized_return,
        annualized_volatility,
        max_drawdown,
        total_turnover,
        average_long_exposure,
        transaction_cost_paid,
    )):
        raise ValueError("portfolio metrics must remain finite")
    return PortfolioMetrics(
        observations=len(ledger),
        intervals=intervals,
        initial_nav=assumptions.initial_nav,
        ending_nav=ending_nav,
        total_return=total_return,
        annualized_return=annualized_return,
        annualized_volatility=annualized_volatility,
        max_drawdown=max_drawdown,
        trade_count=sum(row.trade_notional > 0.0 for row in ledger),
        total_turnover=total_turnover,
        average_long_exposure=average_long_exposure,
        transaction_cost_paid=transaction_cost_paid,
    )


def simulate_kospi200_risk_off_portfolio(
    development_prices: pd.DataFrame,
    development_signals: pd.DataFrame,
    holdout_policy: CoverageHoldout,
    assumptions: PortfolioAssumptions = CLOSE_PROXY_V1,
) -> PortfolioSimulation:
    """Return an immutable close-proxy ledger without inspecting holdout rows."""
    if type(assumptions) is not PortfolioAssumptions:
        raise ValueError("only the fixed CLOSE_PROXY_V1 assumptions are supported")
    PortfolioAssumptions.__post_init__(assumptions)
    if assumptions != CLOSE_PROXY_V1:
        raise ValueError("only the fixed CLOSE_PROXY_V1 assumptions are supported")
    coverage_start, coverage_end, holdout_start = _validate_holdout(holdout_policy)
    _validate_frame_schema(
        development_prices,
        required=frozenset({"date", "close", "ticker", "date_semantics"}),
        artifact="price",
    )
    _validate_frame_schema(
        development_signals,
        required=frozenset({
            "observation_date", "ticker", "date_semantics", "usable_from",
            "source_dataset", "source_contract_version", "pit_status",
            "risk_off_signal", "risk_score", "signal_version",
        }),
        artifact="signal",
    )

    # Dates are the only row values inspected before the holdout guard.  A row
    # at or beyond the boundary is rejected before close, signal, or outcome
    # content can be evaluated.
    price_dates = _canonical_dates(development_prices["date"], artifact="price")
    signal_dates = _canonical_dates(
        development_signals["observation_date"], artifact="signal",
    )
    if (
        price_dates[0] < coverage_start
        or price_dates[-1] > coverage_end
        or any(value >= holdout_start for value in price_dates)
        or any(value >= holdout_start for value in signal_dates)
    ):
        raise ValueError("portfolio inputs must remain before untouched holdout")

    _validate_source_contracts(development_prices, development_signals)
    closes = _validated_closes(development_prices["close"])
    risk_off = _validated_signal_decisions(development_signals)
    usable_clocks = _validated_usable_clocks(development_signals["usable_from"])
    first_signal, _execution_dates = _aligned_execution_dates(
        price_dates, signal_dates, usable_clocks,
    )
    ledger = _ledger(
        price_dates=price_dates,
        closes=closes,
        signal_dates=signal_dates,
        usable_clocks=usable_clocks,
        risk_off=risk_off,
        first_signal=first_signal,
        assumptions=assumptions,
    )
    return PortfolioSimulation(
        status=PORTFOLIO_STATUS,
        instrument_claim=INSTRUMENT_CLAIM,
        source_dataset=_SOURCE_DATASET,
        source_contract_version=_SOURCE_CONTRACT_VERSION,
        holdout_policy_id=holdout_policy.policy_id,
        holdout_start=holdout_start,
        assumptions=assumptions,
        ledger=ledger,
        metrics=_metrics(ledger, assumptions),
    )


__all__ = [
    "CLOSE_PROXY_V1",
    "INSTRUMENT_CLAIM",
    "KOSPI200_FROZEN_HOLDOUT_V1",
    "PORTFOLIO_STATUS",
    "PortfolioAssumptions",
    "PortfolioLedgerRow",
    "PortfolioMetrics",
    "PortfolioSimulation",
    "simulate_kospi200_risk_off_portfolio",
]
