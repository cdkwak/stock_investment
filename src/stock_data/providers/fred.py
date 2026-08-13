from __future__ import annotations

from datetime import date
from io import StringIO

import numpy as np
import pandas as pd
import requests
from pathlib import Path

from stock_data.contracts.global_market import FRED_TREASURY_YIELD_DAILY, FRED_USD_FX_DAILY
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic
from stock_data.validation.global_market import validate_fred
from stock_data.providers.public_http_capture import capture_public_response


URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"


def fetch_series(
    series_id: str, start: date | None = None, *, end: date | None = None, session=requests,
    capture_root: Path | None = None,
) -> pd.DataFrame:
    params = {"id": series_id}
    if start is not None:
        params["cosd"] = start.isoformat()
    if end is not None:
        if start is not None and end < start:
            raise ValueError("FRED end must be on or after start")
        params["coed"] = end.isoformat()
    response = session.get(URL, params=params, headers={"User-Agent":"stock-investment-rev1/0.1"}, timeout=30)
    if capture_root is not None:
        capture_public_response(
            root=capture_root, provider="fred", operation="fredgraph_csv",
            request_url=URL, request_parameters=params, response=response,
        )
    response.raise_for_status()
    frame = pd.read_csv(StringIO(response.text))
    if frame.empty or len(frame.columns) != 2:
        raise RuntimeError(f"FRED {series_id} response schema is invalid")
    frame.columns = ["date", series_id.lower()]
    frame["date"] = pd.to_datetime(frame["date"], errors="raise").dt.strftime("%Y-%m-%d")
    frame[series_id.lower()] = pd.to_numeric(frame[series_id.lower()], errors="coerce")
    finite = frame[series_id.lower()].dropna().to_numpy(dtype="float64")
    if not np.isfinite(finite).all() or frame[series_id.lower()].notna().sum() == 0:
        raise RuntimeError(f"FRED {series_id} has no finite observations")
    if frame["date"].duplicated().any() or not frame["date"].is_monotonic_increasing:
        raise RuntimeError(f"FRED {series_id} dates are invalid")
    return frame


def fetch_dataset(
    series_ids: tuple[str, ...], start: date | None = None, *, end: date | None = None, session=requests,
    capture_root: Path | None = None,
) -> pd.DataFrame:
    frames = [fetch_series(
        series_id, start, end=end, session=session, capture_root=capture_root,
    ) for series_id in series_ids]
    result = frames[0]
    for frame in frames[1:]:
        result = result.merge(frame, on="date", how="outer", validate="one_to_one")
    return result.sort_values("date", kind="stable").reset_index(drop=True)


def collect_fred(
    root: Path, *, start: date | None = None,
    capture_root: Path | None = None, session=requests,
) -> dict[str, pd.DataFrame]:
    configs=((FRED_TREASURY_YIELD_DAILY,("DGS2","DGS10","DGS30")),(FRED_USD_FX_DAILY,("DEXKOUS","DEXJPUS")))
    results={}
    for contract,series in configs:
        path=root/contract.name
        existing=(
            read_dataset(path,contract,validate_fred)
            if path.exists() and any(path.rglob("data.parquet"))
            else None
        )
        request_start=start
        if existing is not None:
            overlap=(pd.Timestamp(existing.date.max())-pd.Timedelta(days=10)).date()
            request_start=max(start,overlap) if start else overlap
        incoming=fetch_dataset(
            series, request_start, session=session, capture_root=capture_root,
        )
        if existing is not None:
            incoming=pd.concat([existing.loc[~existing.date.isin(incoming.date)],incoming],ignore_index=True)
        incoming=incoming.sort_values("date",kind="stable").reset_index(drop=True)
        validate_fred(incoming); write_dataset_atomic(incoming,path,contract,validate_fred)
        results[contract.name]=incoming
    return results
