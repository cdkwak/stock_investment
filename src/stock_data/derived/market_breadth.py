import pandas as pd

from stock_data.contracts.kr_market import KR_MARKET_BREADTH_DAILY
from stock_data.validation.kr_equity import validate_equity_price
from stock_data.validation.kr_market import validate_market_breadth


def calculate_market_breadth(prices: pd.DataFrame, canonical_universe: pd.DataFrame | None = None) -> pd.DataFrame:
    validate_equity_price(prices)
    working = prices[["date", "market", "symbol", "close"]].copy()
    working["previous_close"] = working.groupby(["market", "symbol"], sort=False)["close"].shift(1)
    comparable = working.loc[working["previous_close"].notna()].copy()
    if canonical_universe is not None:
        membership = canonical_universe[["date", "market", "symbol"]].drop_duplicates()
        comparable = comparable.merge(membership, on=["date", "market", "symbol"], how="inner",
                                      validate="many_to_one")
    if comparable.empty:
        raise ValueError("at least two observations per symbol are required")
    comparable["advancing"] = comparable["close"].gt(comparable["previous_close"])
    comparable["declining"] = comparable["close"].lt(comparable["previous_close"])
    comparable["unchanged"] = comparable["close"].eq(comparable["previous_close"])
    result = comparable.groupby(["date", "market"], sort=True).agg(
        advancing=("advancing", "sum"), declining=("declining", "sum"),
        unchanged=("unchanged", "sum"), total=("symbol", "count"),
    ).reset_index()
    for column in ("advancing", "declining", "unchanged", "total"):
        result[column] = result[column].astype("int64")
    result = result[list(KR_MARKET_BREADTH_DAILY.column_names)]
    validate_market_breadth(result)
    return result
