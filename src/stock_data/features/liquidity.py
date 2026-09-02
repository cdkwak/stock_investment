"""As-of liquidity inputs for local Korean-equity discovery tools."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, datetime
from pathlib import Path
import re

import pandas as pd


_PRICE_RELATIVE = Path("data/normalized/kr_equity_price_daily")
_MARKET_CAP_RELATIVE = Path("data/normalized/kr_equity_market_cap_daily")
_YEAR_PARTITION = re.compile(r"year=(\d{4})\Z")
_OUTPUT_COLUMNS = ("symbol", "avg_value_20d", "market_cap")


def _empty_snapshot() -> pd.DataFrame:
    return pd.DataFrame({
        "symbol": pd.Series(dtype="string"),
        "avg_value_20d": pd.Series(dtype="Float64"),
        "market_cap": pd.Series(dtype="Int64"),
    })


def _as_timestamp(as_of: str | date | datetime | pd.Timestamp) -> pd.Timestamp:
    value = pd.Timestamp(as_of)
    if pd.isna(value):
        raise ValueError("as_of must be a valid date")
    if value.tzinfo is not None:
        value = value.tz_convert("Asia/Seoul").tz_localize(None)
    return value.normalize()


def _partition_year(path: Path) -> int | None:
    for part in reversed(path.parts):
        match = _YEAR_PARTITION.fullmatch(part)
        if match:
            return int(match.group(1))
    return None


def _parquet_files(root: Path) -> tuple[Path, ...]:
    if not root.is_dir():
        raise FileNotFoundError(f"normalized dataset is missing: {root}")
    paths = tuple(sorted(root.rglob("*.parquet")))
    if not paths:
        raise FileNotFoundError(f"normalized dataset has no parquet files: {root}")
    return paths


def _read_files(paths: Iterable[Path], columns: list[str], as_of: pd.Timestamp) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for path in paths:
        frame = pd.read_parquet(path, columns=columns, engine="pyarrow")
        missing = set(columns) - set(frame.columns)
        if missing:
            raise ValueError(f"normalized parquet is missing columns: {sorted(missing)}")
        frame = frame.copy()
        frame["date"] = pd.to_datetime(frame["date"], errors="raise").dt.normalize()
        frame = frame.loc[frame["date"] <= as_of]
        if not frame.empty:
            frames.append(frame)
    if not frames:
        return pd.DataFrame(columns=columns)
    return pd.concat(frames, ignore_index=True)


def _recent_rows(
    root: Path,
    *,
    columns: list[str],
    as_of: pd.Timestamp,
    required_sessions: int,
) -> pd.DataFrame:
    paths = _parquet_files(root)
    by_year: dict[int | None, list[Path]] = {}
    for path in paths:
        by_year.setdefault(_partition_year(path), []).append(path)

    # Partition pruning keeps the ordinary path small while still crossing a
    # calendar-year boundary when an early-January snapshot needs prior dates.
    selected: list[pd.DataFrame] = []
    unpartitioned = by_year.pop(None, [])
    if unpartitioned:
        selected.append(_read_files(unpartitioned, columns, as_of))
    observed_dates: set[pd.Timestamp] = set()
    for year in sorted((year for year in by_year if year <= as_of.year), reverse=True):
        frame = _read_files(by_year[year], columns, as_of)
        if not frame.empty:
            selected.append(frame)
            observed_dates.update(frame["date"].unique())
        if len(observed_dates) >= required_sessions:
            break

    result = [frame for frame in selected if not frame.empty]
    if not result:
        return pd.DataFrame(columns=columns)
    return pd.concat(result, ignore_index=True)


def _validate_identity(frame: pd.DataFrame, *, dataset: str) -> pd.DataFrame:
    checked = frame.copy()
    checked["symbol"] = checked["symbol"].astype("string")
    if checked["symbol"].isna().any() or checked["symbol"].str.strip().eq("").any():
        raise ValueError(f"{dataset} contains a missing symbol")
    if checked.duplicated(["date", "symbol"]).any():
        raise ValueError(f"{dataset} contains duplicate date-symbol rows")
    return checked


def liquidity_snapshot(
    project_root: str | Path,
    as_of: str | date | datetime | pd.Timestamp,
) -> pd.DataFrame:
    """Return local as-of liquidity inputs without provider or filesystem writes.

    ``avg_value_20d`` is the arithmetic mean of ``trading_value`` over the last
    20 distinct retained market sessions at or before ``as_of``.  A symbol with
    fewer than 20 observations in that common session window receives null,
    rather than a shortened-window estimate.  ``market_cap`` comes from the
    latest retained market-cap session at or before ``as_of``.  Missing inputs
    stay null and symbols are never silently dropped by an inner join.
    """

    root = Path(project_root)
    cutoff = _as_timestamp(as_of)
    price = _recent_rows(
        root / _PRICE_RELATIVE,
        columns=["date", "symbol", "trading_value"],
        as_of=cutoff,
        required_sessions=20,
    )
    market_cap = _recent_rows(
        root / _MARKET_CAP_RELATIVE,
        columns=["date", "symbol", "market_cap"],
        as_of=cutoff,
        required_sessions=1,
    )
    if price.empty and market_cap.empty:
        return _empty_snapshot()

    if price.empty:
        averages = pd.DataFrame({
            "symbol": pd.Series(dtype="string"),
            "avg_value_20d": pd.Series(dtype="Float64"),
        })
    else:
        price = _validate_identity(price, dataset="kr_equity_price_daily")
        price["trading_value"] = pd.to_numeric(price["trading_value"], errors="raise")
        sessions = tuple(sorted(price["date"].unique())[-20:])
        window = price.loc[price["date"].isin(sessions)]
        grouped = window.groupby("symbol", sort=True, observed=True)["trading_value"]
        averages = grouped.agg(["count", "mean"]).reset_index()
        averages["avg_value_20d"] = averages["mean"].astype("Float64").where(
            averages["count"].eq(20), pd.NA,
        )
        averages = averages[["symbol", "avg_value_20d"]]

    if market_cap.empty:
        caps = pd.DataFrame({
            "symbol": pd.Series(dtype="string"),
            "market_cap": pd.Series(dtype="Int64"),
        })
    else:
        market_cap = _validate_identity(
            market_cap, dataset="kr_equity_market_cap_daily",
        )
        latest_cap_date = market_cap["date"].max()
        caps = market_cap.loc[
            market_cap["date"].eq(latest_cap_date), ["symbol", "market_cap"],
        ].copy()
        caps["market_cap"] = pd.to_numeric(caps["market_cap"], errors="raise").astype("Int64")

    result = averages.merge(caps, on="symbol", how="outer", validate="one_to_one")
    result["symbol"] = result["symbol"].astype("string")
    result["avg_value_20d"] = result["avg_value_20d"].astype("Float64")
    result["market_cap"] = result["market_cap"].astype("Int64")
    return result.loc[:, _OUTPUT_COLUMNS].sort_values("symbol").reset_index(drop=True)


__all__ = ["liquidity_snapshot"]
