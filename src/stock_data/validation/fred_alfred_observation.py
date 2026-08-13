from __future__ import annotations

import re
from datetime import date

import numpy as np
import pandas as pd

from stock_data.contracts.fred_alfred_observation import (
    FRED_ALFRED_SERIES_SOURCE_OBSERVATION as CONTRACT,
)


RETAINED_SERIES = frozenset({"DGS2", "DGS10", "DGS30", "DEXKOUS", "DEXJPUS"})


def validate_fred_alfred_source_observation(frame: pd.DataFrame) -> None:
    if list(frame.columns) != list(CONTRACT.column_names) or frame.empty:
        raise ValueError("FRED/ALFRED source-observation schema is invalid or empty")
    if frame.duplicated(list(CONTRACT.primary_key)).any():
        raise ValueError("FRED/ALFRED capture row key is duplicated")
    if not frame["series_id"].isin(RETAINED_SERIES).all():
        raise ValueError("unapproved FRED series identity")
    try:
        observation = frame["observation_date"].map(lambda value: date.fromisoformat(str(value)[:10]))
        start = frame["realtime_start"].map(lambda value: date.fromisoformat(str(value)[:10]))
        end = frame["realtime_end"].map(
            lambda value: date.max if pd.isna(value) else date.fromisoformat(str(value)[:10])
        )
    except ValueError as error:
        raise ValueError("FRED/ALFRED date field is invalid") from error
    if (start > end).any():
        raise ValueError("real-time validity interval is inverted")
    open_ended = frame["source_realtime_end"].eq("9999-12-31")
    if not (open_ended == frame["realtime_end"].isna()).all():
        raise ValueError("source open-ended token and normalized end differ")
    closed = frame.loc[~open_ended]
    if not closed.empty and not (
        closed["source_realtime_end"].str[:10]
        == closed["realtime_end"].astype(str).str[:10]
    ).all():
        raise ValueError("source and normalized real-time end differ")
    numeric = pd.to_numeric(frame["value"], errors="coerce").to_numpy(dtype="float64")
    if not np.isfinite(numeric[~np.isnan(numeric)]).all():
        raise ValueError("observation value is not finite")
    source_missing = frame["source_value"].eq(".")
    if not (source_missing == frame["value"].isna()).all():
        raise ValueError("source missing token and normalized null differ")
    nonmissing = frame.loc[~source_missing]
    parsed = pd.to_numeric(nonmissing["source_value"], errors="coerce")
    if parsed.isna().any() or not np.array_equal(
        parsed.to_numpy(dtype="float64"), nonmissing["value"].to_numpy(dtype="float64")
    ):
        raise ValueError("source token and normalized value differ")
    if not frame["source_output_type"].eq(1).all():
        raise ValueError("only standard real-time-period rows are approved")
    if not frame["availability_precision"].eq("source_date_only").all():
        raise ValueError("availability precision must remain date-only")
    if not frame["landing_response_sha256"].map(lambda value: bool(re.fullmatch(r"[0-9a-f]{64}", str(value)))).all():
        raise ValueError("Landing response digest is invalid")
    if frame["source_row_ordinal"].lt(0).any():
        raise ValueError("source row ordinal is negative")
    if observation.isna().any():
        raise ValueError("observation date is invalid")
