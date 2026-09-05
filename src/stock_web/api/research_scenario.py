"""Fail-closed scenario selection for Research best-cell summaries."""

from __future__ import annotations

import math
from typing import Mapping, Sequence


_SCENARIO_ERROR = "scenario must fix cost/tax/exit/base_exposure"
_REQUIRED_SCENARIO_KEYS = (
    "cost_enabled",
    "exit",
    "base_exposure",
    "product_variant",
)


def _metric_value(
    row: Mapping[str, object], metric_path: Sequence[str],
) -> float | None:
    value: object = row
    for key in metric_path:
        if not isinstance(value, Mapping) or key not in value:
            return None
        value = value[key]
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _typed_value(value: object) -> tuple[type[object], str]:
    """Keep bool/int aliases from passing the mixed-scenario guard."""

    return type(value), repr(value)


def select_best_in_scenario(
    rows: Sequence[Mapping[str, object]],
    *,
    scenario: Mapping[str, object],
    metric_path: Sequence[str] = ("fit", "relative_to_baseline"),
) -> Mapping[str, object]:
    """Return the metric maximum after explicitly fixing one complete scenario."""

    candidates = list(rows)
    required = list(_REQUIRED_SCENARIO_KEYS)
    if any("tax_rate" in row for row in candidates):
        required.append("tax_rate")
    if any(key not in scenario or scenario[key] is None for key in required):
        raise ValueError(_SCENARIO_ERROR)

    selected = [
        row
        for row in candidates
        if all(key in row and row[key] == value for key, value in scenario.items())
    ]
    for key in scenario:
        values = {_typed_value(row[key]) for row in selected}
        if len(values) > 1:
            raise ValueError(_SCENARIO_ERROR)

    ranked = [
        (value, row)
        for row in selected
        if (value := _metric_value(row, metric_path)) is not None
    ]
    if not ranked:
        raise ValueError("no rows match scenario with a finite metric")
    return max(ranked, key=lambda item: item[0])[1]
