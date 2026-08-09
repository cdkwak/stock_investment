import pandas as pd


def calculate_treasury_spreads(yields: pd.DataFrame) -> pd.DataFrame:
    required=["date","dgs2","dgs10","dgs30"]
    if list(yields.columns)!=required or yields.empty or yields.date.duplicated().any():
        raise ValueError("invalid treasury yield source")
    result=pd.DataFrame({"date":yields.date.astype(str)})
    result["spread_10y_2y"]=yields.dgs10-yields.dgs2
    result["spread_30y_2y"]=yields.dgs30-yields.dgs2
    return result
