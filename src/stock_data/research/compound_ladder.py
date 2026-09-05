"""Continuous-account core-equity drawdown ladder simulation.

Signals are observed at index close T and can first change the account at the
next retained session's close.  The implementation is provider-free, long-only,
and intentionally separate from broker/order code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
import pandas as pd

from .signals import BuySignalSpec, evaluate_buy_signal


FIT_END = pd.Timestamp("2015-12-31")
HOLDOUT_START = pd.Timestamp("2016-01-01")
TRADING_DAYS = 252
ExitVariant = Literal["a", "b60", "b120", "c", "d"]
GRID_REQUIRED_FIELDS: tuple[str, ...] = (
    "row_kind",
    "basket",
    "underlying",
    "drawdown_threshold",
    "disp60_threshold",
    "levels",
    "leverage_multiple",
    "base_exposure",
    "product_share_at_max",
    "effective_exposure_max",
    "exit",
    "cost_enabled",
    "fit",
    "holdout",
    "full",
)
_UNDECIDED = object()


def _require_negative_threshold(value: object, name: str) -> float:
    if value is _UNDECIDED or value is None:
        raise ValueError(
            f"{name} is undecided under rule ⑥; caller must pass it explicitly"
        )
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    result = float(value)
    if not -1.0 < result < 0.0:
        raise ValueError(f"{name} must be between -1 and 0")
    return result


def require_drawdown_threshold(value: object) -> float:
    return _require_negative_threshold(value, "drawdown_threshold")


def require_disp60_threshold(value: object) -> float:
    return _require_negative_threshold(value, "disp60_threshold")


def require_product_share_at_max(value: object) -> float:
    if value is _UNDECIDED or value is None:
        raise ValueError(
            "product_share_at_max is undecided under rule ⑥; caller must pass it explicitly"
        )
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value):
        raise ValueError("product_share_at_max must be a finite number")
    result = float(value)
    if not 0.0 <= result <= 1.0:
        raise ValueError("product_share_at_max must be in [0.0, 1.0]")
    return result


def require_levels(value: object) -> int:
    if value is _UNDECIDED or value is None:
        raise ValueError(
            "levels is undecided under rule ⑥; caller must pass it explicitly"
        )
    if isinstance(value, bool) or not isinstance(value, (int, np.integer)):
        raise ValueError("levels must be an integer")
    result = int(value)
    if result not in (1, 2, 3, 4):
        raise ValueError("levels must be 1, 2, 3, or 4")
    return result


def require_base_exposure(value: object) -> float:
    if value is _UNDECIDED or value is None:
        raise ValueError(
            "base_exposure is undecided under rule ⑥; caller must pass it explicitly"
        )
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value):
        raise ValueError("base_exposure must be a finite number")
    result = float(value)
    if not 0.0 <= result <= 3.0:
        raise ValueError("base_exposure must be in [0.0, 3.0]")
    return result


@dataclass(frozen=True, slots=True)
class LadderSpec:
    drawdown_threshold: float | object = _UNDECIDED
    disp60_threshold: float | object = _UNDECIDED
    product_share_at_max: float | object = _UNDECIDED
    levels: int | object = _UNDECIDED
    base_exposure: float | object = _UNDECIDED

    def __post_init__(self) -> None:
        require_drawdown_threshold(self.drawdown_threshold)
        require_disp60_threshold(self.disp60_threshold)
        require_product_share_at_max(self.product_share_at_max)
        require_levels(self.levels)
        require_base_exposure(self.base_exposure)


@dataclass(frozen=True, slots=True)
class SimulationResult:
    curve: pd.DataFrame
    trades: pd.DataFrame
    cycles: pd.DataFrame
    metrics: dict[str, dict[str, float | int | str | None]]
    effective_exposure_max: float


def ladder_levels(
    signals: pd.DataFrame,
    spec: LadderSpec,
    buy_signal_spec: BuySignalSpec | None = None,
) -> pd.DataFrame:
    """Map an explicit buy-signal candidate onto equal ladder rungs.

    Omitting ``buy_signal_spec`` is only a compatibility route for candidate B;
    its two thresholds still come explicitly from ``LadderSpec``.
    """

    signal_spec = buy_signal_spec or BuySignalSpec(
        kind="B",
        drawdown_threshold=float(spec.drawdown_threshold),
        disp60_threshold=float(spec.disp60_threshold),
    )
    evaluated = evaluate_buy_signal(signals, signal_spec)
    raw = evaluated["raw_score"]
    maximum = int(evaluated["max_score"].iloc[0])
    mapped = np.ceil(raw.astype("float64") * spec.levels / maximum)
    observed = mapped.round().astype("Int64").where(raw.notna(), pd.NA)
    executable = observed.shift(1)
    frame = evaluated.copy()
    frame["observed_level"] = observed
    frame["executable_level"] = executable
    frame["target_weight"] = (
        frame["executable_level"].astype("float64")
        / spec.levels
        * float(spec.product_share_at_max)
    )
    return frame


def _target_allocation(
    overlay_fraction: float,
    *,
    base_exposure: float,
    leverage_multiple: int,
    product_share_at_max: float,
) -> tuple[float, float, float]:
    """Return core weight, product weight, and effective market exposure."""

    overlay = min(max(float(overlay_fraction), 0.0), 1.0)
    base_product_fraction = _base_product_fraction(base_exposure, leverage_multiple)
    if product_share_at_max + 1e-14 < base_product_fraction:
        raise ValueError(
            "product_share_at_max must be at least the product weight implied by base_exposure"
        )
    if base_exposure <= 1.0:
        product_weight = overlay * product_share_at_max
        core_weight = base_exposure * (1.0 - product_weight)
    else:
        product_weight = base_product_fraction + (
            product_share_at_max - base_product_fraction
        ) * overlay
        core_weight = 1.0 - product_weight
    exposure = core_weight + leverage_multiple * product_weight
    return core_weight, product_weight, exposure


def _validate_base_exposure(base_exposure: float, leverage_multiple: int) -> None:
    if base_exposure > leverage_multiple:
        raise ValueError("base_exposure must be less than or equal to leverage_multiple")


def effective_exposure_at_max(spec: LadderSpec, leverage_multiple: int) -> float:
    """Return core weight + leverage multiple × product weight at the top rung."""

    _validate_base_exposure(spec.base_exposure, leverage_multiple)
    return _target_allocation(
        1.0,
        base_exposure=spec.base_exposure,
        leverage_multiple=leverage_multiple,
        product_share_at_max=float(spec.product_share_at_max),
    )[2]


def _base_product_fraction(base_exposure: float, leverage_multiple: int) -> float:
    if base_exposure <= 1.0:
        return 0.0
    return (base_exposure - 1.0) / (leverage_multiple - 1.0)


def _overlay_progress_from_product_weight(
    product_weight: float,
    *,
    base_exposure: float,
    leverage_multiple: int,
    product_share_at_max: float,
) -> float:
    base_fraction = _base_product_fraction(base_exposure, leverage_multiple)
    span = product_share_at_max - base_fraction
    if span <= 0.0:
        return 1.0
    return min(max((product_weight - base_fraction) / span, 0.0), 1.0)


def _rebalance_assets(
    cash: float,
    core_units: float,
    product_units: float,
    core_price: float,
    product_price: float,
    target_core_weight: float,
    target_product_weight: float,
    cost_rate: float,
) -> tuple[float, float, float, float, float]:
    """Self-finance a two-asset rebalance and charge each traded leg."""

    core_value = core_units * core_price
    product_value = product_units * product_price
    wealth = cash + core_value + product_value
    if wealth <= 0.0:
        return cash, core_units, product_units, 0.0, 0.0
    if core_price <= 0.0:
        raise ValueError("core price must stay positive")
    if product_price <= 0.0 and target_product_weight > 0.0:
        target_core_weight = 1.0 if target_core_weight > 0.0 else 0.0
        target_product_weight = 0.0
    if (
        target_core_weight < 0.0
        or target_product_weight < 0.0
        or target_core_weight + target_product_weight > 1.0 + 1e-14
    ):
        raise ValueError("target asset weights must be non-negative and sum to at most 1")

    post_wealth = wealth
    for _ in range(32):
        target_core = target_core_weight * post_wealth
        target_product = target_product_weight * post_wealth
        notional = abs(target_core - core_value) + abs(target_product - product_value)
        updated = wealth - cost_rate * notional
        if abs(updated - post_wealth) <= 1e-15 * max(wealth, 1.0):
            post_wealth = updated
            break
        post_wealth = updated
    target_core = target_core_weight * post_wealth
    target_product = target_product_weight * post_wealth
    core_notional = abs(target_core - core_value)
    product_notional = abs(target_product - product_value)
    notional = core_notional + product_notional
    cost = cost_rate * notional
    cash_after = wealth - cost - target_core - target_product
    if abs(cash_after) <= 1e-14 * max(wealth, 1.0):
        cash_after = 0.0
    return (
        cash_after,
        target_core / core_price,
        target_product / product_price if product_price > 0.0 else 0.0,
        notional,
        cost,
    )


def _rebalance(
    cash: float,
    units: float,
    price: float,
    target_weight: float,
    cost_rate: float,
) -> tuple[float, float, float, float]:
    if price <= 0.0:
        # A daily-reset product that reaches zero is terminal.  Existing units
        # are worthless and no later signal can purchase a positive claim.
        return cash, 0.0, 0.0, 0.0
    asset = units * price
    wealth = cash + asset
    if wealth <= 0:
        return cash, units, 0.0, 0.0
    current_weight = asset / wealth
    if abs(target_weight - current_weight) <= 1e-14:
        return cash, units, 0.0, 0.0
    if target_weight > current_weight:
        target_asset = target_weight * (wealth + cost_rate * asset) / (1.0 + target_weight * cost_rate)
    else:
        denominator = 1.0 - target_weight * cost_rate
        target_asset = target_weight * (wealth - cost_rate * asset) / denominator
    target_asset = min(max(target_asset, 0.0), wealth)
    notional = abs(target_asset - asset)
    cost = cost_rate * notional
    wealth_after = wealth - cost
    cash_after = wealth_after - target_asset
    return cash_after, target_asset / price, notional, cost


def _period_stats(curve: pd.DataFrame, mask: pd.Series, *, label: str) -> dict[str, float | int | str | None]:
    part = curve.loc[mask].copy()
    if part.empty:
        return {
            "start": None,
            "end": None,
            "observations": 0,
            "final_wealth_multiple": float("nan"),
            "cagr": float("nan"),
            "max_drawdown": float("nan"),
            "trades": 0,
            "turnover": 0.0,
            "transaction_cost": 0.0,
        }
    starts_at_account_origin = int(part.index[0]) == int(curve.index[0])
    start_wealth = (
        float(part["wealth_before_trade"].iloc[0])
        if starts_at_account_origin and "wealth_before_trade" in part
        else float(part["wealth"].iloc[0])
    )
    end_wealth = float(part["wealth"].iloc[-1])
    multiple = end_wealth / start_wealth if start_wealth > 0 else (0.0 if end_wealth <= 0 else float("nan"))
    years = max((len(part) - 1) / TRADING_DAYS, 0.0)
    cagr = float(multiple ** (1.0 / years) - 1.0) if years > 0 and multiple > 0 else float("nan")
    if start_wealth <= 0:
        max_drawdown = -1.0
    else:
        normalized = part["wealth"] / start_wealth
        running_max = normalized.cummax()
        if starts_at_account_origin:
            running_max = running_max.clip(lower=1.0)
        drawdown = normalized / running_max.replace(0.0, 1.0) - 1.0
        max_drawdown = float(drawdown.min())
    return {
        "start": pd.Timestamp(part["date"].iloc[0]).strftime("%Y-%m-%d"),
        "end": pd.Timestamp(part["date"].iloc[-1]).strftime("%Y-%m-%d"),
        "observations": int(len(part)),
        "final_wealth_multiple": float(multiple),
        "cagr": cagr,
        "max_drawdown": max_drawdown,
        "trades": int(part["trade_notional"].gt(0).sum()),
        "turnover": float(part["trade_notional"].sum()),
        "transaction_cost": float(part["transaction_cost"].sum()),
        "period": label,
    }


def performance_metrics(curve: pd.DataFrame) -> dict[str, dict[str, float | int | str | None]]:
    dates = pd.to_datetime(curve["date"], errors="raise")
    return {
        "fit": _period_stats(curve, dates.le(FIT_END), label="fit"),
        "holdout": _period_stats(curve, dates.ge(HOLDOUT_START), label="holdout"),
        "full": _period_stats(curve, pd.Series(True, index=curve.index), label="full"),
    }


def _metrics_from_arrays(
    calendar: pd.DatetimeIndex,
    wealth: np.ndarray,
    trade_notional: np.ndarray,
    transaction_cost: np.ndarray,
) -> dict[str, dict[str, float | int | str | None]]:
    masks = {
        "fit": calendar <= FIT_END,
        "holdout": calendar >= HOLDOUT_START,
        "full": np.ones(len(calendar), dtype=bool),
    }
    result: dict[str, dict[str, float | int | str | None]] = {}
    for label, mask in masks.items():
        indices = np.flatnonzero(mask)
        if not len(indices):
            result[label] = {
                "start": None,
                "end": None,
                "observations": 0,
                "final_wealth_multiple": float("nan"),
                "cagr": float("nan"),
                "max_drawdown": float("nan"),
                "trades": 0,
                "turnover": 0.0,
                "transaction_cost": 0.0,
            }
            continue
        selected = wealth[indices]
        starts_at_account_origin = int(indices[0]) == 0
        start_wealth = 1.0 if starts_at_account_origin else float(selected[0])
        if start_wealth > 0:
            multiple = float(selected[-1] / start_wealth)
        else:
            multiple = 0.0 if float(selected[-1]) <= 0 else float("nan")
        years = max((len(indices) - 1) / TRADING_DAYS, 0.0)
        running_max = np.maximum.accumulate(selected)
        if starts_at_account_origin:
            running_max = np.maximum(running_max, 1.0)
        if np.all(running_max <= 0):
            max_drawdown = -1.0
        else:
            safe_max = np.where(running_max > 0, running_max, 1.0)
            max_drawdown = float(np.min(selected / safe_max - 1.0))
        result[label] = {
            "start": calendar[indices[0]].strftime("%Y-%m-%d"),
            "end": calendar[indices[-1]].strftime("%Y-%m-%d"),
            "observations": int(len(indices)),
            "final_wealth_multiple": multiple,
            "cagr": float(multiple ** (1.0 / years) - 1.0) if years > 0 and multiple > 0 else float("nan"),
            "max_drawdown": max_drawdown,
            "trades": int(np.count_nonzero(trade_notional[indices] > 0)),
            "turnover": float(np.sum(trade_notional[indices])),
            "transaction_cost": float(np.sum(transaction_cost[indices])),
            "period": label,
        }
    return result


def _filled_levels(executable: np.ndarray) -> np.ndarray:
    return (
        pd.Series(executable, copy=False)
        .ffill()
        .fillna(0)
        .to_numpy(dtype="int16")
    )


def _target_events(
    executable: np.ndarray,
    *,
    spec: LadderSpec,
    exit_variant: ExitVariant,
) -> list[tuple[int, float]]:
    levels = _filled_levels(executable)
    events: list[tuple[int, float]] = []
    if exit_variant == "a":
        changes = np.r_[0, np.flatnonzero(np.diff(levels) != 0) + 1]
        for i in changes:
            target = int(levels[i]) / spec.levels
            if target > 0 or int(i) > 0:
                events.append((int(i), target))
    elif exit_variant in ("b60", "b120"):
        holding = 60 if exit_variant == "b60" else 120
        prior_level = 0
        expiry: int | None = None
        for i, level in enumerate(levels):
            if expiry is not None and i >= expiry:
                events.append((i, 0.0))
                expiry = None
            if int(level) > 0 and prior_level == 0 and expiry is None:
                events.append((i, 1.0))
                expiry = i + holding
            prior_level = int(level)
    elif exit_variant == "d":
        hits = np.flatnonzero(levels >= 1)
        if len(hits):
            events.append((int(hits[0]), 1.0))
    else:
        raise ValueError("target events support a, b60, b120, and d")
    if spec.base_exposure > 0 and (not events or events[0][0] != 0):
        events.insert(0, (0, 0.0))
    return events


def _simulate_target_events_fast(
    calendar: pd.DatetimeIndex,
    core_prices: np.ndarray,
    product_prices: np.ndarray,
    executable: np.ndarray,
    *,
    spec: LadderSpec,
    leverage_multiple: int,
    exit_variant: ExitVariant,
    transaction_cost: float,
    cash_yield: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    events = _target_events(executable, spec=spec, exit_variant=exit_variant)
    n = len(calendar)
    wealth = np.empty(n, dtype="float64")
    notionals = np.zeros(n, dtype="float64")
    costs = np.zeros(n, dtype="float64")
    daily_cash_factor = (1.0 + cash_yield) ** (1.0 / TRADING_DAYS)
    cash = 1.0
    core_units = 0.0
    product_units = 0.0
    anchor = 0
    wealth[0] = 1.0

    def fill_through(end: int) -> None:
        nonlocal cash, anchor
        if end < anchor:
            return
        offsets = np.arange(end - anchor + 1, dtype="float64")
        cash_path = cash * np.power(daily_cash_factor, offsets)
        wealth[anchor : end + 1] = (
            cash_path
            + core_units * core_prices[anchor : end + 1]
            + product_units * product_prices[anchor : end + 1]
        )
        cash = float(cash_path[-1])
        anchor = end

    for index, overlay_fraction in events:
        fill_through(index)
        core_weight, product_weight, _ = _target_allocation(
            overlay_fraction,
            base_exposure=spec.base_exposure,
            leverage_multiple=leverage_multiple,
            product_share_at_max=float(spec.product_share_at_max),
        )
        cash, core_units, product_units, notional, cost = _rebalance_assets(
            cash,
            core_units,
            product_units,
            core_prices[index],
            product_prices[index],
            core_weight,
            product_weight,
            transaction_cost,
        )
        notionals[index] += notional
        costs[index] += cost
        wealth[index] = (
            cash + core_units * core_prices[index] + product_units * product_prices[index]
        )
    if anchor < n - 1:
        anchor += 1
        cash *= daily_cash_factor
        offsets = np.arange(n - anchor, dtype="float64")
        cash_path = cash * np.power(daily_cash_factor, offsets)
        wealth[anchor:] = (
            cash_path
            + core_units * core_prices[anchor:]
            + product_units * product_prices[anchor:]
        )
        cash = float(cash_path[-1])
        anchor = n - 1
    elif not events:
        fill_through(n - 1)
    return wealth, notionals, costs


def _profit_event_plan(levels: np.ndarray, prices: np.ndarray, split_count: int) -> dict[int, list[tuple[str, int, int]]]:
    events: dict[int, list[tuple[str, int, int]]] = {}
    tranche_id = 0
    for rung in range(1, split_count + 1):
        active = levels >= rung
        entries = np.flatnonzero(active & ~np.r_[False, active[:-1]])
        for entry in entries:
            if prices[entry] <= 0.0:
                continue
            tranche_id += 1
            events.setdefault(int(entry), []).append(("buy", tranche_id, rung))
            entry_price = prices[entry]
            search_start = int(entry) + 1
            for third in (1, 2, 3):
                candidates = np.flatnonzero(
                    prices[search_start:] + 1e-14
                    >= entry_price * (1.0 + 0.30 * third)
                )
                if not len(candidates):
                    break
                sale = search_start + int(candidates[0])
                events.setdefault(sale, []).append(("sell", tranche_id, third))
    return events


def _simulate_profit_events_fast(
    calendar: pd.DatetimeIndex,
    core_prices: np.ndarray,
    product_prices: np.ndarray,
    executable: np.ndarray,
    *,
    spec: LadderSpec,
    leverage_multiple: int,
    transaction_cost: float,
    cash_yield: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    levels = _filled_levels(executable)
    events = _profit_event_plan(levels, product_prices, spec.levels)
    n = len(calendar)
    wealth = np.empty(n, dtype="float64")
    notionals = np.zeros(n, dtype="float64")
    costs = np.zeros(n, dtype="float64")
    daily_cash_factor = (1.0 + cash_yield) ** (1.0 / TRADING_DAYS)
    cash = 1.0
    core_units = 0.0
    product_units = 0.0
    tranche_units: dict[int, tuple[float, float]] = {}
    anchor = 0
    wealth[0] = 1.0

    if spec.base_exposure > 0:
        core_weight, product_weight, _ = _target_allocation(
            0.0,
            base_exposure=spec.base_exposure,
            leverage_multiple=leverage_multiple,
            product_share_at_max=float(spec.product_share_at_max),
        )
        cash, core_units, product_units, notional, cost = _rebalance_assets(
            cash,
            0.0,
            0.0,
            core_prices[0],
            product_prices[0],
            core_weight,
            product_weight,
            transaction_cost,
        )
        notionals[0] += notional
        costs[0] += cost

    for index in sorted(events):
        if index >= anchor:
            offsets = np.arange(index - anchor + 1, dtype="float64")
            cash_path = cash * np.power(daily_cash_factor, offsets)
            wealth[anchor : index + 1] = (
                cash_path
                + core_units * core_prices[anchor : index + 1]
                + product_units * product_prices[anchor : index + 1]
            )
            cash = float(cash_path[-1])
            anchor = index
        day_events = sorted(events[index], key=lambda item: 0 if item[0] == "sell" else 1)
        for kind, tranche_id, marker in day_events:
            if kind == "sell":
                stored = tranche_units.get(tranche_id)
                if stored is None:
                    continue
                original, remaining = stored
                sell_units = min(original / 3.0, remaining)
                product_notional = sell_units * product_prices[index]
                sale_cost = transaction_cost * product_notional
                cash += product_notional - sale_cost
                remaining -= sell_units
                product_units -= sell_units
                tranche_units[tranche_id] = (original, remaining)
                notional = product_notional
                cost = sale_cost
                if spec.base_exposure > 0 and product_notional > 0.0:
                    core_notional = (product_notional - sale_cost) / (1.0 + transaction_cost)
                    buy_cost = transaction_cost * core_notional
                    cash -= core_notional + buy_cost
                    core_units += core_notional / core_prices[index]
                    notional += core_notional
                    cost += buy_cost
            else:
                if product_prices[index] <= 0.0:
                    continue
                current_wealth = (
                    cash
                    + core_units * core_prices[index]
                    + product_units * product_prices[index]
                )
                current_product_weight = (
                    product_units * product_prices[index] / current_wealth
                    if current_wealth > 0.0
                    else 0.0
                )
                current_overlay = _overlay_progress_from_product_weight(
                    current_product_weight,
                    base_exposure=spec.base_exposure,
                    leverage_multiple=leverage_multiple,
                    product_share_at_max=float(spec.product_share_at_max),
                )
                target_overlay = min(1.0, current_overlay + 1.0 / spec.levels)
                core_weight, product_weight, _ = _target_allocation(
                    target_overlay,
                    base_exposure=spec.base_exposure,
                    leverage_multiple=leverage_multiple,
                    product_share_at_max=float(spec.product_share_at_max),
                )
                prior_product_units = product_units
                cash, core_units, product_units, notional, cost = _rebalance_assets(
                    cash,
                    core_units,
                    product_units,
                    core_prices[index],
                    product_prices[index],
                    core_weight,
                    product_weight,
                    transaction_cost,
                )
                bought_units = max(0.0, product_units - prior_product_units)
                if bought_units > 0.0:
                    tranche_units[tranche_id] = (bought_units, bought_units)
            notionals[index] += notional
            costs[index] += cost
        wealth[index] = (
            cash + core_units * core_prices[index] + product_units * product_prices[index]
        )
    if anchor < n - 1:
        anchor += 1
        cash *= daily_cash_factor
        offsets = np.arange(n - anchor, dtype="float64")
        cash_path = cash * np.power(daily_cash_factor, offsets)
        wealth[anchor:] = (
            cash_path
            + core_units * core_prices[anchor:]
            + product_units * product_prices[anchor:]
        )
    elif not events:
        offsets = np.arange(n, dtype="float64")
        wealth[:] = (
            cash * np.power(daily_cash_factor, offsets)
            + core_units * core_prices
            + product_units * product_prices
        )
    return wealth, notionals, costs


def simulate_grid_metrics(
    dates: pd.Series,
    product_returns: pd.Series,
    executable_levels: pd.Series,
    *,
    underlying_returns: pd.Series,
    spec: LadderSpec,
    leverage_multiple: int,
    exit_variant: ExitVariant,
    transaction_cost: float,
    cash_yield: float = 0.0,
) -> dict[str, dict[str, float | int | str | None]]:
    """Fast metric-only equivalent used by the exhaustive sensitivity grid."""

    _validate_base_exposure(spec.base_exposure, leverage_multiple)
    if spec.base_exposure == 1.0 and leverage_multiple == 1:
        return simulate_baseline(
            dates,
            underlying_returns,
            transaction_cost=transaction_cost,
        ).metrics

    (
        calendar,
        _,
        _,
        executable,
        core_prices,
        product_prices,
    ) = _aligned_inputs(dates, product_returns, underlying_returns, executable_levels)
    if exit_variant == "c":
        wealth, notionals, costs = _simulate_profit_events_fast(
            calendar,
            core_prices,
            product_prices,
            executable,
            spec=spec,
            leverage_multiple=leverage_multiple,
            transaction_cost=transaction_cost,
            cash_yield=cash_yield,
        )
    else:
        wealth, notionals, costs = _simulate_target_events_fast(
            calendar,
            core_prices,
            product_prices,
            executable,
            spec=spec,
            leverage_multiple=leverage_multiple,
            exit_variant=exit_variant,
            transaction_cost=transaction_cost,
            cash_yield=cash_yield,
        )
    return _metrics_from_arrays(calendar, wealth, notionals, costs)


def _trade_row(
    date: pd.Timestamp,
    action: str,
    level: int | None,
    notional: float,
    cost: float,
    target_weight: float | None,
    target_exposure: float | None = None,
) -> dict[str, Any]:
    return {
        "date": date,
        "action": action,
        "level": level,
        "notional": float(notional),
        "cost": float(cost),
        "target_weight": target_weight,
        "target_exposure": target_exposure,
    }


def _aligned_inputs(
    dates: pd.Series,
    product_returns: pd.Series,
    underlying_returns: pd.Series,
    levels: pd.Series,
) -> tuple[pd.DatetimeIndex, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    calendar = pd.DatetimeIndex(pd.to_datetime(dates, errors="raise")).normalize()
    if calendar.has_duplicates or not calendar.is_monotonic_increasing:
        raise ValueError("dates must be unique and increasing")
    product = pd.to_numeric(product_returns, errors="raise").to_numpy(dtype="float64")
    underlying = pd.to_numeric(underlying_returns, errors="raise").to_numpy(dtype="float64")
    executable = pd.to_numeric(levels, errors="coerce").to_numpy(dtype="float64")
    if len({len(calendar), len(product), len(underlying), len(executable)}) != 1:
        raise ValueError("dates, product returns, underlying returns, and levels must have equal length")
    if len(calendar) < 2:
        raise ValueError("account inputs must have at least two rows")
    if not np.isfinite(product).all() or np.any(product < -1.0):
        raise ValueError("product returns must be finite and at least -100%")
    if not np.isfinite(underlying).all() or np.any(underlying < -1.0):
        raise ValueError("underlying returns must be finite and at least -100%")
    product_prices = np.cumprod(1.0 + product)
    core_prices = np.cumprod(1.0 + underlying)
    return calendar, product, underlying, executable, core_prices, product_prices


def _simulate_target_account(
    calendar: pd.DatetimeIndex,
    product_returns: np.ndarray,
    underlying_returns: np.ndarray,
    executable: np.ndarray,
    core_prices: np.ndarray,
    product_prices: np.ndarray,
    *,
    spec: LadderSpec,
    leverage_multiple: int,
    exit_variant: ExitVariant,
    transaction_cost: float,
    cash_yield: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    n = len(calendar)
    cash = 1.0
    core_units = 0.0
    product_units = 0.0
    daily_cash = (1.0 + cash_yield) ** (1.0 / TRADING_DAYS) - 1.0
    rows: list[dict[str, Any]] = []
    trade_rows: list[dict[str, Any]] = []
    events = {index: overlay for index, overlay in _target_events(
        executable,
        spec=spec,
        exit_variant=exit_variant,
    )}
    action = {
        "a": "ladder_rebalance",
        "b60": "fixed_period_rebalance",
        "b120": "fixed_period_rebalance",
        "d": "buy_and_hold_rebalance",
    }[exit_variant]
    for i in range(n):
        if i > 0:
            cash *= 1.0 + daily_cash
        core_value = core_units * core_prices[i]
        product_value = product_units * product_prices[i]
        wealth_before = cash + core_value + product_value
        level_value = executable[i]
        level = None if not np.isfinite(level_value) else int(level_value)
        notional = 0.0
        cost = 0.0
        target_product_weight: float | None = None
        target_exposure: float | None = None
        if i in events:
            core_weight, target_product_weight, target_exposure = _target_allocation(
                events[i],
                base_exposure=spec.base_exposure,
                leverage_multiple=leverage_multiple,
                product_share_at_max=float(spec.product_share_at_max),
            )
            cash, core_units, product_units, notional, cost = _rebalance_assets(
                cash,
                core_units,
                product_units,
                core_prices[i],
                product_prices[i],
                core_weight,
                target_product_weight,
                transaction_cost,
            )

        core_value = core_units * core_prices[i]
        product_value = product_units * product_prices[i]
        wealth = cash + core_value + product_value
        core_weight = core_value / wealth if wealth > 0 else 0.0
        product_weight = product_value / wealth if wealth > 0 else 0.0
        exposure = core_weight + leverage_multiple * product_weight
        if notional > 0:
            trade_rows.append(_trade_row(
                calendar[i],
                action,
                level,
                notional,
                cost,
                target_product_weight,
                target_exposure,
            ))
        rows.append({
            "date": calendar[i],
            "wealth": wealth,
            "cash": cash,
            "asset_value": core_value + product_value,
            "core_value": core_value,
            "product_value": product_value,
            "core_weight": core_weight,
            "product_weight": product_weight,
            "weight": product_weight,
            "exposure": exposure,
            "effective_exposure": exposure,
            "executable_level": level,
            "underlying_return": underlying_returns[i],
            "product_return": product_returns[i],
            "trade_notional": notional,
            "transaction_cost": cost,
            "wealth_before_trade": wealth_before,
        })
    return pd.DataFrame(rows), pd.DataFrame(trade_rows)


def _simulate_profit_account(
    calendar: pd.DatetimeIndex,
    product_returns: np.ndarray,
    underlying_returns: np.ndarray,
    executable: np.ndarray,
    core_prices: np.ndarray,
    product_prices: np.ndarray,
    *,
    spec: LadderSpec,
    leverage_multiple: int,
    transaction_cost: float,
    cash_yield: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Shift equal overlay rungs back to the 1x core at +30/+60/+90%."""

    cash = 1.0
    core_units = 0.0
    product_units = 0.0
    tranche_units: dict[int, tuple[float, float]] = {}
    levels = _filled_levels(executable)
    events = _profit_event_plan(levels, product_prices, spec.levels)
    daily_cash = (1.0 + cash_yield) ** (1.0 / TRADING_DAYS) - 1.0
    rows: list[dict[str, Any]] = []
    trade_rows: list[dict[str, Any]] = []
    initial_notional = 0.0
    initial_cost = 0.0
    if spec.base_exposure > 0:
        initial_core_weight, initial_product_weight, initial_exposure = _target_allocation(
            0.0,
            base_exposure=spec.base_exposure,
            leverage_multiple=leverage_multiple,
            product_share_at_max=float(spec.product_share_at_max),
        )
        cash, core_units, product_units, notional, cost = _rebalance_assets(
            cash,
            0.0,
            0.0,
            core_prices[0],
            product_prices[0],
            initial_core_weight,
            initial_product_weight,
            transaction_cost,
        )
        initial_notional = notional
        initial_cost = cost
        if notional > 0:
            trade_rows.append(_trade_row(
                calendar[0],
                "base_entry",
                None,
                notional,
                cost,
                initial_product_weight,
                initial_exposure,
            ))
    for i, date in enumerate(calendar):
        if i > 0:
            cash *= 1.0 + daily_cash
        level_value = executable[i]
        level = None if not np.isfinite(level_value) else int(level_value)
        day_notional = initial_notional if i == 0 else 0.0
        day_cost = initial_cost if i == 0 else 0.0
        wealth_before = 1.0 if i == 0 else (
            cash + core_units * core_prices[i] + product_units * product_prices[i]
        )

        for kind, tranche_id, marker in sorted(
            events.get(i, []), key=lambda item: 0 if item[0] == "sell" else 1
        ):
            if kind == "sell":
                stored = tranche_units.get(tranche_id)
                if stored is None:
                    continue
                original, remaining = stored
                sell_units = min(original / 3.0, remaining)
                product_notional = sell_units * product_prices[i]
                sale_cost = transaction_cost * product_notional
                cash += product_notional - sale_cost
                product_units -= sell_units
                remaining -= sell_units
                tranche_units[tranche_id] = (original, remaining)
                notional = product_notional
                cost = sale_cost
                if spec.base_exposure > 0 and product_notional > 0.0:
                    core_notional = (product_notional - sale_cost) / (1.0 + transaction_cost)
                    buy_cost = transaction_cost * core_notional
                    cash -= core_notional + buy_cost
                    core_units += core_notional / core_prices[i]
                    notional += core_notional
                    cost += buy_cost
                action = f"profit_take_{marker}"
            else:
                current_wealth = (
                    cash + core_units * core_prices[i] + product_units * product_prices[i]
                )
                current_product_weight = (
                    product_units * product_prices[i] / current_wealth
                    if current_wealth > 0.0
                    else 0.0
                )
                current_overlay = _overlay_progress_from_product_weight(
                    current_product_weight,
                    base_exposure=spec.base_exposure,
                    leverage_multiple=leverage_multiple,
                    product_share_at_max=float(spec.product_share_at_max),
                )
                target_overlay = min(1.0, current_overlay + 1.0 / spec.levels)
                core_weight, product_weight, target_exposure = _target_allocation(
                    target_overlay,
                    base_exposure=spec.base_exposure,
                    leverage_multiple=leverage_multiple,
                    product_share_at_max=float(spec.product_share_at_max),
                )
                prior_product_units = product_units
                cash, core_units, product_units, notional, cost = _rebalance_assets(
                    cash,
                    core_units,
                    product_units,
                    core_prices[i],
                    product_prices[i],
                    core_weight,
                    product_weight,
                    transaction_cost,
                )
                bought_units = max(0.0, product_units - prior_product_units)
                if bought_units > 0.0:
                    tranche_units[tranche_id] = (bought_units, bought_units)
                action = "profit_entry"
            day_notional += notional
            day_cost += cost
            if notional > 0.0:
                core_value = core_units * core_prices[i]
                product_value = product_units * product_prices[i]
                wealth = cash + core_value + product_value
                exposure = (
                    core_value + leverage_multiple * product_value
                ) / wealth if wealth > 0.0 else 0.0
                trade_rows.append(_trade_row(
                    date,
                    action,
                    marker if kind == "buy" else None,
                    notional,
                    cost,
                    product_value / wealth if wealth > 0.0 else 0.0,
                    exposure,
                ))

        core_value = core_units * core_prices[i]
        product_value = product_units * product_prices[i]
        wealth = cash + core_value + product_value
        core_weight = core_value / wealth if wealth > 0.0 else 0.0
        product_weight = product_value / wealth if wealth > 0.0 else 0.0
        rows.append({
            "date": date,
            "wealth": wealth,
            "cash": cash,
            "asset_value": core_value + product_value,
            "core_value": core_value,
            "product_value": product_value,
            "core_weight": core_weight,
            "product_weight": product_weight,
            "weight": product_weight,
            "exposure": core_weight + leverage_multiple * product_weight,
            "effective_exposure": core_weight + leverage_multiple * product_weight,
            "executable_level": level,
            "underlying_return": underlying_returns[i],
            "product_return": product_returns[i],
            "trade_notional": day_notional,
            "transaction_cost": day_cost,
            "wealth_before_trade": wealth_before,
        })
    return pd.DataFrame(rows), pd.DataFrame(trade_rows)


def _episode_rows(
    curve: pd.DataFrame,
    trades: pd.DataFrame,
    *,
    baseline_curve: pd.DataFrame | None,
    base_exposure: float,
    leverage_multiple: int,
) -> pd.DataFrame:
    executable = pd.to_numeric(curve["executable_level"], errors="coerce").ffill().fillna(0).astype(int)
    positive = executable.gt(0)
    starts = positive & ~positive.shift(1, fill_value=False)
    ends = positive & ~positive.shift(-1, fill_value=False)
    start_indices = list(curve.index[starts])
    end_indices = list(curve.index[ends])
    rows: list[dict[str, Any]] = []
    base_product_fraction = _base_product_fraction(base_exposure, leverage_multiple)
    for number, (start, signal_end) in enumerate(zip(start_indices, end_indices, strict=True), start=1):
        entry_wealth = float(curve.loc[max(start - 1, 0), "wealth"])
        actual_exit: int | None = None
        for idx in range(signal_end + 1, len(curve)):
            if float(curve.loc[idx, "product_weight"]) <= base_product_fraction + 1e-8:
                actual_exit = idx
                break
        measurement_end = actual_exit if actual_exit is not None else signal_end
        end_wealth = float(curve.loc[measurement_end, "wealth"])
        baseline_contribution = float("nan")
        if baseline_curve is not None:
            b_start = float(baseline_curve.loc[max(start - 1, 0), "wealth"])
            b_end = float(baseline_curve.loc[measurement_end, "wealth"])
            baseline_contribution = b_end / b_start - 1.0
        rows.append({
            "episode": number,
            "entry_date": pd.Timestamp(curve.loc[start, "date"]).strftime("%Y-%m-%d"),
            "max_level_reached": int(executable.loc[start:signal_end].max()),
            "signal_end_date": pd.Timestamp(curve.loc[signal_end, "date"]).strftime("%Y-%m-%d"),
            "exit_date": (
                pd.Timestamp(curve.loc[actual_exit, "date"]).strftime("%Y-%m-%d")
                if actual_exit is not None
                else None
            ),
            "contribution_to_wealth": end_wealth / entry_wealth - 1.0,
            "baseline_contribution": baseline_contribution,
        })
    return pd.DataFrame(rows)


def simulate_account(
    dates: pd.Series,
    product_returns: pd.Series,
    executable_levels: pd.Series,
    *,
    underlying_returns: pd.Series,
    spec: LadderSpec,
    leverage_multiple: int,
    exit_variant: ExitVariant = "a",
    transaction_cost: float = 0.001,
    cash_yield: float = 0.0,
    baseline_curve: pd.DataFrame | None = None,
) -> SimulationResult:
    """Run one continuous self-financing account over the supplied span."""

    if exit_variant not in {"a", "b60", "b120", "c", "d"}:
        raise ValueError("unsupported exit variant")
    if leverage_multiple not in (1, 2, 3):
        raise ValueError("leverage_multiple must be 1, 2, or 3")
    if not 0.0 <= transaction_cost < 1.0:
        raise ValueError("transaction_cost must be in [0, 1)")
    if cash_yield <= -1.0:
        raise ValueError("cash_yield must be greater than -100%")
    _validate_base_exposure(spec.base_exposure, leverage_multiple)
    if spec.base_exposure == 1.0 and leverage_multiple == 1:
        return simulate_baseline(
            dates,
            underlying_returns,
            transaction_cost=transaction_cost,
        )
    (
        calendar,
        product,
        underlying,
        executable,
        core_prices,
        product_prices,
    ) = _aligned_inputs(dates, product_returns, underlying_returns, executable_levels)
    if exit_variant == "c":
        curve, trades = _simulate_profit_account(
            calendar,
            product,
            underlying,
            executable,
            core_prices,
            product_prices,
            spec=spec,
            leverage_multiple=leverage_multiple,
            transaction_cost=transaction_cost,
            cash_yield=cash_yield,
        )
    else:
        curve, trades = _simulate_target_account(
            calendar,
            product,
            underlying,
            executable,
            core_prices,
            product_prices,
            spec=spec,
            leverage_multiple=leverage_multiple,
            exit_variant=exit_variant,
            transaction_cost=transaction_cost,
            cash_yield=cash_yield,
        )
    cycles = _episode_rows(
        curve,
        trades,
        baseline_curve=baseline_curve,
        base_exposure=spec.base_exposure,
        leverage_multiple=leverage_multiple,
    )
    return SimulationResult(
        curve,
        trades,
        cycles,
        performance_metrics(curve),
        effective_exposure_at_max(spec, leverage_multiple),
    )


def simulate_baseline(
    dates: pd.Series,
    product_returns: pd.Series,
    *,
    transaction_cost: float = 0.001,
) -> SimulationResult:
    """Buy 100% at the first retained close and never sell."""

    calendar, returns, _, _, prices, _ = _aligned_inputs(
        dates,
        product_returns,
        product_returns,
        pd.Series(np.zeros(len(product_returns))),
    )
    cash, units, notional, cost = _rebalance(1.0, 0.0, prices[0], 1.0, transaction_cost)
    rows: list[dict[str, Any]] = []
    for i, date in enumerate(calendar):
        asset = units * prices[i]
        rows.append({
            "date": date,
            "wealth": cash + asset,
            "cash": cash,
            "asset_value": asset,
            "core_value": asset,
            "product_value": 0.0,
            "core_weight": asset / (cash + asset) if cash + asset > 0 else 0.0,
            "product_weight": 0.0,
            "weight": 0.0,
            "exposure": asset / (cash + asset) if cash + asset > 0 else 0.0,
            "effective_exposure": asset / (cash + asset) if cash + asset > 0 else 0.0,
            "executable_level": 0,
            "underlying_return": returns[i],
            "product_return": returns[i],
            "trade_notional": notional if i == 0 else 0.0,
            "transaction_cost": cost if i == 0 else 0.0,
            "wealth_before_trade": 1.0 if i == 0 else cash + asset,
        })
    curve = pd.DataFrame(rows)
    trades = pd.DataFrame([_trade_row(calendar[0], "baseline_entry", None, notional, cost, 0.0, 1.0)])
    return SimulationResult(curve, trades, pd.DataFrame(), performance_metrics(curve), 1.0)


def with_baseline_comparison(
    metrics: dict[str, dict[str, float | int | str | None]],
    baseline: dict[str, dict[str, float | int | str | None]],
) -> dict[str, dict[str, float | int | str | None]]:
    result: dict[str, dict[str, float | int | str | None]] = {}
    for period in ("fit", "holdout", "full"):
        row = dict(metrics[period])
        strategy_multiple = float(row["final_wealth_multiple"])
        baseline_multiple = float(baseline[period]["final_wealth_multiple"])
        row["baseline_final_wealth_multiple"] = baseline_multiple
        row["relative_to_baseline"] = (
            strategy_multiple / baseline_multiple
            if np.isfinite(strategy_multiple) and np.isfinite(baseline_multiple) and baseline_multiple != 0
            else float("nan")
        )
        row["final_wealth_edge"] = strategy_multiple - baseline_multiple
        result[period] = row
    return result


def weekly_curve(curve: pd.DataFrame) -> list[dict[str, float | str]]:
    frame = curve.copy().set_index(pd.to_datetime(curve["date"], errors="raise"))
    selected = frame.resample("W-FRI").last().dropna(subset=["wealth"])
    if not frame.empty and (selected.empty or selected.index[-1] != frame.index[-1]):
        selected = pd.concat([selected, frame.iloc[[-1]]]).sort_index().loc[lambda item: ~item.index.duplicated(keep="last")]
    return [
        {
            "date": pd.Timestamp(index).strftime("%Y-%m-%d"),
            "wealth": float(row["wealth"]),
            "weight": float(row["weight"]),
            "effective_exposure": float(row["effective_exposure"]),
        }
        for index, row in selected.iterrows()
    ]


def validate_grid_row(row: dict[str, Any]) -> None:
    missing = set(GRID_REQUIRED_FIELDS).difference(row)
    if missing:
        raise ValueError(f"grid row is missing fields: {sorted(missing)}")
    if row["row_kind"] not in {"strategy", "baseline"}:
        raise ValueError("grid row_kind must be strategy or baseline")
    base_exposure = row["base_exposure"]
    if not isinstance(base_exposure, (int, float)) or not np.isfinite(base_exposure):
        raise ValueError("grid base_exposure must be finite")
    if not 0.0 <= float(base_exposure) <= 3.0:
        raise ValueError("grid base_exposure must be in [0.0, 3.0]")
    leverage_multiple = row["leverage_multiple"]
    if leverage_multiple is not None and float(base_exposure) > float(leverage_multiple):
        raise ValueError("grid base_exposure must not exceed leverage_multiple")
    product_share = row["product_share_at_max"]
    effective_max = row["effective_exposure_max"]
    if row["row_kind"] == "strategy":
        if not isinstance(product_share, (int, float)) or not np.isfinite(product_share):
            raise ValueError("grid product_share_at_max must be finite for strategy rows")
        if not 0.0 <= float(product_share) <= 1.0:
            raise ValueError("grid product_share_at_max must be in [0.0, 1.0]")
        expected = effective_exposure_at_max(
            LadderSpec(
                drawdown_threshold=float(row["drawdown_threshold"]),
                disp60_threshold=float(row["disp60_threshold"]),
                product_share_at_max=float(product_share),
                levels=int(row["levels"]),
                base_exposure=float(base_exposure),
            ),
            int(leverage_multiple),
        )
        if not isinstance(effective_max, (int, float)) or not np.isclose(effective_max, expected):
            raise ValueError("grid effective_exposure_max is inconsistent with its weights")
    elif product_share is not None or not isinstance(effective_max, (int, float)):
        raise ValueError("baseline rows require product_share_at_max=None and numeric effective_exposure_max")
    for period in ("fit", "holdout", "full"):
        metrics = row[period]
        required = {"final_wealth_multiple", "cagr", "max_drawdown"}
        if not isinstance(metrics, dict) or required.difference(metrics):
            raise ValueError(f"grid {period} metrics are incomplete")


__all__ = [
    "FIT_END",
    "GRID_REQUIRED_FIELDS",
    "HOLDOUT_START",
    "LadderSpec",
    "SimulationResult",
    "effective_exposure_at_max",
    "ladder_levels",
    "performance_metrics",
    "require_base_exposure",
    "require_disp60_threshold",
    "require_drawdown_threshold",
    "require_levels",
    "require_product_share_at_max",
    "simulate_account",
    "simulate_baseline",
    "simulate_grid_metrics",
    "validate_grid_row",
    "weekly_curve",
    "with_baseline_comparison",
]
