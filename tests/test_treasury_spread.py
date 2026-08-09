import pandas as pd
from stock_data.derived.treasury_spread import calculate_treasury_spreads


def test_spreads_are_derived_without_filling_missing_values() -> None:
    source=pd.DataFrame({"date":["2026-08-03","2026-08-04"],"dgs2":[3.0,None],"dgs10":[4.0,4.1],"dgs30":[4.5,4.6]})
    result=calculate_treasury_spreads(source)
    assert result.spread_10y_2y.iloc[0]==1.0
    assert pd.isna(result.spread_10y_2y.iloc[1])
