from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


INPUT_DATASET = "kr_kospi200_options_provider_bridge_daily"
EXPIRY_STATUS = "MATURITY_MONTH_ONLY_EXACT_EXPIRY_NOT_AVAILABLE"
WALL_SELECTION_RULE = "MAX_OI_LOWEST_STRIKE_REPRESENTATIVE_ALL_CANDIDATES_PRESERVED"
NEAR_WALL_SELECTION_RULE = "MAX_OI_WITHIN_15PCT_OF_EXPLICIT_UNDERLYING_LOWEST_STRIKE"
NEAR_WALL_WINDOW_PCT = 15.0
FRONT_SELECTION_RULE = "MIN_RETAINED_MATURITY_MONTH_NOT_BEFORE_TRADE_MONTH"
VERIFIED_STATUS = "WALL_ANALYSIS_VERIFIED"
LIMITED_STATUS = "WALL_RANKING_USABLE_WITH_UNIT_LIMIT"
WALL_AVAILABLE = "WALL_AVAILABLE"
NO_OPEN_INTEREST = "NO_OPEN_INTEREST"
NO_OI_OBSERVATION = "NO_OI_OBSERVATION"
NO_NEAR_WINDOW_OI = "NO_NEAR_WINDOW_OI"
EXTREME_MONEYNESS = "EXTREME_MONEYNESS"
KOSPI200_SYMBOL = "KOSPI200"
PIT_SAFE_EOD_T_PLUS_1 = "PIT_SAFE_EOD_T_PLUS_1"

_REQUIRED = {
    "date", "maturity_month", "strike", "call_put", "open_interest", "volume",
    "bridge_segment", "session", "source",
}


class OptionWallError(ValueError):
    pass


@dataclass(frozen=True)
class MoneynessWarningPolicy:
    """Caller-owned warning threshold; None means no threshold has been accepted."""

    max_abs_distance_pct: float | None = None

    def __post_init__(self) -> None:
        value = self.max_abs_distance_pct
        if value is not None and (not np.isfinite(value) or value <= 0):
            raise OptionWallError("max_abs_distance_pct must be finite and positive")


def _prepare(options: pd.DataFrame) -> pd.DataFrame:
    missing = sorted(_REQUIRED.difference(options.columns))
    if missing:
        raise OptionWallError(f"missing required option columns: {missing}")
    frame = options.loc[:, sorted(_REQUIRED)].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    null_keys = frame[["date", "maturity_month", "strike", "call_put"]].isna().any(axis=1)
    if null_keys.any():
        raise OptionWallError(f"null option identity keys: {int(null_keys.sum())}")
    frame["maturity_month"] = frame["maturity_month"].astype("string")
    if not frame["maturity_month"].str.fullmatch(r"\d{4}-\d{2}").all():
        raise OptionWallError("invalid maturity_month; expected YYYY-MM")
    frame["call_put"] = frame["call_put"].astype("string").str.upper()
    if not frame["call_put"].isin(["CALL", "PUT"]).all():
        raise OptionWallError("call_put must contain only CALL or PUT")
    frame["strike"] = pd.to_numeric(frame["strike"], errors="coerce")
    frame["open_interest"] = pd.to_numeric(frame["open_interest"], errors="coerce")
    frame["volume"] = pd.to_numeric(frame["volume"], errors="coerce")
    if frame["strike"].isna().any() or (frame["strike"] <= 0).any():
        raise OptionWallError("strike must be finite and positive")
    if (frame[["open_interest", "volume"]].dropna() < 0).any().any():
        raise OptionWallError("open_interest and volume cannot be negative")
    duplicates = frame.duplicated(["date", "maturity_month", "strike", "call_put"])
    if duplicates.any():
        raise OptionWallError("duplicate date/maturity_month/strike/call_put rows")
    return frame


def compute_option_walls(options: pd.DataFrame) -> pd.DataFrame:
    """Compute all-strike walls and retain candidates for the explicit near-wall join."""
    frame = _prepare(options)
    keys = ["date", "maturity_month"]
    metadata_columns = ["bridge_segment", "session", "source"]
    metadata_counts = frame.groupby(keys, observed=True)[metadata_columns].nunique(dropna=True)
    if metadata_counts.ne(1).any().any():
        raise OptionWallError("provider boundary is not unique within date/maturity_month")
    result = frame.groupby(keys, observed=True)[metadata_columns].first().reset_index()

    totals = frame.groupby(keys + ["call_put"], observed=True).agg(
        total_oi=("open_interest", lambda values: values.sum(min_count=1)),
        total_volume=("volume", lambda values: values.sum(min_count=1)),
    )
    totals = totals.unstack("call_put")
    totals.columns = [f"total_{side.lower()}_{metric.removeprefix('total_')}" for metric, side in totals.columns]
    result = result.merge(totals.reset_index(), on=keys, how="left", validate="one_to_one")

    eligible = frame.loc[frame["open_interest"].notna()].copy()
    side_keys = keys + ["call_put"]
    eligible["max_oi"] = eligible.groupby(side_keys, observed=True)["open_interest"].transform("max")
    candidates = eligible.loc[eligible["open_interest"].eq(eligible["max_oi"])].sort_values(
        side_keys + ["strike"]
    )
    wall_stats = candidates.groupby(side_keys, observed=True).agg(
        wall_strike=("strike", "first"),
        wall_oi=("open_interest", "first"),
        wall_volume=("volume", "first"),
        wall_candidate_count=("strike", "size"),
        wall_candidate_strikes=("strike", lambda values: "|".join(f"{float(value):g}" for value in values)),
    )
    wall_stats["wall_tie"] = wall_stats["wall_candidate_count"].gt(1)
    wall_stats = wall_stats.unstack("call_put")
    wall_stats.columns = [f"{side.lower()}_{metric}" for metric, side in wall_stats.columns]
    result = result.merge(wall_stats.reset_index(), on=keys, how="left", validate="one_to_one")

    # The authoritative KOSPI200 close is joined separately, so preserve the
    # complete strike/OI candidates only in this in-memory intermediate.  The
    # private columns are consumed and removed by ``join_kospi200_daily_index``.
    candidate_rows: list[dict[str, object]] = []
    for (observed, maturity, side), group in eligible.groupby(side_keys, observed=True):
        candidate_rows.append({
            "date": observed,
            "maturity_month": maturity,
            "call_put": side,
            "candidates": tuple(
                (float(row.strike), float(row.open_interest), _optional_float(row.volume))
                for row in group.sort_values("strike").itertuples(index=False)
            ),
        })
    if candidate_rows:
        packed = pd.DataFrame(candidate_rows).pivot(
            index=keys, columns="call_put", values="candidates",
        ).reset_index()
        packed = packed.rename(columns={
            "CALL": "_call_wall_candidates", "PUT": "_put_wall_candidates",
        })
        result = result.merge(packed, on=keys, how="left", validate="one_to_one")
    for side in ("call", "put"):
        internal = f"_{side}_wall_candidates"
        if internal not in result:
            result[internal] = [tuple() for _ in range(len(result))]
    for side in ("call", "put"):
        result[f"{side}_wall_candidate_count"] = (
            result[f"{side}_wall_candidate_count"].fillna(0).astype("int64")
        )
        result[f"{side}_wall_tie"] = result[f"{side}_wall_tie"].fillna(False).astype(bool)
        no_observation = result[f"{side}_wall_oi"].isna()
        no_interest = result[f"{side}_wall_oi"].eq(0)
        result[f"{side}_wall_status"] = np.select(
            [no_observation, no_interest], [NO_OI_OBSERVATION, NO_OPEN_INTEREST],
            default=WALL_AVAILABLE,
        )
        # Zero-OI candidates remain evidence, but zero interest cannot define a wall.
        result.loc[no_interest, f"{side}_wall_strike"] = np.nan
        result.loc[no_interest, f"{side}_wall_volume"] = np.nan

    result["analysis_status"] = np.where(
        result["date"].dt.year.ge(2020), VERIFIED_STATUS, LIMITED_STATUS
    )
    result["expiry_status"] = EXPIRY_STATUS
    result["wall_selection_rule"] = WALL_SELECTION_RULE
    result["oi_put_call_ratio"] = result["total_put_oi"].div(
        result["total_call_oi"].replace(0, np.nan)
    )
    result["volume_put_call_ratio"] = result["total_put_volume"].div(
        result["total_call_volume"].replace(0, np.nan)
    )
    result = result.sort_values(keys).reset_index(drop=True)
    for side in ("call", "put"):
        grouping = [result["maturity_month"], result["bridge_segment"], result["session"]]
        result[f"{side}_wall_oi_change_1d"] = result.groupby(grouping, sort=False)[
            f"{side}_wall_oi"
        ].diff()
        result[f"{side}_wall_strike_change_1d"] = result.groupby(grouping, sort=False)[
            f"{side}_wall_strike"
        ].diff()
    return compute_wall_distance(result)


def _optional_float(value: object) -> float | None:
    return None if pd.isna(value) else float(value)


def _compute_near_walls(walls: pd.DataFrame) -> pd.DataFrame:
    """Select maximum OI strikes inside ±15% of the explicit underlying."""
    result = walls.copy()
    result["near_wall_window_pct"] = NEAR_WALL_WINDOW_PCT
    result["near_wall_selection_rule"] = NEAR_WALL_SELECTION_RULE
    for side in ("call", "put"):
        prefix = f"near_{side}_wall"
        values: dict[str, list[object]] = {
            "strike": [], "oi": [], "volume": [], "candidate_count": [],
            "candidate_strikes": [], "tie": [], "status": [],
        }
        for _, row in result.iterrows():
            underlying = _optional_float(row.get("underlying_price", np.nan))
            candidates = row.get(f"_{side}_wall_candidates", ()) or ()
            near = [] if underlying is None else [
                candidate for candidate in candidates
                if abs(candidate[0] / underlying - 1.0) <= NEAR_WALL_WINDOW_PCT / 100.0 + 1e-12
            ]
            positive = [candidate for candidate in near if candidate[1] > 0]
            if not positive:
                values["strike"].append(np.nan)
                values["oi"].append(np.nan)
                values["volume"].append(np.nan)
                values["candidate_count"].append(0)
                values["candidate_strikes"].append("")
                values["tie"].append(False)
                values["status"].append(NO_NEAR_WINDOW_OI)
                continue
            maximum = max(candidate[1] for candidate in positive)
            winners = sorted(candidate for candidate in positive if candidate[1] == maximum)
            winner = winners[0]
            values["strike"].append(winner[0])
            values["oi"].append(winner[1])
            values["volume"].append(winner[2])
            values["candidate_count"].append(len(winners))
            values["candidate_strikes"].append("|".join(f"{item[0]:g}" for item in winners))
            values["tie"].append(len(winners) > 1)
            values["status"].append(WALL_AVAILABLE)
        for suffix, column in values.items():
            result[f"{prefix}_{suffix}"] = column
        result[f"{prefix}_distance"] = result[f"{prefix}_strike"] - result["underlying_price"]
        result[f"{prefix}_distance_pct"] = (
            result[f"{prefix}_strike"] / result["underlying_price"] - 1.0
        ) * 100.0
    return result.drop(
        columns=["_call_wall_candidates", "_put_wall_candidates"], errors="ignore",
    )


def compute_wall_distance(
    walls: pd.DataFrame,
    *,
    warning_policy: MoneynessWarningPolicy | None = None,
) -> pd.DataFrame:
    """Calculate distances from an already explicit-joined underlying_price column."""
    result = walls.copy()
    if "underlying_price" not in result:
        result["underlying_price"] = np.nan
    else:
        result["underlying_price"] = pd.to_numeric(result["underlying_price"], errors="coerce")
        invalid = result["underlying_price"].notna() & result["underlying_price"].le(0)
        if invalid.any():
            raise OptionWallError("underlying_price must be positive when present")
    for column in ("underlying_dataset", "underlying_source", "underlying_pit_status"):
        if column not in result:
            result[column] = None
    for side in ("call", "put"):
        result[f"{side}_wall_distance"] = result[f"{side}_wall_strike"] - result["underlying_price"]
        result[f"{side}_wall_distance_pct"] = (
            result[f"{side}_wall_strike"] / result["underlying_price"] - 1.0
        ) * 100.0
        result[f"{side}_wall_warning"] = None
        threshold = warning_policy.max_abs_distance_pct if warning_policy else None
        if threshold is not None:
            extreme = result[f"{side}_wall_distance_pct"].abs().gt(threshold)
            result.loc[extreme, f"{side}_wall_warning"] = EXTREME_MONEYNESS
    return result


def join_kospi200_daily_index(
    walls: pd.DataFrame,
    index_daily: pd.DataFrame,
    *,
    dataset_name: str,
    symbol: str,
    pit_status: str,
    warning_policy: MoneynessWarningPolicy | None = None,
    require_complete: bool = True,
) -> pd.DataFrame:
    """Explicit same-date join to an authority-labelled PIT-safe KOSPI200 index."""
    if symbol != KOSPI200_SYMBOL or pit_status != PIT_SAFE_EOD_T_PLUS_1:
        raise OptionWallError(
            "underlying join requires explicit KOSPI200 symbol and PIT_SAFE_EOD_T_PLUS_1 status"
        )
    required = {"date", "symbol", "close", "source"}
    if not required.issubset(index_daily.columns):
        raise OptionWallError(f"index input missing columns: {sorted(required - set(index_daily.columns))}")
    selected = index_daily.loc[index_daily["symbol"].eq(symbol), ["date", "close", "source"]].copy()
    if selected.empty:
        raise OptionWallError(f"no {symbol} rows in explicit index input")
    selected["date"] = pd.to_datetime(selected["date"], errors="coerce").dt.normalize()
    if selected["date"].isna().any() or selected["date"].duplicated().any():
        raise OptionWallError("KOSPI200 index dates must be non-null and unique")
    if selected["source"].isna().any() or selected["source"].astype(str).nunique() != 1:
        raise OptionWallError("KOSPI200 index source must be one explicit non-null provider")
    prices = selected.rename(columns={"close": "underlying_price", "source": "underlying_source"})
    result = walls.drop(
        columns=["underlying_price", "underlying_dataset", "underlying_source", "underlying_pit_status"],
        errors="ignore",
    ).merge(prices, on="date", how="left", validate="many_to_one")
    if require_complete and result["underlying_price"].isna().any():
        missing = result.loc[result["underlying_price"].isna(), "date"].nunique()
        raise OptionWallError(f"same-date KOSPI200 join incomplete: missing_dates={missing}")
    result["underlying_dataset"] = dataset_name
    result["underlying_pit_status"] = pit_status
    return _compute_near_walls(
        compute_wall_distance(result, warning_policy=warning_policy)
    )


def compute_front_month_wall(walls: pd.DataFrame) -> pd.DataFrame:
    """Select the minimum retained maturity month not before each trade month."""
    required = {"date", "maturity_month"}
    if not required.issubset(walls.columns):
        raise OptionWallError("walls require date and maturity_month")
    frame = walls.copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.normalize()
    maturity = pd.to_datetime(frame["maturity_month"] + "-01", errors="coerce")
    trade_month = frame["date"].dt.to_period("M").dt.to_timestamp()
    eligible = frame.loc[maturity.ge(trade_month)].copy()
    if eligible.empty:
        return eligible.assign(front_selection_rule=pd.Series(dtype="string"))
    minimum = eligible.groupby("date")["maturity_month"].transform("min")
    selected = eligible.loc[eligible["maturity_month"].eq(minimum)].copy()
    if selected["date"].duplicated().any():
        raise OptionWallError("front maturity selection produced more than one row per date")
    selected["front_selection_rule"] = FRONT_SELECTION_RULE
    return selected.sort_values("date").reset_index(drop=True)


def get_option_wall_histogram(
    options: pd.DataFrame, date: object, maturity_month: str
) -> pd.DataFrame:
    """Return strike-level CALL/PUT OI and volume for a detail-page histogram."""
    frame = _prepare(options)
    target = pd.Timestamp(date).normalize()
    selected = frame.loc[
        frame["date"].eq(target) & frame["maturity_month"].eq(maturity_month),
        ["date", "maturity_month", "strike", "call_put", "open_interest", "volume", "source"],
    ]
    return selected.sort_values(["strike", "call_put"]).reset_index(drop=True)
