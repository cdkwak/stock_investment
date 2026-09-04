from __future__ import annotations

import pandas as pd

from stock_data.contracts.kr_equity_investor_flow import (
    KR_EQUITY_INVESTOR_FLOW_DAILY,
)


VALUE_COLUMNS = (
    "foreign_net",
    "institution_net",
    "individual_net",
    "other_corp_net",
    "total_net",
)


def validate_kr_equity_investor_flow(dataframe: pd.DataFrame) -> tuple[str, ...]:
    if list(dataframe.columns) != list(KR_EQUITY_INVESTOR_FLOW_DAILY.column_names):
        raise ValueError("Korean equity investor-flow columns differ")
    if dataframe.empty:
        raise ValueError("Korean equity investor-flow dataset is empty")
    if dataframe.duplicated(["date", "symbol"]).any():
        raise ValueError("duplicate Korean equity investor-flow key")
    if not dataframe["symbol"].astype(str).str.fullmatch(r"[0-9A-Z]{6}").all():
        raise ValueError("Korean equity investor-flow symbol is not a six-character KRX code")
    dates = pd.to_datetime(dataframe["date"], errors="coerce")
    captured = pd.to_datetime(dataframe["captured_at"], errors="coerce", utc=True)
    if dates.isna().any() or captured.isna().any():
        raise ValueError("Korean equity investor-flow date or capture time is invalid")
    if dataframe[["date", "symbol", "source", "captured_at"]].isna().any().any():
        raise ValueError("Korean equity investor-flow identity/provenance is incomplete")
    if not dataframe["source"].astype(str).eq("pykrx").all():
        raise ValueError("Korean equity investor-flow source differs")
    numeric = dataframe[list(VALUE_COLUMNS)].apply(pd.to_numeric, errors="coerce")
    if numeric.isna().any().any():
        raise ValueError("Korean equity investor-flow amount is missing or non-numeric")
    if any(not pd.api.types.is_integer_dtype(dataframe[column].dtype) for column in VALUE_COLUMNS):
        raise ValueError("Korean equity investor-flow amount must be int64")
    if any(dataframe[column].dtype.itemsize != 8 for column in VALUE_COLUMNS):
        raise ValueError("Korean equity investor-flow amount must be int64")

    component_sum = numeric[[
        "foreign_net", "institution_net", "individual_net", "other_corp_net",
    ]].sum(axis=1)
    residual = (component_sum - numeric["total_net"]).abs()
    tolerance = (numeric["total_net"].abs() * 0.01).clip(lower=1.0)
    warnings: list[str] = []
    for index in dataframe.index[residual > tolerance]:
        warnings.append(
            "INVESTOR_FLOW_SUM_MISMATCH:"
            f"{dataframe.at[index, 'date']}:{dataframe.at[index, 'symbol']}:"
            f"residual={int(residual.at[index])}:tolerance={float(tolerance.at[index]):.2f}"
        )
    return tuple(warnings)


__all__ = ["VALUE_COLUMNS", "validate_kr_equity_investor_flow"]
