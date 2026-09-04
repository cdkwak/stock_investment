"""Retained-data leveraged-product transformations for compound research.

The module has no provider or credential path.  Index close T is treated as an
end-of-day observation; callers own the T+1 strategy execution clock.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Iterable, Mapping

import numpy as np
import pandas as pd
import pyarrow.dataset as pads

from .condition_backtest import load_leveraged_etfs, load_primary_indices


TRADING_DAYS = 252
DEFAULT_SHORT_RATE = 0.025
DEFAULT_EXPENSE_RATIO = {1: 0.0035, 2: 0.009, 3: 0.009}
FOREIGN_SYMBOLS: tuple[str, ...] = (
    "NIKKEI225",
    "TAIEX",
    "EURO_STOXX50",
    "HANG_SENG",
    "DAX",
)

# A retained real product is used only for overlap calibration.  The mapping is
# explicit so no product/index identity is guessed from a ticker at runtime.
REAL_PRODUCT_MAP: Mapping[tuple[str, int], str] = {
    ("KOSPI", 2): "123320",
    ("KOSPI200", 2): "123320",
    ("KOSPI200_IT", 2): "243880",
    ("NASDAQ100", 2): "QLD",
    ("NASDAQ100", 3): "TQQQ",
    ("SOX", 3): "SOXL",
}


@dataclass(frozen=True, slots=True)
class ShortRateSeries:
    annual_rate: pd.Series
    source: str
    fallback_used: bool


@dataclass(frozen=True, slots=True)
class TrackingGap:
    underlying: str
    product_symbol: str
    leverage_multiple: int
    start: str
    end: str
    observations: int
    real_final_multiple: float
    synthetic_final_multiple: float
    annualized_gap: float
    calibrated_extra_drag: float


def _dataset_frame(path: Path, *, partitioning: str | None, columns: Iterable[str]) -> pd.DataFrame:
    dataset = pads.dataset(path, format="parquet", partitioning=partitioning)
    selected = [name for name in columns if name in dataset.schema.names]
    return dataset.to_table(columns=selected).to_pandas()


def load_index_universe(project_root: Path) -> pd.DataFrame:
    """Load the supported retained indices, including optional foreign symbols."""

    root = Path(project_root).resolve()
    primary = load_primary_indices(root).copy()
    global_path = root / "data/normalized/global_index_price_daily"
    foreign = pd.DataFrame()
    if global_path.exists():
        raw = _dataset_frame(
            global_path,
            partitioning=None,
            columns=("date", "symbol", "close", "volume"),
        )
        if "symbol" in raw:
            foreign = raw.loc[raw["symbol"].astype(str).isin(FOREIGN_SYMBOLS)].copy()
            if not foreign.empty:
                foreign["series_id"] = foreign["symbol"].astype(str)
                foreign["basket"] = "FOREIGN"
                foreign["dataset_source"] = "global_index_price_daily"
                if "volume" not in foreign:
                    foreign["volume"] = np.nan
                foreign = foreign[[
                    "date", "series_id", "basket", "close", "volume", "dataset_source"
                ]]
    combined = pd.concat([primary, foreign], ignore_index=True, sort=False)
    combined["date"] = pd.to_datetime(combined["date"], errors="raise").dt.normalize()
    combined["series_id"] = combined["series_id"].astype(str)
    combined["close"] = pd.to_numeric(combined["close"], errors="raise")
    combined = combined.drop_duplicates(["series_id", "date"], keep="last")
    return combined.sort_values(["series_id", "date"], kind="mergesort").reset_index(drop=True)


def load_short_rate(project_root: Path, dates: pd.Series | pd.DatetimeIndex) -> ShortRateSeries:
    """Load a retained FRED 3-month bill series or return the declared fallback."""

    root = Path(project_root).resolve()
    target_dates = pd.DatetimeIndex(pd.to_datetime(dates, errors="raise")).normalize()
    column_priority = ("dtb3", "tb3ms", "dgs3mo", "dgs3m", "rate_3m", "three_month")
    normalized = root / "data/normalized"
    for path in sorted(normalized.glob("fred_*"), key=lambda item: item.as_posix()):
        try:
            dataset = pads.dataset(path, format="parquet", partitioning="hive")
        except (FileNotFoundError, OSError, ValueError):
            continue
        lower = {name.lower(): name for name in dataset.schema.names}
        rate_column = next((lower[name] for name in column_priority if name in lower), None)
        date_column = lower.get("date")
        if rate_column is None or date_column is None:
            continue
        frame = dataset.to_table(columns=[date_column, rate_column]).to_pandas()
        frame[date_column] = pd.to_datetime(frame[date_column], errors="raise").dt.normalize()
        frame[rate_column] = pd.to_numeric(frame[rate_column], errors="coerce") / 100.0
        frame = frame.dropna().drop_duplicates(date_column, keep="last").sort_values(date_column)
        aligned = (
            frame.set_index(date_column)[rate_column]
            .reindex(target_dates, method="ffill")
            .fillna(DEFAULT_SHORT_RATE)
            .astype("float64")
        )
        aligned.index = target_dates
        return ShortRateSeries(aligned, f"{path.name}:{rate_column}", False)
    fallback = pd.Series(DEFAULT_SHORT_RATE, index=target_dates, dtype="float64")
    return ShortRateSeries(fallback, "constant_2.5pct_annual", True)


def synthetic_daily_returns(
    index_close: pd.Series,
    *,
    leverage_multiple: int,
    annual_expense_ratio: float | None = None,
    annual_short_rate: float | pd.Series = DEFAULT_SHORT_RATE,
    annual_tracking_drag: float = 0.0,
) -> pd.Series:
    """Return a daily-reset synthetic product return series.

    Expense, financing, and calibrated tracking drag accrue on retained market
    sessions using a 252-session convention.  The first return is zero.
    """

    if leverage_multiple not in (1, 2, 3):
        raise ValueError("leverage_multiple must be 1, 2, or 3")
    close = pd.to_numeric(index_close, errors="raise").astype("float64")
    if close.empty or not np.isfinite(close).all() or close.le(0).any():
        raise ValueError("index closes must be finite and positive")
    expense = DEFAULT_EXPENSE_RATIO[leverage_multiple] if annual_expense_ratio is None else float(annual_expense_ratio)
    if expense < 0 or annual_tracking_drag < 0:
        raise ValueError("expense and tracking drag must be non-negative")
    if isinstance(annual_short_rate, pd.Series):
        short_rate = pd.to_numeric(annual_short_rate.reindex(close.index), errors="coerce")
        if short_rate.isna().any():
            raise ValueError("short-rate series must cover every index session")
    else:
        short_rate = pd.Series(float(annual_short_rate), index=close.index)
    if not np.isfinite(short_rate).all() or short_rate.lt(0).any():
        raise ValueError("short rate must be finite and non-negative")
    index_return = close.pct_change(fill_method=None).fillna(0.0)
    daily_cost = (expense + (leverage_multiple - 1) * short_rate + annual_tracking_drag) / TRADING_DAYS
    result = leverage_multiple * index_return - daily_cost
    result.iloc[0] = 0.0
    if result.le(-1.0).any():
        # A daily-reset long product cannot have negative NAV.  Once wiped out,
        # it remains at zero; retained histories in scope do not hit this gate.
        first = int(np.flatnonzero(result.to_numpy() <= -1.0)[0])
        result.iloc[first] = -1.0
        result.iloc[first + 1 :] = 0.0
    return result.astype("float64")


def price_from_returns(returns: pd.Series, *, initial: float = 1.0) -> pd.Series:
    values = pd.to_numeric(returns, errors="raise").astype("float64")
    if values.le(-1.0).any():
        wealth = np.cumprod(1.0 + values.to_numpy()) * float(initial)
    else:
        wealth = np.exp(np.log1p(values.to_numpy()).cumsum()) * float(initial)
    return pd.Series(wealth, index=returns.index, name="product_close")


def load_real_products(project_root: Path) -> pd.DataFrame:
    frame = load_leveraged_etfs(Path(project_root).resolve()).copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="raise").dt.normalize()
    frame["series_id"] = frame["series_id"].astype(str)
    frame["close"] = pd.to_numeric(frame["close"], errors="raise")
    return frame.sort_values(["series_id", "date"], kind="mergesort").reset_index(drop=True)


def realized_tracking_gap(
    index_frame: pd.DataFrame,
    real_products: pd.DataFrame,
    *,
    underlying: str,
    leverage_multiple: int,
    short_rate: ShortRateSeries,
) -> TrackingGap | None:
    """Calibrate signed annual real-minus-synthetic return on common sessions."""

    product_symbol = REAL_PRODUCT_MAP.get((underlying, leverage_multiple))
    if product_symbol is None:
        return None
    index_part = index_frame.loc[index_frame["series_id"].eq(underlying), ["date", "close"]].copy()
    real_part = real_products.loc[
        real_products["series_id"].eq(product_symbol), ["date", "close"]
    ].copy()
    common = index_part.merge(real_part, on="date", suffixes=("_index", "_real"), validate="one_to_one")
    common = common.sort_values("date", kind="mergesort").reset_index(drop=True)
    if len(common) < 2:
        return None
    rate = short_rate.annual_rate.reindex(pd.DatetimeIndex(common["date"]), method="ffill")
    rate.index = common.index
    synthetic = synthetic_daily_returns(
        common["close_index"],
        leverage_multiple=leverage_multiple,
        annual_short_rate=rate,
    )
    real_return = common["close_real"].pct_change(fill_method=None)
    valid = real_return.notna() & synthetic.notna() & real_return.gt(-1.0) & synthetic.gt(-1.0)
    if int(valid.sum()) < 2:
        return None
    real_log = float(np.log1p(real_return.loc[valid]).sum())
    synthetic_log = float(np.log1p(synthetic.loc[valid]).sum())
    observations = int(valid.sum())
    annualized_gap = float(np.expm1((real_log - synthetic_log) * TRADING_DAYS / observations))
    return TrackingGap(
        underlying=underlying,
        product_symbol=product_symbol,
        leverage_multiple=leverage_multiple,
        start=pd.Timestamp(common.loc[valid.idxmax(), "date"]).strftime("%Y-%m-%d"),
        end=pd.Timestamp(common.loc[valid[valid].index[-1], "date"]).strftime("%Y-%m-%d"),
        observations=observations,
        real_final_multiple=float(np.exp(real_log)),
        synthetic_final_multiple=float(np.exp(synthetic_log)),
        annualized_gap=annualized_gap,
        calibrated_extra_drag=max(0.0, -annualized_gap),
    )


def volatility_drag(index_close: pd.Series, synthetic_returns: pd.Series, multiple: int) -> dict[str, float]:
    close = pd.to_numeric(index_close, errors="raise").astype("float64")
    index_multiple = float(close.iloc[-1] / close.iloc[0])
    synthetic_multiple = float(np.prod(1.0 + pd.to_numeric(synthetic_returns, errors="raise")))
    linear_multiple = float(1.0 + multiple * (index_multiple - 1.0))
    return {
        "index_final_multiple": index_multiple,
        "synthetic_final_multiple": synthetic_multiple,
        "linear_multiple_of_index_buy_hold": linear_multiple,
        "volatility_and_cost_drag_multiple": synthetic_multiple - linear_multiple,
    }


def retained_manifest_digest(project_root: Path, paths: Iterable[Path]) -> tuple[str, list[dict[str, object]]]:
    """Bind replay to exact retained Parquet bytes used by this experiment."""

    root = Path(project_root).resolve()
    files: list[Path] = []
    for path in paths:
        resolved = path if path.is_absolute() else root / path
        if resolved.is_file() and resolved.suffix == ".parquet":
            files.append(resolved)
        elif resolved.is_dir():
            files.extend(resolved.rglob("*.parquet"))
    inventory: list[dict[str, object]] = []
    aggregate = hashlib.sha256()
    for path in sorted(set(files), key=lambda item: item.relative_to(root).as_posix()):
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        relative = path.relative_to(root).as_posix()
        size = path.stat().st_size
        inventory.append({"path": relative, "bytes": size, "sha256": digest})
        aggregate.update(relative.encode("utf-8"))
        aggregate.update(b"\0")
        aggregate.update(digest.encode("ascii"))
        aggregate.update(b"\n")
    return aggregate.hexdigest(), inventory


__all__ = [
    "DEFAULT_EXPENSE_RATIO",
    "DEFAULT_SHORT_RATE",
    "FOREIGN_SYMBOLS",
    "REAL_PRODUCT_MAP",
    "ShortRateSeries",
    "TrackingGap",
    "load_index_universe",
    "load_real_products",
    "load_short_rate",
    "price_from_returns",
    "realized_tracking_gap",
    "retained_manifest_digest",
    "synthetic_daily_returns",
    "volatility_drag",
]
