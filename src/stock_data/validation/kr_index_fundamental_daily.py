from __future__ import annotations

import re

import numpy as np
import pandas as pd

from stock_data.contracts.kr_index_fundamental_daily import (
    KR_INDEX_FUNDAMENTAL_DAILY,
)


class IndexFundamentalValidationError(ValueError):
    pass


_IDENTITIES = {"1001": "KOSPI", "2001": "KOSDAQ"}
_SHA256 = re.compile(r"[0-9a-f]{64}")


def validate_kr_index_fundamental_daily(dataframe: pd.DataFrame) -> None:
    contract = KR_INDEX_FUNDAMENTAL_DAILY
    if list(dataframe.columns) != list(contract.column_names):
        raise IndexFundamentalValidationError(
            "kr_index_fundamental_daily column order or schema is invalid"
        )
    if dataframe.empty:
        raise IndexFundamentalValidationError(
            "kr_index_fundamental_daily must not be empty"
        )

    dates = pd.to_datetime(dataframe["date"], format="%Y-%m-%d", errors="coerce")
    if dates.isna().any() or not dates.dt.strftime("%Y-%m-%d").equals(
        dataframe["date"].astype(str)
    ):
        raise IndexFundamentalValidationError("date must use valid YYYY-MM-DD")

    codes = dataframe["index_code"].astype(str)
    if not codes.isin(_IDENTITIES).all():
        raise IndexFundamentalValidationError(
            "index_code must be KOSPI 1001 or KOSDAQ 2001"
        )
    expected_markets = codes.map(_IDENTITIES)
    if not dataframe["market"].astype(str).eq(expected_markets).all():
        raise IndexFundamentalValidationError("index_code and market are inconsistent")
    if not dataframe["source"].eq("KRX_MDCSTAT00702").all():
        raise IndexFundamentalValidationError("source must be KRX_MDCSTAT00702")
    if not dataframe["source_response_sha256"].astype(str).map(
        lambda value: bool(_SHA256.fullmatch(value))
    ).all():
        raise IndexFundamentalValidationError(
            "source_response_sha256 must be lowercase SHA-256"
        )
    if dataframe.duplicated(list(contract.primary_key)).any():
        raise IndexFundamentalValidationError(
            "duplicate or conflicting date+index_code rows"
        )

    numeric_columns = (
        "close", "weighted_per", "weighted_pbr", "dividend_yield"
    )
    numeric = dataframe[list(numeric_columns)].apply(pd.to_numeric, errors="coerce")
    for column in numeric_columns:
        invalid_conversion = dataframe[column].notna() & numeric[column].isna()
        if invalid_conversion.any():
            raise IndexFundamentalValidationError(f"{column} must be numeric or null")
        finite_values = numeric[column].dropna().to_numpy(dtype="float64")
        if not np.isfinite(finite_values).all():
            raise IndexFundamentalValidationError(f"{column} must be finite")
    if numeric["close"].isna().any() or numeric["close"].le(0).any():
        raise IndexFundamentalValidationError("close must be positive")
    for column in ("weighted_per", "weighted_pbr"):
        if numeric[column].dropna().le(0).any():
            raise IndexFundamentalValidationError(
                f"{column} must be positive when displayed"
            )
    if numeric["dividend_yield"].dropna().lt(0).any():
        raise IndexFundamentalValidationError(
            "dividend_yield must be nonnegative when displayed"
        )

    sorted_index = dataframe.sort_values(list(contract.sort_key), kind="stable").index
    if not sorted_index.equals(dataframe.index):
        raise IndexFundamentalValidationError(
            "rows must be sorted by date and index_code"
        )


__all__ = [
    "IndexFundamentalValidationError",
    "validate_kr_index_fundamental_daily",
]
