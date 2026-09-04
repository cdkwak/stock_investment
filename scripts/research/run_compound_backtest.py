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
    ladder_levels,
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
    "drawdown_threshold": -0.20,
    "disp60_threshold": -0.10,
    "levels": 2,
    "leverage_multiple": 2,
    "base_exposure": 1.0,
    "exit": "a",
    "cost_enabled": True,
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


def _is_current(row: dict[str, Any]) -> bool:
    return all(row.get(key) == value for key, value in CURRENT.items())


def _strategy_key(row: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(row[key] for key in (
        "drawdown_threshold",
        "disp60_threshold",
        "levels",
        "leverage_multiple",
        "base_exposure",
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


def _plateau(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    strategy = [
        row for row in rows
        if row["row_kind"] == "strategy" and row["base_exposure"] == 1.0
    ]
    definitions = (
        (
            "threshold_x_levels",
            "drawdown_threshold",
            "levels",
            {"disp60_threshold": -0.10, "leverage_multiple": 2, "exit": "a", "cost_enabled": True},
        ),
        (
            "levels_x_multiple",
            "levels",
            "leverage_multiple",
            {"drawdown_threshold": -0.20, "disp60_threshold": -0.10, "exit": "a", "cost_enabled": True},
        ),
        (
            "threshold_x_multiple",
            "drawdown_threshold",
            "leverage_multiple",
            {"disp60_threshold": -0.10, "levels": 2, "exit": "a", "cost_enabled": True},
        ),
    )
    output: list[dict[str, Any]] = []
    for name, x_key, y_key, fixed in definitions:
        selected = [row for row in strategy if all(row[key] == value for key, value in fixed.items())]
        selected = [
            row for row in selected
            if row["fit"]["relative_to_baseline"] is not None
            and math.isfinite(float(row["fit"]["relative_to_baseline"]))
        ]
        if not selected:
            continue
        best = max(selected, key=lambda row: float(row["fit"]["relative_to_baseline"]))
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
            "fixed": fixed,
        })
    return output


def _grid_for_series(
    root: Path,
    basket: str,
    underlying: str,
    frame: pd.DataFrame,
    real_products: pd.DataFrame,
    grid: dict[str, tuple[Any, ...]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
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
                signal_spec = LadderSpec(float(dd), float(disp), int(levels_count))
                cache_key = (float(dd), float(disp), int(levels_count))
                levels = level_cache.setdefault(
                    cache_key, ladder_levels(signals, signal_spec)["executable_level"]
                )
                for multiple in grid["leverage_multiple"]:
                    returns = returns_by_variant[(int(multiple), 0.0)]
                    for base_exposure in grid["base_exposure"]:
                        spec = LadderSpec(
                            float(dd),
                            float(disp),
                            int(levels_count),
                            float(base_exposure),
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
    current = next((row for row in rows if _is_current(row)), None)
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
    current_spec = LadderSpec(-0.20, -0.10, 2, 1.0)
    current_levels = level_cache[(-0.20, -0.10, 2)]
    underlying_baseline_metrics = _baseline_metrics(
        frame["date"], underlying_returns, transaction_cost=TRANSACTION_COST
    )
    underlying_strategy_metrics = simulate_grid_metrics(
        frame["date"],
        underlying_returns,
        current_levels,
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
        "plateau": _plateau(rows),
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
            and row["drawdown_threshold"] == -0.20
            and row["disp60_threshold"] == -0.10
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


def _surface_summary(basket_items: list[dict[str, Any]], all_rows: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    cells: list[dict[str, Any]] = []
    for levels_count in (1, 2, 3, 4):
        for multiple in (1, 2, 3):
            values: list[float] = []
            for item in basket_items:
                for row in all_rows[item["underlying"]]:
                    if (
                        row["row_kind"] == "strategy"
                        and row["drawdown_threshold"] == -0.20
                        and row["disp60_threshold"] == -0.10
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


def run(project_root: Path, baskets: tuple[str, ...], *, quick: bool) -> dict[str, Any]:
    started = time.perf_counter()
    root = project_root.resolve()
    output = root / "artifacts/research/compound_ladder"
    grid = QUICK_GRID if quick else FULL_GRID
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
            rows, metadata = _grid_for_series(root, basket, underlying, frame, real_products, grid)
            rows_by_underlying[underlying] = rows
            path = output / f"grid_{_slug(basket)}_{_slug(underlying)}.json"
            _write_json(path, rows)
            artifact_paths.append(path.relative_to(root).as_posix())
            exit_candidates = [
                row for row in rows
                if row["row_kind"] == "strategy"
                and row["drawdown_threshold"] == -0.20
                and row["disp60_threshold"] == -0.10
                and row["levels"] == 2
                and row["leverage_multiple"] == 2
                and row["base_exposure"] == 1.0
                and row["cost_enabled"] is True
            ]
            ranking = []
            for row in exit_candidates:
                actual = row.get("actual_product_basis")
                metric = actual["holdout"] if actual is not None else row["holdout"]
                ranking.append({
                    "exit": row["exit"],
                    "base_exposure": row["base_exposure"],
                    **metric,
                })
            ranking.sort(key=lambda item: -math.inf if item["final_wealth_multiple"] is None else float(item["final_wealth_multiple"]), reverse=True)
            real_product_rows = [
                {
                    "leverage_multiple": row["leverage_multiple"],
                    "actual_product_basis": row["actual_product_basis"],
                }
                for row in rows
                if row["row_kind"] == "strategy"
                and row["drawdown_threshold"] == -0.20
                and row["disp60_threshold"] == -0.10
                and row["levels"] == 2
                and row["base_exposure"] == 1.0
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
                        "drawdown_threshold", "disp60_threshold", "levels", "leverage_multiple", "base_exposure", "exit", "cost_enabled", "fit", "holdout", "full"
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
        basket: _surface_summary(items, rows_by_underlying)
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
        "current_rule": CURRENT,
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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Continuous-account compound drawdown ladder backtest")
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument(
        "--baskets",
        default="KR,US_TECH,SEMIS",
        help="Comma-separated subset of KR,US_TECH,SEMIS,FOREIGN",
    )
    parser.add_argument("--quick", action="store_true", help="Run the reduced deterministic smoke grid")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    baskets = tuple(part.strip().upper() for part in args.baskets.split(",") if part.strip())
    unknown = sorted(set(baskets).difference(BASKET_SERIES))
    if not baskets or unknown:
        raise SystemExit(f"unsupported baskets: {unknown or 'empty selection'}")
    summary = run(args.project_root, baskets, quick=args.quick)
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
