from __future__ import annotations

import numpy as np
import pandas as pd

from stock_data.contracts.legacy_market_investor import (
    A001_START_DATE,
    C004_END_DATE,
    C004_START_DATE,
    KR_MARKET_INVESTOR_NET_PURCHASE_DAILY,
)


NUMERIC_COLUMNS = (
    "institution_net_buy",
    "other_corporation_net_buy",
    "individual_net_buy",
    "foreign_net_buy",
    "total_net_buy",
)


def validate_legacy_market_investor_net_purchase(dataframe: pd.DataFrame) -> None:
    """Validate the intentionally separate pre-A001 legacy import contract."""
    contract = KR_MARKET_INVESTOR_NET_PURCHASE_DAILY
    if list(dataframe.columns) != list(contract.column_names) or dataframe.empty:
        raise ValueError(f"{contract.name} schema is invalid or empty")
    dates = pd.to_datetime(dataframe["date"], format="%Y-%m-%d", errors="coerce")
    if dates.isna().any():
        raise ValueError("invalid date")
    date_text = dates.dt.strftime("%Y-%m-%d")
    if not date_text.lt(A001_START_DATE).all():
        raise ValueError("date crosses the A001 provider boundary")
    if not date_text.between(C004_START_DATE, C004_END_DATE).all():
        raise ValueError("date is outside C004 legacy scope")
    if not dataframe["market"].eq("KOSPI").all():
        raise ValueError("legacy import market must be KOSPI")
    if dataframe.duplicated(list(contract.primary_key)).any():
        raise ValueError("duplicate primary key")
    if not dataframe.sort_values(list(contract.sort_key), kind="stable").index.equals(dataframe.index):
        raise ValueError("rows are not sorted")
    if not dataframe["source"].eq("legacy_stock_investment_pykrx_1.2.8").all():
        raise ValueError("invalid legacy source identity")
    if not dataframe["source_operation"].eq("MDCSTAT02202").all():
        raise ValueError("invalid legacy source operation")
    if not dataframe["provider_boundary"].eq("legacy_pre_a001_only").all():
        raise ValueError("invalid provider boundary")
    numeric = dataframe[list(NUMERIC_COLUMNS)].apply(pd.to_numeric, errors="coerce")
    if numeric.isna().any().any() or not np.isfinite(numeric.to_numpy(dtype="float64")).all():
        raise ValueError("invalid investor net-purchase numeric value")
    if not numeric.apply(lambda column: column.mod(1).eq(0).all()).all():
        raise ValueError("investor net-purchase values must be integral")
    components = numeric[list(NUMERIC_COLUMNS[:-1])].sum(axis=1)
    if not components.eq(numeric["total_net_buy"]).all():
        raise ValueError("investor category sum differs from total")
