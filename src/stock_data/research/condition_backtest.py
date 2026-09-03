"""Offline historical study for watchlist oversold conditions.

The study is descriptive rather than an executable trading simulation.  Signal
columns use observations through close T only; forward outcomes are built in a
separate frame and joined solely for evaluation.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
from pathlib import Path
from typing import Iterable, Sequence
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pyarrow.dataset as pads

from .drawdown_score import (
    ScoreThresholds,
    default_threshold_grid,
    evaluate_threshold,
    score_event_mask,
    search_score_grid,
)


HORIZONS: tuple[int, ...] = (5, 20, 60, 120)
FIT_END = "2015-12-31"
HOLDOUT_START = "2016-01-01"
MIN_CELL_EVENTS = 15
DEFAULT_MIN_SCORE_EVENTS = 30
CONDITION_COLUMNS: dict[str, str] = {
    "RSI14≤30": "event_rsi14_le_30",
    "60일선 대비≤-10%": "event_disp60_le_m10",
    "52주 고점 대비≤-30%": "event_drawdown252_le_m30",
    "세 조건 동시": "event_all_three",
}
REPORT_BASKETS: tuple[str, ...] = ("KR", "US_TECH", "SEMIS", "POOLED")
PRIMARY_GLOBAL_SYMBOLS = frozenset(
    {"SP500", "NASDAQ100", "NASDAQ_COMPOSITE", "SOX", "DOW_JONES"}
)
LEVERAGED_GLOBAL_SYMBOLS = frozenset({"QLD", "SOXL", "TQQQ"})


@dataclass(frozen=True)
class BacktestResult:
    summary_path: Path
    output_dir: Path
    conclusion_lines: tuple[str, ...]


def wilder_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Compute Wilder RSI with a simple-average seed and recursive smoothing."""

    if period < 1:
        raise ValueError("RSI period must be positive")
    values = pd.to_numeric(close, errors="coerce").to_numpy(dtype="float64")
    result = np.full(len(values), np.nan, dtype="float64")
    if len(values) <= period:
        return pd.Series(result, index=close.index, name="rsi14")
    if not np.isfinite(values).all() or np.any(values <= 0):
        raise ValueError("RSI close values must be finite and positive")
    delta = np.diff(values)
    gains = np.maximum(delta, 0.0)
    losses = np.maximum(-delta, 0.0)
    average_gain = float(gains[:period].mean())
    average_loss = float(losses[:period].mean())

    def rsi_value(gain: float, loss: float) -> float:
        if loss == 0.0:
            return 50.0 if gain == 0.0 else 100.0
        return 100.0 - 100.0 / (1.0 + gain / loss)

    result[period] = rsi_value(average_gain, average_loss)
    for position in range(period + 1, len(values)):
        average_gain = ((period - 1) * average_gain + gains[position - 1]) / period
        average_loss = ((period - 1) * average_loss + losses[position - 1]) / period
        result[position] = rsi_value(average_gain, average_loss)
    return pd.Series(result, index=close.index, name="rsi14")


def _validate_prices(prices: pd.DataFrame) -> pd.DataFrame:
    required = {"date", "series_id", "basket", "close"}
    missing = required.difference(prices.columns)
    if missing:
        raise ValueError(f"price input is missing columns: {sorted(missing)}")
    if prices.empty:
        raise ValueError("price input must not be empty")
    frame = prices.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="raise").dt.normalize()
    frame["series_id"] = frame["series_id"].astype(str)
    frame["basket"] = frame["basket"].astype(str)
    frame["close"] = pd.to_numeric(frame["close"], errors="raise").astype("float64")
    if not np.isfinite(frame["close"]).all() or frame["close"].le(0).any():
        raise ValueError("close values must be finite and positive")
    if frame[["series_id", "date"]].duplicated().any():
        raise ValueError("price input contains duplicate series/date keys")
    if "volume" not in frame:
        frame["volume"] = np.nan
    else:
        frame["volume"] = pd.to_numeric(frame["volume"], errors="coerce")
    return frame.sort_values(["series_id", "date"], kind="mergesort").reset_index(drop=True)


def compute_signals(prices: pd.DataFrame) -> pd.DataFrame:
    """Create point-in-time signals; every row uses close/volume through T only."""

    source = _validate_prices(prices)
    pieces: list[pd.DataFrame] = []
    for _, group in source.groupby("series_id", sort=True):
        group = group.copy().reset_index(drop=True)
        close = group["close"]
        group["rsi14"] = wilder_rsi(close)
        group["ma60"] = close.rolling(60, min_periods=60).mean()
        group["disp60"] = close / group["ma60"] - 1.0
        group["high252"] = close.rolling(252, min_periods=252).max()
        group["drawdown252"] = close / group["high252"] - 1.0
        volume_mean = group["volume"].rolling(20, min_periods=20).mean()
        group["volume_ratio20"] = group["volume"] / volume_mean
        group.loc[volume_mean.eq(0), "volume_ratio20"] = np.nan
        pieces.append(group)
    signals = pd.concat(pieces, ignore_index=True)
    return add_condition_events(signals)


def add_condition_events(signals: pd.DataFrame) -> pd.DataFrame:
    """Add zone-entry events, excluding first-valid rows with no prior state."""

    required = {"series_id", "rsi14", "disp60", "drawdown252"}
    missing = required.difference(signals.columns)
    if missing:
        raise ValueError(f"signal input is missing columns: {sorted(missing)}")
    frame = signals.copy()
    grouped = frame.groupby("series_id", sort=False)
    prior_rsi = grouped["rsi14"].shift(1)
    prior_disp = grouped["disp60"].shift(1)
    prior_drawdown = grouped["drawdown252"].shift(1)
    frame["event_rsi14_le_30"] = frame["rsi14"].le(30.0) & prior_rsi.gt(30.0)
    frame["event_disp60_le_m10"] = frame["disp60"].le(-0.10) & prior_disp.gt(-0.10)
    frame["event_drawdown252_le_m30"] = (
        frame["drawdown252"].le(-0.30) & prior_drawdown.gt(-0.30)
    )
    valid = frame[["rsi14", "disp60", "drawdown252"]].notna().all(axis=1)
    current_all = (
        frame["rsi14"].le(30.0)
        & frame["disp60"].le(-0.10)
        & frame["drawdown252"].le(-0.30)
    )
    prior_valid = valid.groupby(frame["series_id"], sort=False).shift(1, fill_value=False)
    prior_all = current_all.groupby(frame["series_id"], sort=False).shift(1, fill_value=False)
    frame["event_all_three"] = valid & prior_valid & current_all & ~prior_all
    for column in CONDITION_COLUMNS.values():
        frame[column] = frame[column].fillna(False).astype(bool)
    if "date" in frame:
        frame["observation_date"] = pd.to_datetime(
            frame["date"], errors="raise"
        ).dt.strftime("%Y-%m-%d")
    return frame


def build_forward_outcomes(
    prices: pd.DataFrame, horizons: Sequence[int] = HORIZONS,
) -> pd.DataFrame:
    """Build isolated forward returns and true peak-to-trough path drawdowns."""

    source = _validate_prices(prices)
    clean_horizons = tuple(sorted(set(int(value) for value in horizons)))
    if not clean_horizons or clean_horizons[0] < 1:
        raise ValueError("forward horizons must be positive")
    rows: list[pd.DataFrame] = []
    for _, group in source.groupby("series_id", sort=True):
        group = group.reset_index(drop=True)
        close = group["close"].to_numpy(dtype="float64")
        dates = group["date"].to_numpy()
        for horizon in clean_horizons:
            count = len(group) - horizon
            if count <= 0:
                continue
            returns = close[horizon:] / close[:count] - 1.0
            path_drawdowns = np.empty(count, dtype="float64")
            for position in range(count):
                path = close[position : position + horizon + 1]
                running_high = np.maximum.accumulate(path)
                path_drawdowns[position] = np.min(path / running_high - 1.0)
            rows.append(
                pd.DataFrame(
                    {
                        "observation_date": pd.to_datetime(dates[:count]).strftime("%Y-%m-%d"),
                        "outcome_end_date": pd.to_datetime(
                            dates[horizon : horizon + count]
                        ).strftime("%Y-%m-%d"),
                        "series_id": group["series_id"].iloc[0],
                        "basket": group["basket"].iloc[0],
                        "horizon": horizon,
                        "forward_return": returns,
                        "forward_max_drawdown": path_drawdowns,
                    }
                )
            )
    if not rows:
        return pd.DataFrame(
            columns=[
                "observation_date",
                "outcome_end_date",
                "series_id",
                "basket",
                "horizon",
                "forward_return",
                "forward_max_drawdown",
            ]
        )
    return pd.concat(rows, ignore_index=True).sort_values(
        ["series_id", "observation_date", "horizon"], kind="mergesort"
    ).reset_index(drop=True)


def build_condition_event_table(
    signals: pd.DataFrame, outcomes: pd.DataFrame,
) -> pd.DataFrame:
    """Join outcome labels only after extracting signal events."""

    signal_columns = [
        "observation_date",
        "series_id",
        "basket",
        "rsi14",
        "disp60",
        "drawdown252",
        "volume_ratio20",
    ]
    rows: list[pd.DataFrame] = []
    for rule, column in CONDITION_COLUMNS.items():
        selected = signals.loc[signals[column], signal_columns].copy()
        if selected.empty:
            continue
        selected["rule"] = rule
        rows.append(
            selected.merge(
                outcomes,
                on=["observation_date", "series_id", "basket"],
                how="inner",
                validate="one_to_many",
            )
        )
    if not rows:
        return pd.DataFrame(
            columns=[*signal_columns, "rule", *outcomes.columns.difference(signal_columns)]
        )
    return pd.concat(rows, ignore_index=True).sort_values(
        ["rule", "basket", "series_id", "observation_date", "horizon"],
        kind="mergesort",
    ).reset_index(drop=True)


def _with_pooled(frame: pd.DataFrame) -> pd.DataFrame:
    pooled = frame.copy()
    pooled["basket"] = "POOLED"
    named = frame.loc[frame["basket"].isin(REPORT_BASKETS[:-1])]
    return pd.concat([named, pooled], ignore_index=True)


def summarize_condition_events(
    events: pd.DataFrame, outcomes: pd.DataFrame,
) -> pd.DataFrame:
    """Aggregate condition outcomes and same-series unconditional baselines."""

    series_baseline = (
        outcomes.groupby(["series_id", "horizon"], sort=True)["forward_return"]
        .agg(series_baseline_mean="mean", series_baseline_median="median")
        .reset_index()
    )
    event_views = _with_pooled(events).merge(
        series_baseline,
        on=["series_id", "horizon"],
        how="left",
        validate="many_to_one",
    )
    baseline_views = _with_pooled(outcomes)
    baseline = (
        baseline_views.groupby(["basket", "horizon"], sort=True)["forward_return"]
        .agg(
            baseline_n="count",
            basket_baseline_mean="mean",
            basket_baseline_median="median",
        )
        .reset_index()
    )
    observed = (
        event_views.groupby(["rule", "horizon", "basket"], sort=True)
        .agg(
            n=("forward_return", "count"),
            mean_return=("forward_return", "mean"),
            median_return=("forward_return", "median"),
            hit_rate=("forward_return", lambda value: value.gt(0).mean()),
            mean_max_drawdown=("forward_max_drawdown", "mean"),
            baseline_mean=("series_baseline_mean", "mean"),
            baseline_median=("series_baseline_median", "mean"),
        )
        .reset_index()
    )
    complete_index = pd.MultiIndex.from_product(
        [tuple(CONDITION_COLUMNS), HORIZONS, REPORT_BASKETS],
        names=["rule", "horizon", "basket"],
    )
    summary = observed.set_index(["rule", "horizon", "basket"]).reindex(
        complete_index
    ).reset_index()
    summary["n"] = summary["n"].fillna(0).astype("int64")
    summary = summary.merge(
        baseline, on=["basket", "horizon"], how="left", validate="many_to_one"
    )
    summary["baseline_mean"] = summary["baseline_mean"].fillna(
        summary["basket_baseline_mean"]
    )
    summary["baseline_median"] = summary["baseline_median"].fillna(
        summary["basket_baseline_median"]
    )
    summary = summary.drop(columns=["basket_baseline_mean", "basket_baseline_median"])
    summary["difference_vs_baseline"] = summary["mean_return"] - summary["baseline_mean"]
    summary["low_sample"] = summary["n"].lt(MIN_CELL_EVENTS)
    basket_order = {basket: position for position, basket in enumerate(REPORT_BASKETS)}
    rule_order = {rule: position for position, rule in enumerate(CONDITION_COLUMNS)}
    summary["_basket_order"] = summary["basket"].map(basket_order)
    summary["_rule_order"] = summary["rule"].map(rule_order)
    return summary.sort_values(
        ["_rule_order", "horizon", "_basket_order"], kind="mergesort"
    ).drop(columns=["_basket_order", "_rule_order"]).reset_index(drop=True)


def _score_input(signals: pd.DataFrame, outcomes: pd.DataFrame) -> pd.DataFrame:
    labels = outcomes.loc[outcomes["horizon"].eq(60), [
        "observation_date",
        "outcome_end_date",
        "series_id",
        "forward_return",
    ]].rename(
        columns={
            "outcome_end_date": "outcome_end_date_60",
            "forward_return": "forward_return_60",
        }
    )
    return signals.merge(
        labels,
        on=["observation_date", "series_id"],
        how="left",
        validate="one_to_one",
    )


def _scope_frame(score_input: pd.DataFrame, basket: str) -> pd.DataFrame:
    if basket == "POOLED":
        return score_input.copy()
    return score_input.loc[score_input["basket"].eq(basket)].copy()


def run_score_grid_analysis(
    score_input: pd.DataFrame,
    *,
    min_events: int = DEFAULT_MIN_SCORE_EVENTS,
    candidates: Sequence[ScoreThresholds] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, ScoreThresholds | None]]:
    """Fit pooled and per-basket score rules, then evaluate untouched periods."""

    candidates = tuple(candidates) if candidates is not None else default_threshold_grid()
    grids: list[pd.DataFrame] = []
    winners: dict[str, ScoreThresholds | None] = {}
    for basket in REPORT_BASKETS:
        scoped = _scope_frame(score_input, basket)
        if scoped.empty:
            winners[basket] = None
            continue
        grid, winner = search_score_grid(
            scoped,
            fit_end=FIT_END,
            holdout_start=HOLDOUT_START,
            min_events=min_events,
            candidates=candidates,
        )
        grid.insert(0, "selection_scope", basket)
        grids.append(grid)
        winners[basket] = winner

    winner_rows: list[dict[str, object]] = []
    global_winner = winners.get("POOLED")
    for basket in REPORT_BASKETS:
        scoped = _scope_frame(score_input, basket)
        if global_winner is None or scoped.empty:
            winner_rows.append(
                {
                    "strategy": "ONE_CUTOFF_ALL_BASKETS",
                    "selection_scope": "POOLED",
                    "evaluation_basket": basket,
                    "status": "INSUFFICIENT_FIT_EVENTS",
                }
            )
        else:
            stats = evaluate_threshold(
                scoped,
                global_winner,
                fit_end=FIT_END,
                holdout_start=HOLDOUT_START,
            )
            winner_rows.append(
                {
                    "strategy": "ONE_CUTOFF_ALL_BASKETS",
                    "selection_scope": "POOLED",
                    "evaluation_basket": basket,
                    "status": "SELECTED_ON_FIT_ONLY",
                    **stats,
                }
            )
        local_winner = winners.get(basket)
        if local_winner is None or scoped.empty:
            winner_rows.append(
                {
                    "strategy": "PER_BASKET_CUTOFF",
                    "selection_scope": basket,
                    "evaluation_basket": basket,
                    "status": "INSUFFICIENT_FIT_EVENTS",
                }
            )
        else:
            stats = evaluate_threshold(
                scoped,
                local_winner,
                fit_end=FIT_END,
                holdout_start=HOLDOUT_START,
            )
            winner_rows.append(
                {
                    "strategy": "PER_BASKET_CUTOFF",
                    "selection_scope": basket,
                    "evaluation_basket": basket,
                    "status": "SELECTED_ON_FIT_ONLY",
                    **stats,
                }
            )
    grid_frame = pd.concat(grids, ignore_index=True) if grids else pd.DataFrame()
    return grid_frame, pd.DataFrame(winner_rows), winners


def expanding_five_year_score_study(
    score_input: pd.DataFrame,
    *,
    min_events: int = DEFAULT_MIN_SCORE_EVENTS,
    candidates: Sequence[ScoreThresholds] | None = None,
) -> pd.DataFrame:
    """Refit on expanding history at fixed five-calendar-year boundaries."""

    candidates = tuple(candidates) if candidates is not None else default_threshold_grid()
    rows: list[dict[str, object]] = []
    for basket in REPORT_BASKETS:
        scoped = _scope_frame(score_input, basket)
        if scoped.empty:
            continue
        dates = pd.to_datetime(scoped["observation_date"], errors="raise")
        first_year = int(dates.min().year)
        last_year = int(dates.max().year)
        first_test_year = ((first_year + 9) // 5 + 1) * 5
        prior_threshold_id: str | None = None
        for test_start_year in range(first_test_year, last_year + 1, 5):
            test_start = pd.Timestamp(test_start_year, 1, 1)
            test_end = pd.Timestamp(test_start_year + 5, 1, 1) - pd.Timedelta(days=1)
            train = scoped.loc[dates.lt(test_start)].copy()
            if train.empty:
                continue
            grid, winner = search_score_grid(
                train,
                fit_end=(test_start - pd.Timedelta(days=1)).strftime("%Y-%m-%d"),
                holdout_start=test_start.strftime("%Y-%m-%d"),
                min_events=min_events,
                candidates=candidates,
            )
            if winner is None:
                rows.append(
                    {
                        "basket": basket,
                        "test_start": test_start.strftime("%Y-%m-%d"),
                        "test_end": test_end.strftime("%Y-%m-%d"),
                        "status": "INSUFFICIENT_TRAIN_EVENTS",
                        "threshold_id": pd.NA,
                        "threshold_stable": pd.NA,
                        "train_n": int(grid["fit_n"].max()) if not grid.empty else 0,
                        "test_n": 0,
                        "test_mean": np.nan,
                        "test_median": np.nan,
                        "test_hit_rate": np.nan,
                    }
                )
                continue
            event = score_event_mask(scoped, winner)
            outcome_date = pd.to_datetime(scoped["outcome_end_date_60"], errors="coerce")
            test_mask = (
                event
                & dates.ge(test_start)
                & dates.le(test_end)
                & outcome_date.le(test_end)
            )
            test_values = scoped.loc[test_mask, "forward_return_60"].dropna()
            winner_fit = grid.loc[grid["threshold_id"].eq(winner.threshold_id)].iloc[0]
            rows.append(
                {
                    "basket": basket,
                    "test_start": test_start.strftime("%Y-%m-%d"),
                    "test_end": test_end.strftime("%Y-%m-%d"),
                    "status": "SELECTED_ON_PRIOR_DATA",
                    "threshold_id": winner.threshold_id,
                    "threshold_stable": (
                        pd.NA if prior_threshold_id is None else winner.threshold_id == prior_threshold_id
                    ),
                    "train_n": int(winner_fit["fit_n"]),
                    "test_n": int(test_values.size),
                    "test_mean": float(test_values.mean()) if not test_values.empty else np.nan,
                    "test_median": float(test_values.median()) if not test_values.empty else np.nan,
                    "test_hit_rate": float(test_values.gt(0).mean()) if not test_values.empty else np.nan,
                }
            )
            prior_threshold_id = winner.threshold_id
    return pd.DataFrame(rows)


def _dataset_frame(
    path: Path,
    *,
    partitioning: str | None,
    columns: Iterable[str],
) -> pd.DataFrame:
    dataset = pads.dataset(path, format="parquet", partitioning=partitioning)
    selected = [column for column in columns if column in dataset.schema.names]
    return dataset.to_table(columns=selected).to_pandas()


def load_primary_indices(project_root: Path) -> pd.DataFrame:
    """Load the retained primary index universe without provider access."""

    columns = ("date", "symbol", "market", "name", "index_name", "close", "volume")
    kr_path = project_root / "data/normalized/kr_index_daily"
    kr = _dataset_frame(kr_path, partitioning="hive", columns=columns)
    text_columns = [column for column in ("symbol", "name", "index_name") if column in kr]
    identity = kr[text_columns].fillna("").astype(str).agg(" ".join, axis=1)
    keep = identity.str.contains(r"KOSPI200|KOSPI 200|200IT|정보기술", case=False, regex=True)
    keep |= kr["symbol"].astype(str).eq("KOSPI")
    kr = kr.loc[keep].copy()

    if not kr["symbol"].astype(str).eq("KOSPI200").any():
        fallback = project_root / "data/normalized/kr_kospi200_index_daily"
        if fallback.exists():
            extra = _dataset_frame(fallback, partitioning="hive", columns=columns)
            kr = pd.concat([kr, extra], ignore_index=True)
    kr["series_id"] = kr["symbol"].astype(str)
    kr["basket"] = "KR"
    kr["dataset_source"] = np.where(
        kr["series_id"].eq("KOSPI200"), "kr_kospi200_index_daily", "kr_index_daily"
    )

    global_path = project_root / "data/normalized/global_index_price_daily"
    global_indices = _dataset_frame(global_path, partitioning="hive", columns=columns)
    global_indices = global_indices.loc[
        global_indices["symbol"].isin(PRIMARY_GLOBAL_SYMBOLS)
    ].copy()
    global_indices["series_id"] = global_indices["symbol"].astype(str)
    global_indices["basket"] = "POOLED_ONLY"
    global_indices.loc[global_indices["series_id"].eq("NASDAQ100"), "basket"] = "US_TECH"
    global_indices.loc[global_indices["series_id"].eq("SOX"), "basket"] = "SEMIS"
    global_indices["dataset_source"] = "global_index_price_daily"

    combined = pd.concat([kr, global_indices], ignore_index=True, sort=False)
    return combined[[
        "date", "series_id", "basket", "close", "volume", "dataset_source"
    ]]


def load_leveraged_etfs(project_root: Path) -> pd.DataFrame:
    """Load only ETFs whose retained contract explicitly declares leverage."""

    columns = ("date", "symbol", "close", "volume")
    global_etf = _dataset_frame(
        project_root / "data/normalized/global_etf_price_daily",
        partitioning="hive",
        columns=columns,
    )
    global_etf = global_etf.loc[
        global_etf["symbol"].isin(LEVERAGED_GLOBAL_SYMBOLS)
    ].copy()
    global_etf["series_id"] = global_etf["symbol"].astype(str)
    global_etf["basket"] = global_etf["series_id"].map(
        {"QLD": "LEVERAGED_US_TECH", "TQQQ": "LEVERAGED_US_TECH", "SOXL": "LEVERAGED_SEMIS"}
    )
    global_etf["dataset_source"] = "global_etf_price_daily"

    kr_price = _dataset_frame(
        project_root / "data/normalized/kr_etf_price_daily",
        partitioning=None,
        columns=columns,
    )
    master = _dataset_frame(
        project_root / "data/normalized/kr_etf_master",
        partitioning="hive",
        columns=("symbol", "name", "leverage_multiple"),
    )
    leveraged_symbols = master.loc[
        pd.to_numeric(master["leverage_multiple"], errors="coerce").gt(1), "symbol"
    ].astype(str)
    kr_price["symbol"] = kr_price["symbol"].astype(str)
    kr_price = kr_price.loc[kr_price["symbol"].isin(leveraged_symbols)].copy()
    kr_price["series_id"] = kr_price["symbol"]
    kr_price["basket"] = "LEVERAGED_KR"
    kr_price["dataset_source"] = "kr_etf_price_daily"
    return pd.concat([global_etf, kr_price], ignore_index=True, sort=False)[[
        "date", "series_id", "basket", "close", "volume", "dataset_source"
    ]]


def _manifest_for_paths(project_root: Path, roots: Sequence[Path]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for root in sorted(set(roots), key=lambda value: value.as_posix()):
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.parquet"), key=lambda value: value.as_posix()):
            digest = hashlib.sha256()
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            rows.append(
                {
                    "path": path.relative_to(project_root).as_posix(),
                    "bytes": path.stat().st_size,
                    "sha256": digest.hexdigest(),
                }
            )
    return pd.DataFrame(rows, columns=["path", "bytes", "sha256"])


def _manifest_digest(manifest: pd.DataFrame) -> str:
    digest = hashlib.sha256()
    for row in manifest.sort_values("path", kind="mergesort").itertuples(index=False):
        digest.update(f"{row.path}\t{row.bytes}\t{row.sha256}\n".encode("utf-8"))
    return digest.hexdigest()


def _coverage(prices: pd.DataFrame, kind: str) -> pd.DataFrame:
    return (
        prices.groupby(["series_id", "basket", "dataset_source"], sort=True)["date"]
        .agg(start="min", end="max", observations="count")
        .reset_index()
        .assign(kind=kind)
        [["kind", "series_id", "basket", "dataset_source", "start", "end", "observations"]]
    )


def _fmt_pct(value: object) -> str:
    if pd.isna(value):
        return "—"
    return f"{float(value):.2%}"


def _markdown_table(frame: pd.DataFrame, columns: Sequence[str]) -> str:
    selected = frame.loc[:, columns].copy()
    headers = [str(column) for column in selected.columns]
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for row in selected.itertuples(index=False, name=None):
        lines.append("| " + " | ".join("—" if pd.isna(value) else str(value) for value in row) + " |")
    return "\n".join(lines)


def _condition_markdown(summary: pd.DataFrame) -> pd.DataFrame:
    output = summary[[
        "rule", "horizon", "basket", "n", "mean_return", "median_return",
        "hit_rate", "baseline_mean", "difference_vs_baseline", "low_sample",
    ]].copy()
    for column in (
        "mean_return", "median_return", "hit_rate", "baseline_mean", "difference_vs_baseline"
    ):
        output[column] = output[column].map(_fmt_pct)
    output["n"] = output.apply(
        lambda row: f"{int(row['n'])}{' ⚠' if row['low_sample'] else ''}", axis=1
    )
    return output.rename(
        columns={
            "rule": "조건",
            "horizon": "기간",
            "basket": "바스켓",
            "mean_return": "평균수익",
            "median_return": "중앙값",
            "hit_rate": "상승확률",
            "baseline_mean": "무조건평균",
            "difference_vs_baseline": "차이",
        }
    ).drop(columns="low_sample")


def _winner_markdown(winners: pd.DataFrame) -> pd.DataFrame:
    columns = [
        "strategy", "evaluation_basket", "status", "threshold_id",
        "fit_n", "fit_mean", "holdout_n", "holdout_mean", "holdout_hit_rate",
    ]
    output = winners.reindex(columns=columns).copy()
    for column in ("fit_mean", "holdout_mean", "holdout_hit_rate"):
        output[column] = output[column].map(_fmt_pct)
    output["fit_n"] = output["fit_n"].map(lambda value: "—" if pd.isna(value) else int(value))
    output["holdout_n"] = output["holdout_n"].map(
        lambda value: "—" if pd.isna(value) else f"{int(value)}{' ⚠' if int(value) < MIN_CELL_EVENTS else ''}"
    )
    return output.rename(
        columns={
            "strategy": "방식",
            "evaluation_basket": "평가 바스켓",
            "status": "상태",
            "threshold_id": "선택 임계값",
            "fit_n": "적합 n",
            "fit_mean": "적합 평균",
            "holdout_n": "홀드아웃 n",
            "holdout_mean": "홀드아웃 평균",
            "holdout_hit_rate": "홀드아웃 상승확률",
        }
    )


def _conclusions(
    condition_summary: pd.DataFrame,
    winners: pd.DataFrame,
    coverage: pd.DataFrame,
) -> tuple[str, ...]:
    pooled_60 = condition_summary.loc[
        condition_summary["basket"].eq("POOLED") & condition_summary["horizon"].eq(60)
    ]
    if pooled_60.empty:
        best_rule = "표본 없음"
        best_diff = np.nan
        best_n = 0
    else:
        best = pooled_60.sort_values(
            ["difference_vs_baseline", "n"], ascending=[False, False], kind="mergesort"
        ).iloc[0]
        best_rule = str(best["rule"])
        best_diff = best["difference_vs_baseline"]
        best_n = int(best["n"])
    global_row = winners.loc[
        winners["strategy"].eq("ONE_CUTOFF_ALL_BASKETS")
        & winners["evaluation_basket"].eq("POOLED")
    ]
    if global_row.empty or global_row.iloc[0].get("status") != "SELECTED_ON_FIT_ONLY":
        score_line = "통합 점수는 적합 구간 최소 사건 수를 충족한 후보가 없어 판단을 보류한다."
    else:
        row = global_row.iloc[0]
        score_line = (
            f"통합 점수 승자는 적합 n={int(row['fit_n'])}, 홀드아웃 n={int(row['holdout_n'])}, "
            f"홀드아웃 평균 {_fmt_pct(row['holdout_mean'])}였으나 표본이 15건 미만이면 판단 근거로 부족하다."
        )
    sox = coverage.loc[coverage["series_id"].eq("SOX")]
    sox_line = (
        "SOX는 retained 적합 이전 이력이 없어 semis 전용 임계값을 선택할 수 없다."
        if sox.empty or pd.Timestamp(sox.iloc[0]["start"]) >= pd.Timestamp(HOLDOUT_START)
        else "SOX는 적합/홀드아웃 양쪽에 관측치가 있다."
    )
    return (
        f"60세션 기준 통합 바스켓에서 무조건 기준 대비 차이가 가장 큰 조건은 '{best_rule}' "
        f"(n={best_n}, 차이 {_fmt_pct(best_diff)})였다.",
        score_line,
        sox_line,
        (
            "KOSPI200 IT 지수는 retained index 자료에 없어 결과에 포함하지 않았다."
            if not coverage["series_id"].astype(str).str.contains(
                r"KOSPI.?200.?IT|200IT|정보기술", case=False, regex=True
            ).any()
            else "Retained KOSPI200 IT 계열 지수를 KR 바스켓에 포함했다."
        ),
        "레버리지 ETF는 별도 기술통계만 계산했으며 임계값 탐색에는 한 행도 사용하지 않았다.",
        "2015년 말까지의 적합 라벨은 60세션 결과 종료일도 2015년 이내인 경우만 허용했다.",
        "신호는 종가 T까지의 정보만 쓰며 결과 라벨은 평가 단계에서만 결합했다.",
        "정규화 자료는 현재 retained snapshot이므로 원천 당시 빈티지·개정 이력 한계가 남는다.",
        "108개 점수 후보를 비교한 선택 편향과 다중검정 보정 부재도 과적합 위험으로 남는다.",
        "사건 수 15 미만 셀은 ⚠로 표시했으며 투자성과나 매매 추천으로 해석하면 안 된다.",
    )


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False, encoding="utf-8", lineterminator="\n")
    temporary.replace(path)


def _write_text(text: str, path: Path) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(text, encoding="utf-8", newline="\n")
    temporary.replace(path)


def run_condition_backtest(
    project_root: Path,
    *,
    output_date: str | None = None,
    min_score_events: int = DEFAULT_MIN_SCORE_EVENTS,
) -> BacktestResult:
    """Run the retained-data study and atomically publish CSV/Markdown outputs."""

    project_root = project_root.resolve()
    roots = [
        project_root / "data/normalized/kr_index_daily",
        project_root / "data/normalized/kr_kospi200_index_daily",
        project_root / "data/normalized/global_index_price_daily",
        project_root / "data/normalized/kr_etf_price_daily",
        project_root / "data/normalized/kr_etf_master",
        project_root / "data/normalized/global_etf_price_daily",
    ]
    manifest_before = _manifest_for_paths(project_root, roots)
    primary = load_primary_indices(project_root)
    leveraged = load_leveraged_etfs(project_root)
    manifest_after = _manifest_for_paths(project_root, roots)
    if not manifest_before.equals(manifest_after):
        raise RuntimeError("retained Parquet inputs changed during the backtest read")

    signals = compute_signals(primary)
    outcomes = build_forward_outcomes(primary)
    events = build_condition_event_table(signals, outcomes)
    condition_summary = summarize_condition_events(events, outcomes)
    score_input = _score_input(signals, outcomes)
    score_grid, score_winners, _ = run_score_grid_analysis(
        score_input, min_events=min_score_events
    )
    walk_forward = expanding_five_year_score_study(
        score_input, min_events=min_score_events
    )

    leveraged_signals = compute_signals(leveraged)
    leveraged_outcomes = build_forward_outcomes(leveraged)
    leveraged_events = build_condition_event_table(leveraged_signals, leveraged_outcomes)
    leveraged_summary = (
        leveraged_events.groupby(["series_id", "rule", "horizon"], sort=True)
        .agg(
            n=("forward_return", "count"),
            mean_return=("forward_return", "mean"),
            median_return=("forward_return", "median"),
            hit_rate=("forward_return", lambda value: value.gt(0).mean()),
            mean_max_drawdown=("forward_max_drawdown", "mean"),
        )
        .reset_index()
    )
    leveraged_summary["low_sample"] = leveraged_summary["n"].lt(MIN_CELL_EVENTS)

    coverage = pd.concat(
        [_coverage(primary, "PRIMARY_INDEX"), _coverage(leveraged, "LEVERAGED_ETF_DESCRIPTIVE_ONLY")],
        ignore_index=True,
    )
    date_key = output_date or datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y%m%d")
    if len(date_key) != 8 or not date_key.isdigit():
        raise ValueError("output_date must be YYYYMMDD")
    output_dir = project_root / "artifacts/research/condition_backtest" / date_key
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest_digest = _manifest_digest(manifest_before)
    conclusions = _conclusions(condition_summary, score_winners, coverage)
    condition_table = _condition_markdown(condition_summary)
    winner_table = _winner_markdown(score_winners)
    coverage_md = coverage.copy()
    coverage_md["start"] = pd.to_datetime(coverage_md["start"]).dt.strftime("%Y-%m-%d")
    coverage_md["end"] = pd.to_datetime(coverage_md["end"]).dt.strftime("%Y-%m-%d")
    wf_display = walk_forward.reindex(columns=[
        "basket", "test_start", "test_end", "status", "threshold_id",
        "threshold_stable", "train_n", "test_n", "test_mean", "test_hit_rate",
    ]).copy()
    for column in ("test_mean", "test_hit_rate"):
        wf_display[column] = wf_display[column].map(_fmt_pct)

    summary_text = "\n".join(
        [
            "# 조건 시나리오 백테스트",
            "",
            f"- 입력 manifest SHA-256: `{manifest_digest}` ({len(manifest_before)} Parquet files)",
            f"- 적합: 관측일·60세션 결과 종료일 모두 `{FIT_END}` 이하",
            f"- 홀드아웃: 관측일 `{HOLDOUT_START}` 이상",
            f"- 점수 후보 최소 사건 수 N: `{min_score_events}`; 셀 경고 기준: `{MIN_CELL_EVENTS}` 미만",
            "- 성격: 종가 기준 오프라인 조건부 분포 연구(체결·비용·실행 가능성 없음)",
            "",
            "## 자료 범위",
            "",
            _markdown_table(coverage_md, coverage_md.columns),
            "",
            "## 조건 사건 결과",
            "",
            "`n` 뒤의 ⚠는 15건 미만이다. 최대낙폭은 사건 종가를 포함한 미래 경로의 peak-to-trough 값이다.",
            "",
            _markdown_table(condition_table, condition_table.columns),
            "",
            "## 낙폭 과대 점수: 통합 임계값과 바스켓별 임계값",
            "",
            "각 축은 세 단계로 1점씩, 합계 0~9점이다. 순위는 적합 구간 60세션 평균수익만으로 정했다.",
            "",
            _markdown_table(winner_table, winner_table.columns),
            "",
            "## 5년 단위 expanding walk-forward 안정성",
            "",
            _markdown_table(wf_display, wf_display.columns) if not wf_display.empty else "적격 fold 없음.",
            "",
            "## 결론 (10줄 이내)",
            "",
            *[f"{index}. {line}" for index, line in enumerate(conclusions, start=1)],
            "",
            "## 재현 및 해석 제한",
            "",
            "- `input_manifest.csv`가 읽기 전후 동일함을 확인한 뒤 결과를 썼다.",
            "- `build_forward_labels`, `define_untouched_holdout`, `expanding_walk_forward`는 각각 고정 5/20/60 기간·최종 5년 봉인·관측수 기반 fold 계약이라 이 연구의 120세션·고정 2016 경계·5년 달력 refit과 맞지 않아 최소 로컬 구현을 사용했다.",
            "- SP500/NASDAQ_COMPOSITE/DOW_JONES는 통합 바스켓에만 들어간다. US_TECH 전용 바스켓은 NASDAQ100이다.",
            "- 레버리지 ETF는 `leveraged_etf_event_summary.csv`에만 기록되고 score grid/winner/walk-forward에는 들어가지 않는다.",
            "- 이는 조건이 발생한 종가에서 본 사후 분포이며, T+1 체결·거래비용·세금·환율·추적오차를 모델링하지 않는다.",
            "",
        ]
    )

    _write_csv(manifest_before, output_dir / "input_manifest.csv")
    _write_csv(coverage, output_dir / "series_coverage.csv")
    _write_csv(events, output_dir / "condition_events.csv")
    _write_csv(condition_summary, output_dir / "condition_event_summary.csv")
    _write_csv(score_grid, output_dir / "score_grid.csv")
    _write_csv(score_winners, output_dir / "score_winners.csv")
    _write_csv(walk_forward, output_dir / "score_walk_forward.csv")
    _write_csv(leveraged_events, output_dir / "leveraged_etf_events.csv")
    _write_csv(leveraged_summary, output_dir / "leveraged_etf_event_summary.csv")
    summary_path = output_dir / "summary.md"
    _write_text(summary_text, summary_path)
    return BacktestResult(summary_path=summary_path, output_dir=output_dir, conclusion_lines=conclusions)


__all__ = [
    "BacktestResult",
    "CONDITION_COLUMNS",
    "DEFAULT_MIN_SCORE_EVENTS",
    "FIT_END",
    "HOLDOUT_START",
    "HORIZONS",
    "add_condition_events",
    "build_condition_event_table",
    "build_forward_outcomes",
    "compute_signals",
    "expanding_five_year_score_study",
    "load_leveraged_etfs",
    "load_primary_indices",
    "run_condition_backtest",
    "run_score_grid_analysis",
    "summarize_condition_events",
    "wilder_rsi",
]
