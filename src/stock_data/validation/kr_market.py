import numpy as np
import pandas as pd

from stock_data.contracts.kr_market import KR_INVESTOR_FLOW_DAILY, KR_MARKET_BREADTH_DAILY


def _base(dataframe: pd.DataFrame, contract) -> None:
    if list(dataframe.columns) != list(contract.column_names) or dataframe.empty:
        raise ValueError(f"{contract.name} schema is invalid or empty")
    dates = pd.to_datetime(dataframe["date"], format="%Y-%m-%d", errors="coerce")
    if dates.isna().any():
        raise ValueError("invalid date")
    if not dataframe["market"].isin({"KOSPI", "KOSDAQ"}).all():
        raise ValueError("invalid market")
    if dataframe.duplicated(list(contract.primary_key)).any():
        raise ValueError("duplicate primary key")
    if not dataframe.sort_values(list(contract.sort_key), kind="stable").index.equals(dataframe.index):
        raise ValueError("rows are not sorted")


def validate_investor_flow(dataframe: pd.DataFrame) -> None:
    _base(dataframe, KR_INVESTOR_FLOW_DAILY)
    columns = list(KR_INVESTOR_FLOW_DAILY.column_names[2:])
    numeric = dataframe[columns].apply(pd.to_numeric, errors="coerce")
    if numeric.isna().any().any() or not np.isfinite(numeric.to_numpy(dtype="float64")).all():
        raise ValueError("invalid investor flow numeric value")
    participant_sum = numeric[columns[:-1]].sum(axis=1)
    if not participant_sum.eq(numeric["total_net_buy"]).all():
        raise ValueError("investor participant sum differs from total")


def validate_market_breadth(dataframe: pd.DataFrame) -> None:
    _base(dataframe, KR_MARKET_BREADTH_DAILY)
    columns = ["advancing", "declining", "unchanged", "total"]
    numeric = dataframe[columns].apply(pd.to_numeric, errors="coerce")
    if numeric.isna().any().any() or (numeric < 0).any().any():
        raise ValueError("invalid breadth count")
    if not numeric[["advancing", "declining", "unchanged"]].sum(axis=1).eq(numeric["total"]).all():
        raise ValueError("breadth components differ from total")
