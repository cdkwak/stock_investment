"""Continuous-account drawdown ladder simulation.

Signals are observed at index close T and can first change the account at the
next retained session's close.  The implementation is provider-free, long/cash
only, and intentionally separate from broker/order code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import numpy as np
import pandas as pd


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
    "exit",
    "cost_enabled",
    "fit",
    "holdout",
    "full",
)


@dataclass(frozen=True, slots=True)
class LadderSpec:
    drawdown_threshold: float = -0.20
    disp60_threshold: float = -0.10
    levels: int = 2
    base_weight: float = 0.0

    def __post_init__(self) -> None:
        if self.levels not in (1, 2, 3, 4):
            raise ValueError("levels must be 1, 2, 3, or 4")
        if not -1.0 < self.drawdown_threshold < 0.0:
            raise ValueError("drawdown threshold must be between -1 and 0")
        if not -1.0 < self.disp60_threshold < 0.0:
            raise ValueError("disp60 threshold must be between -1 and 0")
        if not 0.0 <= self.base_weight <= 1.0:
            raise ValueError("base_weight must be in [0, 1]")


@dataclass(frozen=True, slots=True)
class SimulationResult:
    curve: pd.DataFrame
    trades: pd.DataFrame
    cycles: pd.DataFrame
    metrics: dict[str, dict[str, float | int | str | None]]


def ladder_levels(signals: pd.DataFrame, spec: LadderSpec) -> pd.DataFrame:
    """Map the two unchanged rule conditions onto equal capital rungs.

    The registered two-condition rule has raw scores 0, 1, 2.  Sensitivity
    split counts rescale that score proportionally: ceil(score / 2 * splits).
    Consequently every split count reaches full weight when both conditions
    hold while preserving the exact registered two-split path.
    """

    required = {"date", "drawdown252", "disp60"}
    missing = required.difference(signals.columns)
    if missing:
        raise ValueError(f"signal input is missing columns: {sorted(missing)}")
    frame = signals.loc[:, ["date", "drawdown252", "disp60"]].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="raise").dt.normalize()
    if frame["date"].duplicated().any() or not frame["date"].is_monotonic_increasing:
        raise ValueError("signal dates must be unique and increasing")
    dd = pd.to_numeric(frame["drawdown252"], errors="coerce")
    disp = pd.to_numeric(frame["disp60"], errors="coerce")
    valid = dd.notna() & disp.notna()
    raw = dd.le(spec.drawdown_threshold).astype("int8") + disp.le(spec.disp60_threshold).astype("int8")
    mapped = np.ceil(raw.astype("float64") * spec.levels / 2.0).astype("int64")
    observed = pd.Series(mapped, index=frame.index, dtype="Int64").where(valid, pd.NA)
    executable = observed.shift(1)
    frame["raw_score"] = pd.Series(raw, dtype="Int64").where(valid, pd.NA)
    frame["observed_level"] = observed
    frame["executable_level"] = executable
    frame["target_weight"] = (
        spec.base_weight
        + (1.0 - spec.base_weight) * frame["executable_level"].astype("float64") / spec.levels
    )
    return frame


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
            target = spec.base_weight + (1.0 - spec.base_weight) * int(levels[i]) / spec.levels
            if target > 0 or int(i) > 0:
                events.append((int(i), target))
    elif exit_variant in ("b60", "b120"):
        holding = 60 if exit_variant == "b60" else 120
        expiries: dict[int, int] = {}
        prior_level = 0
        prior_target = -1
        for i, level in enumerate(levels):
            for rung in [rung for rung, expiry in expiries.items() if expiry <= i]:
                del expiries[rung]
            if level > prior_level:
                for rung in range(prior_level + 1, int(level) + 1):
                    expiries.setdefault(rung, i + holding)
            prior_level = int(level)
            target_level = min(len(expiries), spec.levels)
            if target_level != prior_target:
                target = spec.base_weight + (1.0 - spec.base_weight) * target_level / spec.levels
                if target > 0 or prior_target >= 0:
                    events.append((i, target))
                prior_target = target_level
    elif exit_variant == "d":
        hits = np.flatnonzero(levels >= 1)
        if len(hits):
            events.append((int(hits[0]), 1.0))
    else:
        raise ValueError("target events support a, b60, b120, and d")
    if spec.base_weight > 0 and (not events or events[0][0] != 0):
        events.insert(0, (0, spec.base_weight))
    return events


def _simulate_target_events_fast(
    calendar: pd.DatetimeIndex,
    prices: np.ndarray,
    executable: np.ndarray,
    *,
    spec: LadderSpec,
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
    units = 0.0
    anchor = 0
    wealth[0] = 1.0

    def fill_through(end: int) -> None:
        nonlocal cash, anchor
        if end < anchor:
            return
        offsets = np.arange(end - anchor + 1, dtype="float64")
        cash_path = cash * np.power(daily_cash_factor, offsets)
        wealth[anchor : end + 1] = cash_path + units * prices[anchor : end + 1]
        cash = float(cash_path[-1])
        anchor = end

    for index, target in events:
        fill_through(index)
        cash, units, notional, cost = _rebalance(cash, units, prices[index], target, transaction_cost)
        notionals[index] += notional
        costs[index] += cost
        wealth[index] = cash + units * prices[index]
    if anchor < n - 1:
        anchor += 1
        cash *= daily_cash_factor
        offsets = np.arange(n - anchor, dtype="float64")
        cash_path = cash * np.power(daily_cash_factor, offsets)
        wealth[anchor:] = cash_path + units * prices[anchor:]
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
                    prices[search_start:] >= entry_price * (1.0 + 0.30 * third)
                )
                if not len(candidates):
                    break
                sale = search_start + int(candidates[0])
                events.setdefault(sale, []).append(("sell", tranche_id, third))
    return events


def _simulate_profit_events_fast(
    calendar: pd.DatetimeIndex,
    prices: np.ndarray,
    executable: np.ndarray,
    *,
    spec: LadderSpec,
    transaction_cost: float,
    cash_yield: float,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    levels = _filled_levels(executable)
    events = _profit_event_plan(levels, prices, spec.levels)
    n = len(calendar)
    wealth = np.empty(n, dtype="float64")
    notionals = np.zeros(n, dtype="float64")
    costs = np.zeros(n, dtype="float64")
    daily_cash_factor = (1.0 + cash_yield) ** (1.0 / TRADING_DAYS)
    cash = 1.0
    base_units = 0.0
    tranche_units: dict[int, tuple[float, float]] = {}
    total_units = 0.0
    anchor = 0
    wealth[0] = 1.0

    if spec.base_weight > 0:
        cash, base_units, notional, cost = _rebalance(cash, 0.0, prices[0], spec.base_weight, transaction_cost)
        total_units = base_units
        notionals[0] += notional
        costs[0] += cost

    for index in sorted(events):
        if index >= anchor:
            offsets = np.arange(index - anchor + 1, dtype="float64")
            cash_path = cash * np.power(daily_cash_factor, offsets)
            wealth[anchor : index + 1] = cash_path + total_units * prices[anchor : index + 1]
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
                notional = sell_units * prices[index]
                cost = transaction_cost * notional
                cash += notional - cost
                remaining -= sell_units
                total_units -= sell_units
                tranche_units[tranche_id] = (original, remaining)
            else:
                if prices[index] <= 0.0:
                    continue
                current_wealth = cash + total_units * prices[index]
                desired = (1.0 - spec.base_weight) * current_wealth / spec.levels
                notional = min(desired, cash / (1.0 + transaction_cost))
                cost = transaction_cost * notional
                if notional > 0:
                    cash -= notional + cost
                    bought_units = notional / prices[index]
                    tranche_units[tranche_id] = (bought_units, bought_units)
                    total_units += bought_units
            notionals[index] += notional
            costs[index] += cost
        wealth[index] = cash + total_units * prices[index]
    if anchor < n - 1:
        anchor += 1
        cash *= daily_cash_factor
        offsets = np.arange(n - anchor, dtype="float64")
        cash_path = cash * np.power(daily_cash_factor, offsets)
        wealth[anchor:] = cash_path + total_units * prices[anchor:]
    elif not events:
        offsets = np.arange(n, dtype="float64")
        wealth[:] = cash * np.power(daily_cash_factor, offsets) + total_units * prices
    return wealth, notionals, costs


def simulate_grid_metrics(
    dates: pd.Series,
    product_returns: pd.Series,
    executable_levels: pd.Series,
    *,
    spec: LadderSpec,
    exit_variant: ExitVariant,
    transaction_cost: float,
    cash_yield: float = 0.0,
) -> dict[str, dict[str, float | int | str | None]]:
    """Fast metric-only equivalent used by the exhaustive sensitivity grid."""

    calendar, _, executable, prices = _aligned_inputs(dates, product_returns, executable_levels)
    if exit_variant == "c":
        wealth, notionals, costs = _simulate_profit_events_fast(
            calendar,
            prices,
            executable,
            spec=spec,
            transaction_cost=transaction_cost,
            cash_yield=cash_yield,
        )
    else:
        wealth, notionals, costs = _simulate_target_events_fast(
            calendar,
            prices,
            executable,
            spec=spec,
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
) -> dict[str, Any]:
    return {
        "date": date,
        "action": action,
        "level": level,
        "notional": float(notional),
        "cost": float(cost),
        "target_weight": target_weight,
    }


def _aligned_inputs(
    dates: pd.Series,
    product_returns: pd.Series,
    levels: pd.Series,
) -> tuple[pd.DatetimeIndex, np.ndarray, np.ndarray, np.ndarray]:
    calendar = pd.DatetimeIndex(pd.to_datetime(dates, errors="raise")).normalize()
    if calendar.has_duplicates or not calendar.is_monotonic_increasing:
        raise ValueError("dates must be unique and increasing")
    returns = pd.to_numeric(product_returns, errors="raise").to_numpy(dtype="float64")
    executable = pd.to_numeric(levels, errors="coerce").to_numpy(dtype="float64")
    if len(calendar) != len(returns) or len(calendar) != len(executable):
        raise ValueError("dates, returns, and levels must have equal length")
    if len(calendar) < 2 or not np.isfinite(returns).all() or np.any(returns < -1.0):
        raise ValueError("product returns must be finite, at least -100%, and have two rows")
    prices = np.cumprod(1.0 + returns)
    return calendar, returns, executable, prices


def _simulate_target_account(
    calendar: pd.DatetimeIndex,
    returns: np.ndarray,
    executable: np.ndarray,
    prices: np.ndarray,
    *,
    spec: LadderSpec,
    exit_variant: ExitVariant,
    transaction_cost: float,
    cash_yield: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    n = len(calendar)
    cash = 1.0
    units = 0.0
    daily_cash = (1.0 + cash_yield) ** (1.0 / TRADING_DAYS) - 1.0
    rows: list[dict[str, Any]] = []
    trade_rows: list[dict[str, Any]] = []
    last_signal_level = 0
    last_target_level: int | None = None
    expiries: dict[int, int] = {}
    bought_forever = False
    for i in range(n):
        if i > 0:
            cash *= 1.0 + daily_cash
        asset = units * prices[i]
        wealth_before = cash + asset
        level_value = executable[i]
        level = None if not np.isfinite(level_value) else int(level_value)
        notional = 0.0
        cost = 0.0
        action = ""
        target: float | None = None

        if exit_variant == "a":
            if level is not None:
                target = spec.base_weight + (1.0 - spec.base_weight) * level / spec.levels
                if last_target_level != level or (i == 0 and spec.base_weight > 0):
                    cash, units, notional, cost = _rebalance(
                        cash, units, prices[i], target, transaction_cost
                    )
                    action = "ladder_rebalance"
                    last_target_level = level
        elif exit_variant in ("b60", "b120"):
            holding = 60 if exit_variant == "b60" else 120
            expired = [rung for rung, expiry in expiries.items() if expiry <= i]
            for rung in expired:
                del expiries[rung]
            if level is not None:
                if level > last_signal_level:
                    for rung in range(last_signal_level + 1, level + 1):
                        if rung not in expiries:
                            expiries[rung] = i + holding
                last_signal_level = level
            target_level = min(len(expiries), spec.levels)
            target = spec.base_weight + (1.0 - spec.base_weight) * target_level / spec.levels
            if target_level != last_target_level or (i == 0 and spec.base_weight > 0):
                cash, units, notional, cost = _rebalance(
                    cash, units, prices[i], target, transaction_cost
                )
                action = "fixed_period_rebalance"
                last_target_level = target_level
        elif exit_variant == "d":
            if not bought_forever and level is not None and level >= 1:
                cash, units, notional, cost = _rebalance(
                    cash, units, prices[i], 1.0, transaction_cost
                )
                action = "buy_and_hold_entry"
                target = 1.0
                bought_forever = True
        else:
            raise ValueError("target-account simulator supports exits a, b60, b120, and d")

        asset = units * prices[i]
        wealth = cash + asset
        weight = asset / wealth if wealth > 0 else 0.0
        if notional > 0:
            trade_rows.append(_trade_row(calendar[i], action, level, notional, cost, target))
        rows.append({
            "date": calendar[i],
            "wealth": wealth,
            "cash": cash,
            "asset_value": asset,
            "weight": weight,
            "executable_level": level,
            "product_return": returns[i],
            "trade_notional": notional,
            "transaction_cost": cost,
            "wealth_before_trade": wealth_before,
        })
    return pd.DataFrame(rows), pd.DataFrame(trade_rows)


def _simulate_profit_account(
    calendar: pd.DatetimeIndex,
    returns: np.ndarray,
    executable: np.ndarray,
    prices: np.ndarray,
    *,
    spec: LadderSpec,
    transaction_cost: float,
    cash_yield: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Buy equal rungs and sell one original third at +30/+60/+90%."""

    cash = 1.0
    base_units = 0.0
    tranches: list[dict[str, float | int]] = []
    armed = {rung: True for rung in range(1, spec.levels + 1)}
    last_level = 0
    daily_cash = (1.0 + cash_yield) ** (1.0 / TRADING_DAYS) - 1.0
    rows: list[dict[str, Any]] = []
    trade_rows: list[dict[str, Any]] = []
    if spec.base_weight > 0:
        cash, base_units, notional, cost = _rebalance(cash, 0.0, prices[0], spec.base_weight, transaction_cost)
        if notional > 0:
            trade_rows.append(_trade_row(calendar[0], "base_entry", None, notional, cost, spec.base_weight))
    for i, date in enumerate(calendar):
        if i > 0:
            cash *= 1.0 + daily_cash
        level_value = executable[i]
        level = None if not np.isfinite(level_value) else int(level_value)
        day_notional = 0.0
        day_cost = 0.0

        # A large close jump may clear more than one predeclared profit rung.
        for tranche in tranches:
            while int(tranche["sold_thirds"]) < 3:
                next_third = int(tranche["sold_thirds"]) + 1
                trigger = float(tranche["entry_price"]) * (1.0 + 0.30 * next_third)
                if prices[i] + 1e-14 < trigger:
                    break
                sell_units = min(float(tranche["original_units"]) / 3.0, float(tranche["units"]))
                notional = sell_units * prices[i]
                cost = transaction_cost * notional
                cash += notional - cost
                tranche["units"] = float(tranche["units"]) - sell_units
                tranche["sold_thirds"] = next_third
                day_notional += notional
                day_cost += cost
                trade_rows.append(_trade_row(date, f"profit_take_{next_third}", int(tranche["rung"]), notional, cost, None))

        if level is not None:
            for rung in range(level + 1, spec.levels + 1):
                armed[rung] = True
            if level > last_level:
                for rung in range(last_level + 1, level + 1):
                    if not armed[rung] or cash <= 0:
                        continue
                    if prices[i] <= 0.0:
                        continue
                    total_units = base_units + sum(float(item["units"]) for item in tranches)
                    wealth = cash + total_units * prices[i]
                    desired = (1.0 - spec.base_weight) * wealth / spec.levels
                    notional = min(desired, cash / (1.0 + transaction_cost))
                    if notional <= 0:
                        continue
                    cost = transaction_cost * notional
                    cash -= notional + cost
                    units = notional / prices[i]
                    tranches.append({
                        "rung": rung,
                        "entry_price": prices[i],
                        "original_units": units,
                        "units": units,
                        "sold_thirds": 0,
                    })
                    armed[rung] = False
                    day_notional += notional
                    day_cost += cost
                    trade_rows.append(_trade_row(date, "profit_entry", rung, notional, cost, None))
            last_level = level

        units = base_units + sum(float(item["units"]) for item in tranches)
        asset = units * prices[i]
        wealth = cash + asset
        rows.append({
            "date": date,
            "wealth": wealth,
            "cash": cash,
            "asset_value": asset,
            "weight": asset / wealth if wealth > 0 else 0.0,
            "executable_level": level,
            "product_return": returns[i],
            "trade_notional": day_notional,
            "transaction_cost": day_cost,
            "wealth_before_trade": wealth + day_cost,
        })
    return pd.DataFrame(rows), pd.DataFrame(trade_rows)


def _episode_rows(
    curve: pd.DataFrame,
    trades: pd.DataFrame,
    *,
    baseline_curve: pd.DataFrame | None,
    base_weight: float,
) -> pd.DataFrame:
    executable = pd.to_numeric(curve["executable_level"], errors="coerce").ffill().fillna(0).astype(int)
    positive = executable.gt(0)
    starts = positive & ~positive.shift(1, fill_value=False)
    ends = positive & ~positive.shift(-1, fill_value=False)
    start_indices = list(curve.index[starts])
    end_indices = list(curve.index[ends])
    rows: list[dict[str, Any]] = []
    for number, (start, signal_end) in enumerate(zip(start_indices, end_indices, strict=True), start=1):
        entry_wealth = float(curve.loc[max(start - 1, 0), "wealth"])
        actual_exit: int | None = None
        for idx in range(signal_end + 1, len(curve)):
            if float(curve.loc[idx, "weight"]) <= base_weight + 1e-8:
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
    spec: LadderSpec,
    exit_variant: ExitVariant = "a",
    transaction_cost: float = 0.001,
    cash_yield: float = 0.0,
    baseline_curve: pd.DataFrame | None = None,
) -> SimulationResult:
    """Run one continuous self-financing account over the supplied span."""

    if exit_variant not in {"a", "b60", "b120", "c", "d"}:
        raise ValueError("unsupported exit variant")
    if not 0.0 <= transaction_cost < 1.0:
        raise ValueError("transaction_cost must be in [0, 1)")
    if cash_yield <= -1.0:
        raise ValueError("cash_yield must be greater than -100%")
    calendar, returns, executable, prices = _aligned_inputs(dates, product_returns, executable_levels)
    if exit_variant == "c":
        curve, trades = _simulate_profit_account(
            calendar,
            returns,
            executable,
            prices,
            spec=spec,
            transaction_cost=transaction_cost,
            cash_yield=cash_yield,
        )
    else:
        curve, trades = _simulate_target_account(
            calendar,
            returns,
            executable,
            prices,
            spec=spec,
            exit_variant=exit_variant,
            transaction_cost=transaction_cost,
            cash_yield=cash_yield,
        )
    cycles = _episode_rows(curve, trades, baseline_curve=baseline_curve, base_weight=spec.base_weight)
    return SimulationResult(curve, trades, cycles, performance_metrics(curve))


def simulate_baseline(
    dates: pd.Series,
    product_returns: pd.Series,
    *,
    transaction_cost: float = 0.001,
) -> SimulationResult:
    """Buy 100% at the first retained close and never sell."""

    calendar, returns, _, prices = _aligned_inputs(
        dates,
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
            "weight": asset / (cash + asset) if cash + asset > 0 else 0.0,
            "executable_level": 0,
            "product_return": returns[i],
            "trade_notional": notional if i == 0 else 0.0,
            "transaction_cost": cost if i == 0 else 0.0,
            "wealth_before_trade": 1.0 if i == 0 else cash + asset,
        })
    curve = pd.DataFrame(rows)
    trades = pd.DataFrame([_trade_row(calendar[0], "baseline_entry", None, notional, cost, 1.0)])
    return SimulationResult(curve, trades, pd.DataFrame(), performance_metrics(curve))


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
        }
        for index, row in selected.iterrows()
    ]


def validate_grid_row(row: dict[str, Any]) -> None:
    missing = set(GRID_REQUIRED_FIELDS).difference(row)
    if missing:
        raise ValueError(f"grid row is missing fields: {sorted(missing)}")
    if row["row_kind"] not in {"strategy", "baseline"}:
        raise ValueError("grid row_kind must be strategy or baseline")
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
    "ladder_levels",
    "performance_metrics",
    "simulate_account",
    "simulate_baseline",
    "simulate_grid_metrics",
    "validate_grid_row",
    "weekly_curve",
    "with_baseline_comparison",
]
