"""Run the retained-Parquet compound ladder wealth backtest."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import math
from pathlib import Path
import re
import sys
import time
from typing import Any, Iterable

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from stock_data.research.compound_ladder import (  # noqa: E402
    GRID_REQUIRED_FIELDS,
    LadderSpec,
    effective_exposure_at_max,
    ladder_levels,
    require_base_exposure,
    require_disp60_threshold,
    require_drawdown_threshold,
    require_levels,
    simulate_account,
    simulate_baseline,
    simulate_grid_metrics,
    validate_grid_row,
    weekly_curve,
    with_baseline_comparison,
)
from stock_data.research.condition_backtest import compute_signals  # noqa: E402
from stock_data.research.leveraged_product import (  # noqa: E402
    FOREIGN_SYMBOLS,
    REAL_PRODUCT_MAP,
    load_index_universe,
    load_real_products,
    load_short_rate,
    realized_tracking_gap,
    retained_manifest_digest,
    synthetic_daily_returns,
    volatility_drag,
)
from stock_web.api.research_scenario import select_best_in_scenario  # noqa: E402


BASKET_SERIES: dict[str, tuple[str, ...]] = {
    "KR": ("KOSPI", "KOSPI200", "KOSPI200_IT"),
    "US_TECH": ("NASDAQ100",),
    "SEMIS": ("SOX",),
    "FOREIGN": FOREIGN_SYMBOLS,
}
FULL_GRID = {
    "drawdown_threshold": (-0.10, -0.15, -0.20, -0.25, -0.30, -0.35),
    "disp60_threshold": (-0.05, -0.10, -0.15),
    "levels": (1, 2, 3, 4),
    "leverage_multiple": (1, 2, 3),
    "base_exposure": (0.0, 1.0),
    "exit": ("a", "b60", "b120", "c", "d"),
    "cost_enabled": (False, True),
}
QUICK_GRID = {
    "drawdown_threshold": (-0.20,),
    "disp60_threshold": (-0.10,),
    "levels": (2,),
    "leverage_multiple": (1, 2),
    "base_exposure": (0.0, 1.0),
    "exit": ("a", "d"),
    "cost_enabled": (True,),
}
TRANSACTION_COST = 0.001
CURRENT = {
    "leverage_multiple": 2,
    "exit": "a",
    "cost_enabled": True,
}
_UNDECIDED = object()
BASE_SWEEP_MULTIPLES = (2, 3)
BASE_SWEEP_EXITS = ("a", "d")
BASE_SWEEP_PERIODS = ("fit", "holdout", "full")
BASE_SWEEP_REQUIRED_FIELDS = {
    "schema_version",
    "experiment",
    "development_only",
    "api_calls",
    "quick",
    "basket",
    "underlying",
    "parameters",
    "input_manifest_sha256",
    "input_manifest",
    "calibration",
    "independent_cycle_count",
    "references",
    "rows",
    "thresholds",
    "runtime_seconds",
}


def _json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (np.integer, int)) and not isinstance(value, bool):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d")
    if value is pd.NA:
        return None
    return value


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(_json_value(payload), ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")


def _baseline_metrics(
    dates: pd.Series,
    returns: pd.Series,
    *,
    transaction_cost: float,
) -> dict[str, dict[str, Any]]:
    return simulate_baseline(dates, returns, transaction_cost=transaction_cost).metrics


def _metric_pair(metrics: dict[str, Any]) -> dict[str, float]:
    return {
        "final_wealth_multiple": float(metrics["final_wealth_multiple"]),
        "max_drawdown": float(metrics["max_drawdown"]),
    }


def _safe_ratio(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator != 0.0 else float("nan")


def _comparison_periods(
    ladder: dict[str, dict[str, Any]],
    permanent: dict[str, dict[str, Any]],
    baseline: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for period in BASE_SWEEP_PERIODS:
        ladder_pair = _metric_pair(ladder[period])
        permanent_pair = _metric_pair(permanent[period])
        baseline_pair = _metric_pair(baseline[period])
        result[period] = {
            "ladder_on_base": ladder_pair,
            "permanent_base": permanent_pair,
            "baseline_1x": baseline_pair,
            "ladder_to_baseline_ratio": _safe_ratio(
                ladder_pair["final_wealth_multiple"],
                baseline_pair["final_wealth_multiple"],
            ),
            "ladder_to_permanent_ratio": _safe_ratio(
                ladder_pair["final_wealth_multiple"],
                permanent_pair["final_wealth_multiple"],
            ),
        }
    return result


def _independent_cycle_counts(frame: pd.DataFrame, executable_levels: pd.Series) -> dict[str, int]:
    filled = pd.to_numeric(executable_levels, errors="coerce").ffill().fillna(0).astype(int)
    dates = pd.to_datetime(frame.loc[filled.gt(0), "date"], errors="raise")

    def count(selected: pd.Series) -> int:
        ordered = selected.drop_duplicates().sort_values()
        if ordered.empty:
            return 0
        gaps = ordered.diff().dt.days.gt(90)
        return 1 + int(gaps.sum())

    return {
        "fit": count(dates.loc[dates.le(pd.Timestamp("2015-12-31"))]),
        "holdout": count(dates.loc[dates.ge(pd.Timestamp("2016-01-01"))]),
        "full": count(dates),
    }


def _base_sweep_thresholds(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    thresholds: list[dict[str, Any]] = []
    for multiple in BASE_SWEEP_MULTIPLES:
        for exit_variant in BASE_SWEEP_EXITS:
            candidates = sorted(
                (
                    row
                    for row in rows
                    if row["leverage_multiple"] == multiple and row["exit"] == exit_variant
                ),
                key=lambda row: float(row["base_exposure"]),
            )
            for period in BASE_SWEEP_PERIODS:
                winner = next(
                    (
                        row
                        for row in candidates
                        if float(row["periods"][period]["ladder_to_baseline_ratio"]) > 1.0
                    ),
                    None,
                )
                metric = winner["periods"][period] if winner is not None else None
                thresholds.append({
                    "leverage_multiple": multiple,
                    "exit": exit_variant,
                    "period": period,
                    "smallest_base_exposure": (
                        float(winner["base_exposure"]) if winner is not None else None
                    ),
                    "beats_permanent_at_threshold": (
                        bool(float(metric["ladder_to_permanent_ratio"]) > 1.0)
                        if metric is not None
                        else None
                    ),
                    "ladder_to_baseline_ratio": (
                        float(metric["ladder_to_baseline_ratio"]) if metric is not None else None
                    ),
                    "ladder_to_permanent_ratio": (
                        float(metric["ladder_to_permanent_ratio"]) if metric is not None else None
                    ),
                })
    return thresholds


def validate_base_sweep_payload(payload: dict[str, Any]) -> None:
    missing = BASE_SWEEP_REQUIRED_FIELDS.difference(payload)
    if missing:
        raise ValueError(f"base sweep payload is missing fields: {sorted(missing)}")
    if payload["schema_version"] != 1 or payload["experiment"] != "compound-ladder/base-exposure-sweep-v1":
        raise ValueError("base sweep payload identity is invalid")
    if payload["api_calls"] != 0:
        raise ValueError("base sweep must remain provider-free")
    counts = payload["independent_cycle_count"]
    if set(counts) != set(BASE_SWEEP_PERIODS) or any(int(value) < 0 for value in counts.values()):
        raise ValueError("base sweep cycle counts are invalid")
    period_required = {
        "ladder_on_base",
        "permanent_base",
        "baseline_1x",
        "ladder_to_baseline_ratio",
        "ladder_to_permanent_ratio",
    }
    metric_required = {"final_wealth_multiple", "max_drawdown"}
    if not payload["rows"] or not payload["references"]:
        raise ValueError("base sweep must contain strategy and permanent reference rows")
    for row in payload["rows"]:
        if row.get("row_kind") != "ladder_on_base":
            raise ValueError("base sweep strategy row_kind is invalid")
        if float(row["base_exposure"]) > int(row["leverage_multiple"]):
            raise ValueError("base sweep row violates base_exposure <= leverage_multiple")
        periods = row.get("periods", {})
        if set(periods) != set(BASE_SWEEP_PERIODS):
            raise ValueError("base sweep strategy periods are incomplete")
        for period in BASE_SWEEP_PERIODS:
            if period_required.difference(periods[period]):
                raise ValueError("base sweep comparison metrics are incomplete")
            for metric_name in ("ladder_on_base", "permanent_base", "baseline_1x"):
                if metric_required.difference(periods[period][metric_name]):
                    raise ValueError("base sweep wealth/drawdown metrics are incomplete")
    for row in payload["references"]:
        if row.get("row_kind") != "permanent_base" or set(row.get("periods", {})) != set(BASE_SWEEP_PERIODS):
            raise ValueError("base sweep permanent reference row is invalid")
    expected_thresholds = len(BASE_SWEEP_MULTIPLES) * len(BASE_SWEEP_EXITS) * len(BASE_SWEEP_PERIODS)
    if len(payload["thresholds"]) != expected_thresholds:
        raise ValueError("base sweep threshold rows are incomplete")


def _require_product_share(value: object) -> float:
    if value is _UNDECIDED or value is None:
        raise ValueError(
            "product_share_at_max is undecided under rule ⑥; caller must pass it explicitly"
        )
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("product_share_at_max must be a finite number")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise ValueError("product_share_at_max must be finite and in [0.0, 1.0]")
    return result


def _is_current(
    row: dict[str, Any],
    drawdown_threshold: float,
    disp60_threshold: float,
    product_share_at_max: float,
    levels: int,
    base_exposure: float,
) -> bool:
    return (
        all(row.get(key) == value for key, value in CURRENT.items())
        and row.get("drawdown_threshold") == drawdown_threshold
        and row.get("disp60_threshold") == disp60_threshold
        and row.get("product_share_at_max") == product_share_at_max
        and row.get("levels") == levels
        and row.get("base_exposure") == base_exposure
    )


def _strategy_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(row[key] for key in (
        "drawdown_threshold",
        "disp60_threshold",
        "levels",
        "leverage_multiple",
        "base_exposure",
        "product_share_at_max",
        "exit",
        "cost_enabled",
    ))


def _build_detailed(
    frame: pd.DataFrame,
    signals: pd.DataFrame,
    product_returns: pd.Series,
    underlying_returns: pd.Series,
    row: dict[str, Any],
) -> tuple[Any, Any]:
    spec = LadderSpec(
        drawdown_threshold=float(row["drawdown_threshold"]),
        disp60_threshold=float(row["disp60_threshold"]),
        product_share_at_max=float(row["product_share_at_max"]),
        levels=int(row["levels"]),
        base_exposure=float(row["base_exposure"]),
    )
    levels = ladder_levels(signals, spec)["executable_level"]
    cost = TRANSACTION_COST if row["cost_enabled"] else 0.0
    baseline = simulate_baseline(frame["date"], underlying_returns, transaction_cost=cost)
    strategy = simulate_account(
        frame["date"],
        product_returns,
        levels,
        underlying_returns=underlying_returns,
        spec=spec,
        leverage_multiple=int(row["leverage_multiple"]),
        exit_variant=str(row["exit"]),
        transaction_cost=cost,
        baseline_curve=baseline.curve,
    )
    return strategy, baseline


def _plateau(
    rows: list[dict[str, Any]],
    *,
    drawdown_threshold: float,
    disp60_threshold: float,
) -> list[dict[str, Any]]:
    strategy = [
        row for row in rows
        if row["row_kind"] == "strategy"
    ]
    definitions = (
        (
            "threshold_x_levels",
            "drawdown_threshold",
            "levels",
            {"disp60_threshold": disp60_threshold, "leverage_multiple": 2, "exit": "a", "cost_enabled": True},
        ),
        (
            "levels_x_multiple",
            "levels",
            "leverage_multiple",
            {"drawdown_threshold": drawdown_threshold, "disp60_threshold": disp60_threshold, "exit": "a", "cost_enabled": True},
        ),
        (
            "threshold_x_multiple",
            "drawdown_threshold",
            "leverage_multiple",
            {"disp60_threshold": disp60_threshold, "levels": 2, "exit": "a", "cost_enabled": True},
        ),
    )
    output: list[dict[str, Any]] = []
    shares = {
        float(row["product_share_at_max"])
        for row in strategy
        if isinstance(row.get("product_share_at_max"), (int, float))
    }
    if len(shares) > 1:
        raise ValueError("plateau rows must fix product_share_at_max")
    for name, x_key, y_key, fixed in definitions:
        scenario = {
            **fixed,
            "base_exposure": 1.0,
            "product_variant": "synthetic",
        }
        if shares:
            scenario["product_share_at_max"] = next(iter(shares))
        try:
            best = select_best_in_scenario(strategy, scenario=scenario)
        except ValueError as error:
            if str(error) == "no rows match scenario with a finite metric":
                continue
            raise
        selected = [
            row for row in strategy
            if all(row[key] == value for key, value in scenario.items())
        ]
        selected = [
            row for row in selected
            if row["fit"]["relative_to_baseline"] is not None
            and math.isfinite(float(row["fit"]["relative_to_baseline"]))
        ]
        xs = sorted({row[x_key] for row in selected})
        ys = sorted({row[y_key] for row in selected})
        xi = xs.index(best[x_key])
        yi = ys.index(best[y_key])
        neighbours = [
            row for row in selected
            if row is not best
            and abs(xs.index(row[x_key]) - xi) <= 1
            and abs(ys.index(row[y_key]) - yi) <= 1
        ]
        neighbour_mean = (
            float(np.mean([row["fit"]["relative_to_baseline"] for row in neighbours]))
            if neighbours
            else float("nan")
        )
        best_value = float(best["fit"]["relative_to_baseline"])
        edge = best_value - 1.0
        sharp = bool(edge > 0 and best_value - neighbour_mean > 0.25 * edge)
        output.append({
            "surface": name,
            "x": x_key,
            "y": y_key,
            "best_x": best[x_key],
            "best_y": best[y_key],
            "best_fit_relative_to_baseline": best_value,
            "neighbour_count": len(neighbours),
            "neighbourhood_mean": neighbour_mean,
            "sharp_peak": sharp,
            "fixed": {
                **fixed,
                **(
                    {"product_share_at_max": next(iter(shares))}
                    if shares else {}
                ),
            },
        })
    return output


def _grid_for_series(
    root: Path,
    basket: str,
    underlying: str,
    frame: pd.DataFrame,
    real_products: pd.DataFrame,
    grid: dict[str, tuple[Any, ...]],
    *,
    current_drawdown_threshold: float,
    current_disp60_threshold: float,
    current_levels: int,
    current_base_exposure: float,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    product_shares = grid.get("product_share_at_max")
    if not product_shares:
        raise ValueError(
            "product_share_at_max is undecided under rule ⑥; caller must pass it explicitly"
        )
    if len(product_shares) != 1:
        raise ValueError("compound grid requires exactly one explicit product_share_at_max")
    product_share = _require_product_share(product_shares[0])
    signals = compute_signals(frame)
    underlying_returns = frame["close"].pct_change(fill_method=None).fillna(0.0)
    short_rate = load_short_rate(root, frame["date"])
    rate_for_returns = pd.Series(short_rate.annual_rate.to_numpy(), index=frame.index)
    gaps: dict[int, Any] = {}
    for multiple in (1, 2, 3):
        gap = realized_tracking_gap(
            frame,
            real_products,
            underlying=underlying,
            leverage_multiple=multiple,
            short_rate=short_rate,
        )
        if gap is not None:
            gaps[multiple] = gap

    returns_by_variant: dict[tuple[int, float], pd.Series] = {}
    drag_rows: dict[str, dict[str, float]] = {}
    for multiple in grid["leverage_multiple"]:
        plain = synthetic_daily_returns(
            frame["close"],
            leverage_multiple=int(multiple),
            annual_short_rate=rate_for_returns,
        )
        returns_by_variant[(int(multiple), 0.0)] = plain
        drag_rows[str(multiple)] = volatility_drag(frame["close"], plain, int(multiple))
        if int(multiple) in gaps and gaps[int(multiple)].calibrated_extra_drag > 0:
            calibrated_drag = gaps[int(multiple)].calibrated_extra_drag
            returns_by_variant[(int(multiple), calibrated_drag)] = synthetic_daily_returns(
                frame["close"],
                leverage_multiple=int(multiple),
                annual_short_rate=rate_for_returns,
                annual_tracking_drag=calibrated_drag,
            )

    baseline_cache: dict[bool, dict[str, dict[str, Any]]] = {}
    level_cache: dict[tuple[float, float, int], pd.Series] = {}
    rows: list[dict[str, Any]] = []
    current_detail: tuple[Any, Any] | None = None
    for dd in grid["drawdown_threshold"]:
        for disp in grid["disp60_threshold"]:
            for levels_count in grid["levels"]:
                signal_spec = LadderSpec(
                    drawdown_threshold=float(dd),
                    disp60_threshold=float(disp),
                    product_share_at_max=float(product_shares[0]),
                    levels=int(levels_count),
                    base_exposure=current_base_exposure,
                )
                cache_key = (float(dd), float(disp), int(levels_count))
                levels = level_cache.setdefault(
                    cache_key, ladder_levels(signals, signal_spec)["executable_level"]
                )
                for multiple in grid["leverage_multiple"]:
                    returns = returns_by_variant[(int(multiple), 0.0)]
                    for base_exposure in grid["base_exposure"]:
                        spec = LadderSpec(
                            drawdown_threshold=float(dd),
                            disp60_threshold=float(disp),
                            product_share_at_max=float(product_share),
                            levels=int(levels_count),
                            base_exposure=float(base_exposure),
                        )
                        for exit_variant in grid["exit"]:
                            for cost_enabled in grid["cost_enabled"]:
                                cost = TRANSACTION_COST if cost_enabled else 0.0
                                baseline = baseline_cache.setdefault(
                                    bool(cost_enabled),
                                    _baseline_metrics(
                                        frame["date"], underlying_returns, transaction_cost=cost
                                    ),
                                )
                                metrics = (
                                    baseline
                                    if spec.base_exposure == 1.0 and int(multiple) == 1
                                    else simulate_grid_metrics(
                                        frame["date"],
                                        returns,
                                        levels,
                                        underlying_returns=underlying_returns,
                                        spec=spec,
                                        leverage_multiple=int(multiple),
                                        exit_variant=str(exit_variant),
                                        transaction_cost=cost,
                                    )
                                )
                                row: dict[str, Any] = {
                                    "row_kind": "strategy",
                                    "basket": basket,
                                    "underlying": underlying,
                                    "drawdown_threshold": float(dd),
                                    "disp60_threshold": float(disp),
                                    "levels": int(levels_count),
                                    "leverage_multiple": int(multiple),
                                    "base_exposure": float(base_exposure),
                                    "product_share_at_max": float(product_share),
                                    "effective_exposure_max": effective_exposure_at_max(
                                        spec, int(multiple)
                                    ),
                                    "exit": str(exit_variant),
                                    "cost_enabled": bool(cost_enabled),
                                    "transaction_cost_one_way": cost,
                                    "cash_yield": 0.0,
                                    "product_variant": "synthetic",
                                    **with_baseline_comparison(metrics, baseline),
                                    "actual_product_basis": None,
                                    "curve_tags": [],
                                    "equity_curve_weekly": None,
                                    "cycles": None,
                                }
                                gap = gaps.get(int(multiple))
                                if gap is not None:
                                    calibrated_drag = gap.calibrated_extra_drag
                                    calibrated_returns = returns_by_variant.get(
                                        (int(multiple), calibrated_drag), returns
                                    )
                                    actual_metrics = simulate_grid_metrics(
                                        frame["date"],
                                        calibrated_returns,
                                        levels,
                                        underlying_returns=underlying_returns,
                                        spec=spec,
                                        leverage_multiple=int(multiple),
                                        exit_variant=str(exit_variant),
                                        transaction_cost=cost,
                                    )
                                    actual_comparison = with_baseline_comparison(
                                        actual_metrics, baseline
                                    )
                                    row["actual_product_basis"] = {
                                        "product_symbol": gap.product_symbol,
                                        "annualized_gap": gap.annualized_gap,
                                        "calibrated_extra_drag": calibrated_drag,
                                        "fit": actual_comparison["fit"],
                                        "holdout": actual_comparison["holdout"],
                                        "full": actual_comparison["full"],
                                    }
                                validate_grid_row(row)
                                rows.append(row)

    candidates = [
        row for row in rows
        if row["row_kind"] == "strategy" and row["base_exposure"] == 1.0
    ]
    best = max(
        candidates,
        key=lambda row: (
            float(row["fit"]["relative_to_baseline"])
            if row["fit"]["relative_to_baseline"] is not None
            and math.isfinite(float(row["fit"]["relative_to_baseline"]))
            else -math.inf
        ),
    )
    product_share_at_max = float(product_shares[0])
    current = next((
        row for row in rows if _is_current(
            row,
            current_drawdown_threshold,
            current_disp60_threshold,
            product_share_at_max,
            current_levels,
            current_base_exposure,
        )
    ), None)
    if current is None:
        # Quick mode still includes the exact current row; keep the guard explicit.
        raise RuntimeError("grid omitted the predeclared current rule")

    for tag, selected in (("current_rule", current), ("best_fit_exploratory", best)):
        multiple = int(selected["leverage_multiple"])
        returns = returns_by_variant[(multiple, 0.0)]
        strategy, baseline_detail = _build_detailed(
            frame, signals, returns, underlying_returns, selected
        )
        selected["curve_tags"].append(tag)
        selected["equity_curve_weekly"] = weekly_curve(strategy.curve)
        selected["cycles"] = _json_value(strategy.cycles.to_dict("records"))
        if tag == "current_rule":
            current_detail = (strategy, baseline_detail)
    assert current_detail is not None
    current_strategy, current_baseline = current_detail
    current_spec = LadderSpec(
        drawdown_threshold=current_drawdown_threshold,
        disp60_threshold=current_disp60_threshold,
        product_share_at_max=product_share_at_max,
        levels=current_levels,
        base_exposure=current_base_exposure,
    )
    current_executable_levels = level_cache[(
        current_drawdown_threshold, current_disp60_threshold, current_levels
    )]
    underlying_baseline_metrics = _baseline_metrics(
        frame["date"], underlying_returns, transaction_cost=TRANSACTION_COST
    )
    underlying_strategy_metrics = simulate_grid_metrics(
        frame["date"],
        underlying_returns,
        current_executable_levels,
        underlying_returns=underlying_returns,
        spec=current_spec,
        leverage_multiple=2,
        exit_variant="a",
        transaction_cost=TRANSACTION_COST,
    )
    baseline_row = {
        "row_kind": "baseline",
        "basket": basket,
        "underlying": underlying,
        "drawdown_threshold": None,
        "disp60_threshold": None,
        "levels": None,
        "leverage_multiple": 1,
        "base_exposure": 1.0,
        "product_share_at_max": None,
        "effective_exposure_max": 1.0,
        "exit": None,
        "cost_enabled": True,
        "transaction_cost_one_way": TRANSACTION_COST,
        "cash_yield": 0.0,
        "product_variant": "underlying_1x",
        "fit": current_baseline.metrics["fit"],
        "holdout": current_baseline.metrics["holdout"],
        "full": current_baseline.metrics["full"],
        "actual_product_basis": None,
        "curve_tags": ["baseline"],
        "equity_curve_weekly": weekly_curve(current_baseline.curve),
        "cycles": _json_value(current_strategy.cycles[[
            "episode", "entry_date", "max_level_reached", "signal_end_date", "exit_date", "baseline_contribution"
        ]].rename(columns={"baseline_contribution": "contribution_to_wealth"}).to_dict("records")) if not current_strategy.cycles.empty else [],
    }
    validate_grid_row(baseline_row)
    rows.append(baseline_row)
    metadata = {
        "headline": current,
        "exploratory_best_fit": best,
        "plateau": _plateau(
            rows,
            drawdown_threshold=current_drawdown_threshold,
            disp60_threshold=current_disp60_threshold,
        ),
        "tracking_gaps": {str(key): asdict(value) for key, value in gaps.items()},
        "volatility_drag": drag_rows,
        "short_rate_source": short_rate.source,
        "short_rate_fallback_used": short_rate.fallback_used,
        "underlying_index_basis": with_baseline_comparison(
            underlying_strategy_metrics, underlying_baseline_metrics
        ),
        "rows": len(rows),
        "grid_schema_required_fields": list(GRID_REQUIRED_FIELDS),
    }
    return rows, metadata


def _fmt_multiple(value: Any) -> str:
    return "N/A" if value is None else f"{float(value):.3f}x"


def _fmt_pct(value: Any) -> str:
    return "N/A" if value is None else f"{float(value) * 100:.1f}%"


def _table(headers: Iterable[str], rows: Iterable[Iterable[Any]]) -> str:
    header = list(headers)
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    lines.extend("| " + " | ".join(str(value) for value in row) + " |" for row in rows)
    return "\n".join(lines)


def _results_markdown(
    summary: dict[str, Any],
    rows_by_underlying: dict[str, list[dict[str, Any]]],
) -> str:
    current_drawdown = float(summary["current_rule"]["drawdown_threshold"])
    current_disp60 = float(summary["current_rule"]["disp60_threshold"])
    headlines = [row for rows in summary["baskets"].values() for row in rows]
    q1_rows = [
        (symbol, "N/A", "N/A", "별도 시험(foreign_transfer)로 이관")
        for symbol in FOREIGN_SYMBOLS
    ]

    q2_rows = []
    for item in summary["baskets"].get("KR", []):
        eligible = [
            row for row in rows_by_underlying[item["underlying"]]
            if row["row_kind"] == "strategy"
            and row["drawdown_threshold"] == current_drawdown
            and row["disp60_threshold"] == current_disp60
            and row["levels"] == 2
            and row["leverage_multiple"] == 2
            and row["base_exposure"] == 1.0
            and row["exit"] == "a"
        ]
        without_cost = next(
            (row for row in eligible if row["cost_enabled"] is False), None
        )
        with_cost = next(
            (row for row in eligible if row["cost_enabled"] is True), None
        )
        if without_cost is None or with_cost is None:
            continue
        actual_without_cost = without_cost["actual_product_basis"]
        actual_with_cost = with_cost["actual_product_basis"]
        if actual_without_cost is not None and actual_with_cost is not None:
            q2_rows.append((
                item["underlying"],
                actual_without_cost["product_symbol"],
                _fmt_multiple(without_cost["holdout"]["relative_to_baseline"]),
                _fmt_multiple(actual_without_cost["holdout"]["relative_to_baseline"]),
                _fmt_multiple(actual_with_cost["holdout"]["relative_to_baseline"]),
            ))
    if not q2_rows:
        q2_rows = [("N/A", "N/A", "N/A", "N/A", "N/A")]

    q3_rows = []
    for item in headlines:
        for surface in item["plateau"]:
            q3_rows.append((
                item["underlying"],
                surface["surface"],
                f"{surface['best_x']} × {surface['best_y']}",
                _fmt_multiple(surface["best_fit_relative_to_baseline"]),
                _fmt_multiple(surface["neighbourhood_mean"]),
                "sharp peak" if surface["sharp_peak"] else "plateau/완만",
            ))

    exit_rows = []
    for item in headlines:
        for rank, row in enumerate(item["exit_ranking"], start=1):
            exit_rows.append((
                item["underlying"], rank, row["exit"],
                _fmt_multiple(row["final_wealth_multiple"]),
                _fmt_pct(row["max_drawdown"]),
                _fmt_multiple(row["relative_to_baseline"]),
            ))

    surface_rows = []
    for basket, cells in summary["split_multiple_surface"].items():
        for cell in cells:
            surface_rows.append((
                basket, cell["levels"], cell["leverage_multiple"],
                _fmt_multiple(cell["median_holdout_relative_to_baseline"]),
                cell["n"],
            ))
    peak = max(
        (cell | {"basket": basket} for basket, cells in summary["split_multiple_surface"].items() for cell in cells),
        key=lambda item: item["median_holdout_relative_to_baseline"],
        default=None,
    )
    headline_period_rows = []
    underlying_rows = []
    cycle_sections: list[str] = []
    for item in headlines:
        for period in ("fit", "holdout", "full"):
            metric = item["headline"][period]
            headline_period_rows.append((
                item["underlying"], period,
                _fmt_multiple(metric["final_wealth_multiple"]),
                _fmt_multiple(metric["baseline_final_wealth_multiple"]),
                _fmt_multiple(metric["relative_to_baseline"]),
                _fmt_pct(metric["cagr"]),
                _fmt_pct(metric["max_drawdown"]),
            ))
        underlying = item["underlying_index_basis"]
        for period in ("fit", "holdout", "full"):
            metric = underlying[period]
            underlying_rows.append((
                item["underlying"], period,
                _fmt_multiple(metric["final_wealth_multiple"]),
                _fmt_multiple(metric["baseline_final_wealth_multiple"]),
                _fmt_multiple(metric["relative_to_baseline"]),
            ))
        cycles = item["headline"].get("cycles") or []
        cycle_sections.extend([
            f"### {item['underlying']}",
            "",
            _table(
                ("episode", "entry", "max level", "signal end", "actual exit", "전략 기여", "baseline 기여"),
                (
                    (
                        cycle["episode"], cycle["entry_date"], cycle["max_level_reached"],
                        cycle["signal_end_date"], cycle["exit_date"] or "open",
                        _fmt_pct(cycle["contribution_to_wealth"]),
                        _fmt_pct(cycle["baseline_contribution"]),
                    )
                    for cycle in cycles
                ),
            ),
            "",
        ])
    plateau_count = sum(
        not surface["sharp_peak"]
        for item in headlines for surface in item["plateau"]
        if surface["surface"] == "levels_x_multiple"
    )
    lines = [
        "# 복리 사다리 백테스트 결과",
        "",
        "> 개발용 retained-data 시뮬레이션입니다. 현재 규칙(`kr_dd_ladder_2`, k=2, exit a)이 헤드라인이며 그리드 최적값은 탐색 결과일 뿐입니다.",
        "",
        "## 1) 해외 지수에 숫자 하나 안 고친 규칙을 적용하면",
        "",
        _table(("지수", "hold-out 최종배수", "항상보유", "baseline 초과"), q1_rows),
        "",
        "결론: FOREIGN은 이 실행에서 계산하지 않았고 사용자가 지정한 별도 `foreign_transfer` 시험으로 이관했습니다.",
        "",
        "## 2) KR 2x overlay에 추적갭과 거래비용을 순서대로 넣으면",
        "",
        _table(("기초지수", "실상품", "synthetic / baseline", "추적갭 반영 / baseline", "추적갭+비용 / baseline"), q2_rows),
        "",
        "결론: 세 열은 모두 hold-out 최종부의 전략/1x 항상보유 배수이며, synthetic 무비용 → 보정 추적갭 무비용 → 보정 추적갭과 양방향 rebalance 비용 순서입니다.",
        "",
        "## 3) 임계값 주변은 plateau인가 peak인가",
        "",
        _table(("지수", "2-D surface", "fit 최적 셀", "최적/기준", "8-neighbour 평균", "판정"), q3_rows),
        "",
        f"결론: 정의된 25% edge 규칙으로 {sum(row[-1] == 'sharp peak' for row in q3_rows)}개 surface가 sharp peak였고 나머지는 완만했습니다.",
        "",
        "## 4) 매도 방식 a–d의 hold-out 최종부 순위",
        "",
        _table(("지수", "순위", "exit", "최종배수", "MDD", "baseline 대비"), exit_rows),
        "",
        "결론: 순위는 현재 임계값·2분할·k=2·비용 포함으로 고정한 뒤 최종부만 비교했습니다. b60/b120은 별도 행입니다.",
        "",
        "## 5) 분할 수 × multiple surface",
        "",
        _table(("basket", "분할 수", "multiple", "hold-out median/기준", "지수 수"), surface_rows),
        "",
        (
            f"결론: 중앙값 peak는 {peak['basket']}의 {peak['levels']}분할 × {peak['leverage_multiple']}배({_fmt_multiple(peak['median_holdout_relative_to_baseline'])})였고, "
            f"levels×multiple surface {plateau_count}개는 sharp peak가 아니었습니다. 따라서 3분할 자체를 특별한 값으로 확정하지 않습니다."
            if peak else "결론: 비교 가능한 surface가 없습니다."
        ),
        "",
        "## 헤드라인 fit/hold-out/full 최종부",
        "",
        _table(("지수", "기간", "전략", "항상보유", "baseline 대비", "CAGR", "MDD"), headline_period_rows),
        "",
        "## 기초지수(1x, 상품비용 없음) 비교",
        "",
        _table(("지수", "기간", "전략", "항상보유", "baseline 대비"), underlying_rows),
        "",
        "## 변동성 drag",
        "",
        _table(
            ("지수", "k", "지수 항상보유", "일일리셋 synthetic", "단순 k×", "차이"),
            (
                (item["underlying"], k, _fmt_multiple(v["index_final_multiple"]), _fmt_multiple(v["synthetic_final_multiple"]), _fmt_multiple(v["linear_multiple_of_index_buy_hold"]), f"{v['volatility_and_cost_drag_multiple']:+.3f}x")
                for item in headlines for k, v in item["volatility_drag"].items()
            ),
        ),
        "",
        "## 부록: current rule cycle별 복리 기여",
        "",
        "전략 episode와 같은 날짜의 always-invested baseline 기여를 나란히 적었습니다. 전체 weekly equity curve는 각 grid JSON의 `current_rule` 및 `baseline` tagged row에 있습니다.",
        "",
        *cycle_sections,
        "## 한계",
        "",
        "- 실 ETF 겹침은 약 5년(2021-09 이후)에 불과하고 배당·분배금이 완전히 반영되지 않은 close 기준입니다.",
        "- 과거 전체의 ‘실제 상품 기준’은 짧은 겹침 구간의 연환산 음의 추적갭을 일정한 추가 drag로 놓은 가정입니다.",
        "- 지수 종가의 원천 빈티지·당시 공개시각을 완전 재현하지 못하므로 index close 자체의 역사적 PIT 안전성을 주장하지 않습니다.",
        "- 종가 T 신호는 다음 retained session 종가에서만 실행해 계산상 look-ahead를 막았지만 실제 체결 가능 가격·유동성·세금·환율은 모델링하지 않았습니다.",
        "- 표본은 소수의 drawdown cycle이며 그리드 최적값은 multiple testing에 노출된 탐색 결과입니다.",
        "",
        f"입력 manifest: `{summary['input_manifest_sha256']}` · API calls: `0` · short rate: `{summary['short_rate_sources']}`",
        "",
    ]
    return "\n".join(lines)


def _surface_summary(
    basket_items: list[dict[str, Any]],
    all_rows: dict[str, list[dict[str, Any]]],
    *,
    drawdown_threshold: float,
    disp60_threshold: float,
) -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = []
    for levels_count in (1, 2, 3, 4):
        for multiple in (1, 2, 3):
            values: list[float] = []
            for item in basket_items:
                for row in all_rows[item["underlying"]]:
                    if (
                        row["row_kind"] == "strategy"
                        and row["drawdown_threshold"] == drawdown_threshold
                        and row["disp60_threshold"] == disp60_threshold
                        and row["levels"] == levels_count
                        and row["leverage_multiple"] == multiple
                        and row["base_exposure"] == 1.0
                        and row["exit"] == "a"
                        and row["cost_enabled"] is True
                    ):
                        value = row["holdout"]["relative_to_baseline"]
                        if value is not None and math.isfinite(float(value)):
                            values.append(float(value))
            if values:
                cells.append({
                    "levels": levels_count,
                    "leverage_multiple": multiple,
                    "median_holdout_relative_to_baseline": float(np.median(values)),
                    "n": len(values),
                })
    return cells


def run(
    project_root: Path,
    baskets: tuple[str, ...],
    *,
    quick: bool,
    drawdown_threshold: float | object = _UNDECIDED,
    disp60_threshold: float | object = _UNDECIDED,
    product_share_at_max: float | object = _UNDECIDED,
    levels: int | object = _UNDECIDED,
    base_exposure: float | object = _UNDECIDED,
) -> dict[str, Any]:
    started = time.perf_counter()
    root = project_root.resolve()
    output = root / "artifacts/research/compound_ladder"
    decided_drawdown = require_drawdown_threshold(drawdown_threshold)
    decided_disp60 = require_disp60_threshold(disp60_threshold)
    decided_share = _require_product_share(product_share_at_max)
    decided_levels = require_levels(levels)
    decided_base_exposure = require_base_exposure(base_exposure)
    selected_grid = QUICK_GRID if quick else FULL_GRID
    grid = {
        **selected_grid,
        "drawdown_threshold": tuple(dict.fromkeys(
            (*selected_grid["drawdown_threshold"], decided_drawdown)
        )),
        "disp60_threshold": tuple(dict.fromkeys(
            (*selected_grid["disp60_threshold"], decided_disp60)
        )),
        "product_share_at_max": (decided_share,),
        "levels": tuple(dict.fromkeys((*selected_grid["levels"], decided_levels))),
        "base_exposure": tuple(dict.fromkeys(
            (*selected_grid["base_exposure"], decided_base_exposure)
        )),
    }
    universe = load_index_universe(root)
    real_products = load_real_products(root)
    available = set(universe["series_id"].astype(str))
    missing: dict[str, list[str]] = {}
    summary_baskets: dict[str, list[dict[str, Any]]] = {basket: [] for basket in baskets}
    rows_by_underlying: dict[str, list[dict[str, Any]]] = {}
    artifact_paths: list[str] = []

    for basket in baskets:
        for underlying in BASKET_SERIES[basket]:
            if underlying not in available:
                missing.setdefault(basket, []).append(underlying)
                print(f"SKIP {basket}/{underlying}: retained symbol absent")
                continue
            frame = universe.loc[universe["series_id"].eq(underlying)].copy().reset_index(drop=True)
            rows, metadata = _grid_for_series(
                root,
                basket,
                underlying,
                frame,
                real_products,
                grid,
                current_drawdown_threshold=decided_drawdown,
                current_disp60_threshold=decided_disp60,
                current_levels=decided_levels,
                current_base_exposure=decided_base_exposure,
            )
            rows_by_underlying[underlying] = rows
            path = output / f"grid_{_slug(basket)}_{_slug(underlying)}.json"
            _write_json(path, rows)
            artifact_paths.append(path.relative_to(root).as_posix())
            exit_candidates = [
                row for row in rows
                if row["row_kind"] == "strategy"
                and row["drawdown_threshold"] == decided_drawdown
                and row["disp60_threshold"] == decided_disp60
                and row["levels"] == decided_levels
                and row["leverage_multiple"] == 2
                and row["base_exposure"] == decided_base_exposure
                and row["cost_enabled"] is True
            ]
            ranking = []
            for row in exit_candidates:
                actual = row.get("actual_product_basis")
                metric = actual["holdout"] if actual is not None else row["holdout"]
                ranking.append({
                    "exit": row["exit"],
                    "base_exposure": row["base_exposure"],
                    "product_share_at_max": row["product_share_at_max"],
                    "effective_exposure_max": row["effective_exposure_max"],
                    **metric,
                })
            ranking.sort(key=lambda item: -math.inf if item["final_wealth_multiple"] is None else float(item["final_wealth_multiple"]), reverse=True)
            real_product_rows = [
                {
                    "leverage_multiple": row["leverage_multiple"],
                    "product_share_at_max": row["product_share_at_max"],
                    "effective_exposure_max": row["effective_exposure_max"],
                    "actual_product_basis": row["actual_product_basis"],
                }
                for row in rows
                if row["row_kind"] == "strategy"
                and row["drawdown_threshold"] == decided_drawdown
                and row["disp60_threshold"] == decided_disp60
                and row["levels"] == decided_levels
                and row["base_exposure"] == decided_base_exposure
                and row["exit"] == "a"
                and row["cost_enabled"] is True
                and row["actual_product_basis"] is not None
            ]
            summary_baskets[basket].append({
                "underlying": underlying,
                "headline": metadata["headline"],
                "exploratory_best_fit": {
                    key: metadata["exploratory_best_fit"][key]
                    for key in (
                        "drawdown_threshold", "disp60_threshold", "levels", "leverage_multiple",
                        "base_exposure", "product_share_at_max", "effective_exposure_max",
                        "exit", "cost_enabled", "fit", "holdout", "full"
                    )
                },
                "plateau": metadata["plateau"],
                "tracking_gaps": metadata["tracking_gaps"],
                "volatility_drag": metadata["volatility_drag"],
                "underlying_index_basis": metadata["underlying_index_basis"],
                "short_rate_source": metadata["short_rate_source"],
                "exit_ranking": ranking,
                "real_product_rows": real_product_rows,
                "grid_path": path.relative_to(root).as_posix(),
            })
            print(f"DONE {basket}/{underlying}: {len(rows)} rows")

    retained_paths = [
        Path("data/normalized/kr_index_daily"),
        Path("data/normalized/kr_kospi200_index_daily"),
        Path("data/normalized/global_index_price_daily"),
        Path("data/normalized/global_etf_price_daily"),
        Path("data/normalized/kr_etf_price_daily"),
        Path("data/normalized/kr_etf_master"),
        *[path.relative_to(root) for path in sorted((root / "data/normalized").glob("fred_*"))],
    ]
    manifest_digest, manifest = retained_manifest_digest(root, retained_paths)
    surface = {
        basket: _surface_summary(
            items,
            rows_by_underlying,
            drawdown_threshold=decided_drawdown,
            disp60_threshold=decided_disp60,
        )
        for basket, items in summary_baskets.items()
    }
    short_sources = sorted({item["short_rate_source"] for items in summary_baskets.values() for item in items})
    summary = {
        "schema_version": 1,
        "experiment": "compound-ladder/v2",
        "development_only": True,
        "api_calls": 0,
        "quick": quick,
        "fit_window": {"end": "2015-12-31"},
        "holdout_window": {"start": "2016-01-01"},
        "current_rule": {
            **CURRENT,
            "drawdown_threshold": decided_drawdown,
            "disp60_threshold": decided_disp60,
            "product_share_at_max": decided_share,
            "levels": decided_levels,
            "base_exposure": decided_base_exposure,
        },
        "headline_policy": "current_rule_not_grid_winner",
        "input_manifest_sha256": manifest_digest,
        "input_manifest": manifest,
        "short_rate_sources": short_sources,
        "missing_symbols": missing,
        "baskets": summary_baskets,
        "split_multiple_surface": surface,
        "grid_artifacts": artifact_paths,
        "runtime_seconds": time.perf_counter() - started,
    }
    _write_json(output / "summary.json", summary)
    _write_text(
        root / "docs/research/RESULTS_20260905_compound_ladder.md",
        _results_markdown(_json_value(summary), _json_value(rows_by_underlying)),
    )
    return _json_value(summary)


def _base_sweep_series(
    root: Path,
    basket: str,
    underlying: str,
    frame: pd.DataFrame,
    real_products: pd.DataFrame,
    base_exposures: tuple[float, ...],
    *,
    drawdown_threshold: float,
    disp60_threshold: float,
    product_share_at_max: float,
    levels: int,
    signal_base_exposure: float,
    quick: bool,
    manifest_digest: str,
    manifest: list[dict[str, Any]],
) -> dict[str, Any]:
    started = time.perf_counter()
    signals = compute_signals(frame)
    signal_spec = LadderSpec(
        drawdown_threshold=drawdown_threshold,
        disp60_threshold=disp60_threshold,
        product_share_at_max=product_share_at_max,
        levels=levels,
        base_exposure=signal_base_exposure,
    )
    executable_levels = ladder_levels(signals, signal_spec)["executable_level"]
    no_ladder_levels = pd.Series(np.zeros(len(frame)), index=frame.index, dtype="float64")
    underlying_returns = frame["close"].pct_change(fill_method=None).fillna(0.0)
    baseline = _baseline_metrics(
        frame["date"],
        underlying_returns,
        transaction_cost=TRANSACTION_COST,
    )
    short_rate = load_short_rate(root, frame["date"])
    rate_for_returns = pd.Series(short_rate.annual_rate.to_numpy(), index=frame.index)
    rows: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []
    calibration: dict[str, dict[str, Any]] = {}

    for multiple in BASE_SWEEP_MULTIPLES:
        gap = realized_tracking_gap(
            frame,
            real_products,
            underlying=underlying,
            leverage_multiple=multiple,
            short_rate=short_rate,
        )
        extra_drag = gap.calibrated_extra_drag if gap is not None else 0.0
        product_returns = synthetic_daily_returns(
            frame["close"],
            leverage_multiple=multiple,
            annual_short_rate=rate_for_returns,
            annual_tracking_drag=extra_drag,
        )
        calibration[str(multiple)] = {
            "enabled": True,
            "available": gap is not None,
            "applied": gap is not None,
            "reason_if_unavailable": (
                None if gap is not None else "no retained mapped real-product overlap"
            ),
            "tracking_gap": asdict(gap) if gap is not None else None,
        }
        for base_exposure in base_exposures:
            if base_exposure > multiple:
                continue
            spec = LadderSpec(
                drawdown_threshold=drawdown_threshold,
                disp60_threshold=disp60_threshold,
                product_share_at_max=product_share_at_max,
                levels=levels,
                base_exposure=base_exposure,
            )
            permanent = simulate_grid_metrics(
                frame["date"],
                product_returns,
                no_ladder_levels,
                underlying_returns=underlying_returns,
                spec=spec,
                leverage_multiple=multiple,
                exit_variant="a",
                transaction_cost=TRANSACTION_COST,
            )
            references.append({
                "row_kind": "permanent_base",
                "leverage_multiple": multiple,
                "base_exposure": base_exposure,
                "product_share_at_max": product_share_at_max,
                "effective_exposure_max": effective_exposure_at_max(spec, multiple),
                "calibration_applied": gap is not None,
                "periods": {
                    period: _metric_pair(permanent[period])
                    for period in BASE_SWEEP_PERIODS
                },
            })
            for exit_variant in BASE_SWEEP_EXITS:
                ladder = simulate_grid_metrics(
                    frame["date"],
                    product_returns,
                    executable_levels,
                    underlying_returns=underlying_returns,
                    spec=spec,
                    leverage_multiple=multiple,
                    exit_variant=exit_variant,
                    transaction_cost=TRANSACTION_COST,
                )
                rows.append({
                    "row_kind": "ladder_on_base",
                    "leverage_multiple": multiple,
                    "base_exposure": base_exposure,
                    "product_share_at_max": product_share_at_max,
                    "effective_exposure_max": effective_exposure_at_max(spec, multiple),
                    "exit": exit_variant,
                    "calibration_applied": gap is not None,
                    "periods": _comparison_periods(ladder, permanent, baseline),
                })

    payload: dict[str, Any] = {
        "schema_version": 1,
        "experiment": "compound-ladder/base-exposure-sweep-v1",
        "development_only": True,
        "api_calls": 0,
        "quick": quick,
        "basket": basket,
        "underlying": underlying,
        "parameters": {
            "drawdown_threshold": drawdown_threshold,
            "disp60_threshold": disp60_threshold,
            "levels": levels,
            "leverage_multiples": list(BASE_SWEEP_MULTIPLES),
            "base_exposures": list(base_exposures),
            "product_share_at_max": product_share_at_max,
            "exits": list(BASE_SWEEP_EXITS),
            "cost_enabled": True,
            "transaction_cost_one_way": TRANSACTION_COST,
            "calibrated_real_product_gap_enabled": True,
            "fit_end": "2015-12-31",
            "holdout_start": "2016-01-01",
        },
        "input_manifest_sha256": manifest_digest,
        "input_manifest": manifest,
        "calibration": calibration,
        "short_rate_source": short_rate.source,
        "short_rate_fallback_used": short_rate.fallback_used,
        "independent_cycle_count": _independent_cycle_counts(frame, executable_levels),
        "references": references,
        "rows": rows,
        "thresholds": _base_sweep_thresholds(rows),
        "runtime_seconds": time.perf_counter() - started,
    }
    validate_base_sweep_payload(payload)
    return _json_value(payload)


def _base_sweep_markdown(payloads: list[dict[str, Any]], runtime_seconds: float) -> str:
    threshold_rows: list[tuple[Any, ...]] = []
    detail_rows: list[tuple[Any, ...]] = []
    reference_rows: list[tuple[Any, ...]] = []
    cycle_rows: list[tuple[Any, ...]] = []
    calibration_rows: list[tuple[Any, ...]] = []
    for payload in payloads:
        label = f"{payload['basket']}/{payload['underlying']}"
        counts = payload["independent_cycle_count"]
        cycle_rows.append((label, counts["fit"], counts["holdout"], counts["full"]))
        for multiple, calibration in payload["calibration"].items():
            gap = calibration["tracking_gap"]
            calibration_rows.append((
                label,
                multiple,
                "적용" if calibration["applied"] else "불가",
                gap["product_symbol"] if gap is not None else "N/A",
                _fmt_pct(gap["annualized_gap"]) if gap is not None else "N/A",
                _fmt_pct(gap["calibrated_extra_drag"]) if gap is not None else "N/A",
            ))
        for threshold in payload["thresholds"]:
            threshold_rows.append((
                label,
                threshold["leverage_multiple"],
                threshold["exit"],
                threshold["period"],
                (
                    f"{threshold['smallest_base_exposure']:.1f}x"
                    if threshold["smallest_base_exposure"] is not None
                    else "없음"
                ),
                (
                    "예" if threshold["beats_permanent_at_threshold"] is True
                    else "아니오" if threshold["beats_permanent_at_threshold"] is False
                    else "N/A"
                ),
                _fmt_multiple(threshold["ladder_to_baseline_ratio"]),
                _fmt_multiple(threshold["ladder_to_permanent_ratio"]),
            ))
        for row in payload["rows"]:
            for period in BASE_SWEEP_PERIODS:
                metric = row["periods"][period]
                ladder = metric["ladder_on_base"]
                permanent = metric["permanent_base"]
                baseline = metric["baseline_1x"]
                detail_rows.append((
                    label,
                    row["leverage_multiple"],
                    f"{row['base_exposure']:.1f}",
                    row["exit"],
                    period,
                    _fmt_multiple(ladder["final_wealth_multiple"]),
                    _fmt_multiple(permanent["final_wealth_multiple"]),
                    _fmt_multiple(baseline["final_wealth_multiple"]),
                    _fmt_multiple(metric["ladder_to_baseline_ratio"]),
                    _fmt_multiple(metric["ladder_to_permanent_ratio"]),
                    _fmt_pct(ladder["max_drawdown"]),
                    _fmt_pct(permanent["max_drawdown"]),
                    _fmt_pct(baseline["max_drawdown"]),
                ))
        for row in payload["references"]:
            for period in BASE_SWEEP_PERIODS:
                metric = row["periods"][period]
                baseline_metric = next(
                    strategy["periods"][period]["baseline_1x"]
                    for strategy in payload["rows"]
                    if strategy["leverage_multiple"] == row["leverage_multiple"]
                    and strategy["base_exposure"] == row["base_exposure"]
                )
                reference_rows.append((
                    label,
                    row["leverage_multiple"],
                    f"{row['base_exposure']:.1f}",
                    period,
                    _fmt_multiple(metric["final_wealth_multiple"]),
                    _fmt_multiple(baseline_metric["final_wealth_multiple"]),
                    _fmt_multiple(_safe_ratio(
                        metric["final_wealth_multiple"], baseline_metric["final_wealth_multiple"]
                    )),
                    _fmt_pct(metric["max_drawdown"]),
                    _fmt_pct(baseline_metric["max_drawdown"]),
                ))

    holdout_thresholds = [
        threshold
        for payload in payloads
        for threshold in payload["thresholds"]
        if threshold["period"] == "holdout" and threshold["smallest_base_exposure"] is not None
    ]
    ladder_adds = sum(threshold["beats_permanent_at_threshold"] is True for threshold in holdout_thresholds)
    threshold_values = [float(row["smallest_base_exposure"]) for row in holdout_thresholds]
    if not holdout_thresholds:
        conclusion = (
            "결론: hold-out에서 시험한 어떤 기본 노출도 사다리 계좌를 1x baseline 위로 올리지 못했다. "
            "따라서 이 범위에서는 사다리나 기본 레버리지 어느 쪽에도 최종부 우위가 확인되지 않았다."
        )
    else:
        threshold_range = f"{min(threshold_values):.1f}x~{max(threshold_values):.1f}x"
        source = (
            "사다리 자체의 추가 기여도 함께 보였다"
            if ladder_adds == len(holdout_thresholds)
            else "대부분의 우위는 사다리보다 기본 레버리지 수준에서 왔다"
        )
        conclusion = (
            f"결론: hold-out에서 1x를 넘긴 {len(holdout_thresholds)}개 지수×k×exit 조합의 최소 기본 노출은 "
            f"{threshold_range}였고, 그 문턱에서 단순 영구 기본 노출까지 이긴 경우는 {ladder_adds}개였다. "
            f"따라서 {source}; 사다리의 독립적 edge는 `ladder/permanent`가 1을 넘는 경우에만 인정해야 한다."
        )

    manifest_digests = sorted({payload["input_manifest_sha256"] for payload in payloads})
    cycle_values = [payload["independent_cycle_count"]["full"] for payload in payloads]
    return "\n\n".join([
        "# 기본 노출 × 낙폭 사다리 sweep 결과 (2026-09-05)",
        (
            "> `compound-ladder/base-exposure-sweep-v1` 개발용 retained-Parquet 시뮬레이션. "
            "현재 규칙(drawdown252 ≤ -20%, disp60 ≤ -10%, 2단계), exit a/d, 편도 0.10% 비용, "
            "가능한 경우 retained 실제 상품 gap 보정을 사용했다."
        ),
        "## 한줄 결론",
        conclusion,
        "## 1x 초과 최소 기본 노출",
        _table(
            ("basket/index", "k", "exit", "split", "최소 b", "사다리가 영구 b를 이김?", "사다리/1x", "사다리/영구 b"),
            threshold_rows,
        ),
        "## 상세 비교",
        _table(
            (
                "basket/index", "k", "b", "exit", "split", "(i) 사다리", "(ii) 영구 b", "(iii) 1x",
                "(i)/(iii)", "(i)/(ii)", "MDD (i)", "MDD (ii)", "MDD (iii)",
            ),
            detail_rows,
        ),
        "## 참조 행: always b× with NO ladder",
        _table(
            ("basket/index", "k", "b", "split", "영구 b", "1x", "영구 b/1x", "MDD 영구 b", "MDD 1x"),
            reference_rows,
        ),
        "## 독립 cycle 수",
        _table(("basket/index", "fit", "hold-out", "full"), cycle_rows),
        (
            f"양수 level 신호일을 90 calendar-day 초과 공백에서 분리한 full 독립 cycle 수는 지수별 {min(cycle_values)}~{max(cycle_values)}개다."
        ),
        "## 실제 상품 gap 보정",
        _table(("basket/index", "k", "상태", "상품", "연환산 gap", "추가 drag"), calibration_rows),
        "## 해석 한계",
        "\n".join([
            "- 상품 경로는 일일 재설정 synthetic이며, 실제 상품 gap은 명시적 mapping과 retained 공통 구간이 있을 때만 상수 drag로 보정했다. 보정 불가 조합은 그 사실을 JSON과 표에 남겼다.",
            f"- 독립 cycle이 지수별 {min(cycle_values)}~{max(cycle_values)}개뿐이어서 표본이 적고, 한두 crisis path가 결과를 지배할 수 있다.",
            "- 신호는 T 종가로 계산해 다음 retained session 종가에서 반영하지만, retained 원천의 역사적 빈티지·당시 공개시각이 PIT-safe였다고 검증한 실험은 아니다. 따라서 look-ahead가 없다는 주장을 하지 않는다.",
            "- 이 결과는 개발용 비교이며 실현 가능한 체결, 세금·용량·대차, 적합성, 추천 또는 실계좌 성과 주장이 아니다.",
        ]),
        "## 재현 정보",
        "\n".join([
            f"- runtime: {runtime_seconds:.3f}초",
            f"- input manifest SHA-256: `{', '.join(manifest_digests)}`",
            "- API calls: `0`; 입력은 retained Parquet만 사용",
            "- 기존 `grid_*.json`과 `summary.json`은 이 sweep 경로에서 쓰지 않음",
        ]),
        "",
    ])


def run_base_exposure_sweep(
    project_root: Path,
    baskets: tuple[str, ...],
    base_exposures: tuple[float, ...],
    *,
    quick: bool,
    drawdown_threshold: float | object = _UNDECIDED,
    disp60_threshold: float | object = _UNDECIDED,
    product_share_at_max: float | object = _UNDECIDED,
    levels: int | object = _UNDECIDED,
    base_exposure: float | object = _UNDECIDED,
) -> dict[str, Any]:
    started = time.perf_counter()
    root = project_root.resolve()
    decided_drawdown = require_drawdown_threshold(drawdown_threshold)
    decided_disp60 = require_disp60_threshold(disp60_threshold)
    decided_share = _require_product_share(product_share_at_max)
    decided_levels = require_levels(levels)
    decided_base_exposure = require_base_exposure(base_exposure)
    if not base_exposures:
        raise ValueError("base_exposures must contain caller-provided values")
    decided_base_exposures = tuple(require_base_exposure(value) for value in base_exposures)
    output = root / "artifacts/research/compound_ladder"
    universe = load_index_universe(root)
    real_products = load_real_products(root)
    retained_paths = [
        Path("data/normalized/kr_index_daily"),
        Path("data/normalized/kr_kospi200_index_daily"),
        Path("data/normalized/global_index_price_daily"),
        Path("data/normalized/global_etf_price_daily"),
        Path("data/normalized/kr_etf_price_daily"),
        Path("data/normalized/kr_etf_master"),
        *[path.relative_to(root) for path in sorted((root / "data/normalized").glob("fred_*"))],
    ]
    manifest_digest, manifest = retained_manifest_digest(root, retained_paths)
    available = set(universe["series_id"].astype(str))
    payloads: list[dict[str, Any]] = []
    artifact_paths: list[str] = []
    for basket in baskets:
        for underlying in BASKET_SERIES[basket]:
            if underlying not in available:
                print(f"SKIP {basket}/{underlying}: retained symbol absent")
                continue
            frame = universe.loc[universe["series_id"].eq(underlying)].copy().reset_index(drop=True)
            payload = _base_sweep_series(
                root,
                basket,
                underlying,
                frame,
                real_products,
                decided_base_exposures,
                drawdown_threshold=decided_drawdown,
                disp60_threshold=decided_disp60,
                product_share_at_max=decided_share,
                levels=decided_levels,
                signal_base_exposure=decided_base_exposure,
                quick=quick,
                manifest_digest=manifest_digest,
                manifest=manifest,
            )
            path = output / f"sweep_base_{_slug(basket)}_{_slug(underlying)}.json"
            _write_json(path, payload)
            artifact_paths.append(path.relative_to(root).as_posix())
            payloads.append(payload)
            print(f"DONE {basket}/{underlying}: {len(payload['rows'])} ladder rows")
    runtime_seconds = time.perf_counter() - started
    _write_text(
        root / "docs/research/RESULTS_20260905_base_exposure_sweep.md",
        _base_sweep_markdown(payloads, runtime_seconds),
    )
    return {
        "payloads": payloads,
        "artifact_paths": artifact_paths,
        "runtime_seconds": runtime_seconds,
        "quick": quick,
    }


def _parse_base_exposures(raw: str) -> tuple[float, ...]:
    parts = [part for part in re.split(r"[\s,]+", raw.strip()) if part]
    if not parts:
        raise argparse.ArgumentTypeError("--base-exposures requires at least one value")
    try:
        values = sorted({float(part) for part in parts})
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--base-exposures values must be numeric") from exc
    for value in values:
        if not math.isfinite(value) or not 0.0 <= value <= 3.0:
            raise argparse.ArgumentTypeError("base_exposure must be finite and in [0.0, 3.0]")
    return tuple(values)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Continuous-account compound drawdown ladder backtest")
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument(
        "--baskets",
        default="KR,US_TECH,SEMIS",
        help="Comma-separated subset of KR,US_TECH,SEMIS,FOREIGN",
    )
    parser.add_argument("--quick", action="store_true", help="Run the reduced deterministic smoke grid")
    parser.add_argument(
        "--drawdown-threshold",
        type=float,
        default=None,
        help="Required selected drawdown252 threshold; no code default.",
    )
    parser.add_argument(
        "--disp60-threshold",
        type=float,
        default=None,
        help="Required selected 60-session disparity threshold; no code default.",
    )
    parser.add_argument(
        "--product-share-at-max",
        type=float,
        default=None,
        help="Required leveraged-product portfolio weight at the highest ladder level.",
    )
    parser.add_argument(
        "--levels",
        type=int,
        help="Required caller-selected ladder step count (1..4); no code default.",
    )
    parser.add_argument(
        "--base-exposure",
        type=float,
        help="Required caller-selected base exposure in [0, 3]; no code default.",
    )
    parser.add_argument(
        "--base-exposures",
        nargs="+",
        type=_parse_base_exposures,
        default=None,
        help=(
            "One or more base exposures, each value or group optionally comma-separated. "
            "When supplied, run the fixed-rule "
            "base-exposure sweep and write only sweep_base_*.json plus its dedicated report."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    decided_levels = require_levels(args.levels)
    decided_base_exposure = require_base_exposure(args.base_exposure)
    baskets = tuple(part.strip().upper() for part in args.baskets.split(",") if part.strip())
    unknown = sorted(set(baskets).difference(BASKET_SERIES))
    if not baskets or unknown:
        raise SystemExit(f"unsupported baskets: {unknown or 'empty selection'}")
    if args.base_exposures is not None:
        base_exposures = tuple(sorted({
            value
            for group in args.base_exposures
            for value in group
        }))
        result = run_base_exposure_sweep(
            args.project_root,
            baskets,
            base_exposures,
            quick=args.quick,
            drawdown_threshold=args.drawdown_threshold,
            disp60_threshold=args.disp60_threshold,
            product_share_at_max=args.product_share_at_max,
            levels=decided_levels,
            base_exposure=decided_base_exposure,
        )
        print("basket | underlying | k | exit | holdout smallest b | beats permanent")
        for payload in result["payloads"]:
            for threshold in payload["thresholds"]:
                if threshold["period"] != "holdout":
                    continue
                base = threshold["smallest_base_exposure"]
                beats = threshold["beats_permanent_at_threshold"]
                print(
                    f"{payload['basket']} | {payload['underlying']} | {threshold['leverage_multiple']} | "
                    f"{threshold['exit']} | {base if base is not None else 'none'} | "
                    f"{beats if beats is not None else 'n/a'}"
                )
        print(f"runtime_seconds={result['runtime_seconds']:.3f}")
        print("BASE_EXPOSURE_SWEEP_COMPLETE")
        return 0
    summary = run(
        args.project_root,
        baskets,
        quick=args.quick,
        drawdown_threshold=args.drawdown_threshold,
        disp60_threshold=args.disp60_threshold,
        product_share_at_max=args.product_share_at_max,
        levels=decided_levels,
        base_exposure=decided_base_exposure,
    )
    print("basket | underlying | holdout strategy | holdout baseline | relative")
    for basket, items in summary["baskets"].items():
        for item in items:
            metric = item["headline"]["holdout"]
            print(
                f"{basket} | {item['underlying']} | {_fmt_multiple(metric['final_wealth_multiple'])} | "
                f"{_fmt_multiple(metric['baseline_final_wealth_multiple'])} | {_fmt_multiple(metric['relative_to_baseline'])}"
            )
    print(f"runtime_seconds={summary['runtime_seconds']:.3f}")
    print("COMPOUND_LADDER_COMPLETE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
