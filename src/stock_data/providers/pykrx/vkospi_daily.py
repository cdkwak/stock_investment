from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from io import StringIO
import pandas as pd
from stock_data.providers.pykrx.kr_index_daily import _stock_module

TICKER="1300"; SYMBOL="VKOSPI"; KRX_SCREEN_OPERATION="dbms/MDC/STAT/standard/MDCSTAT01201"
class VKOSPIPilotError(RuntimeError): pass

def normalize_response(response: pd.DataFrame) -> pd.DataFrame:
    if response.empty: raise VKOSPIPilotError("VKOSPI pilot returned no rows")
    frame=response.reset_index()
    if len(frame.columns) != 8: raise VKOSPIPilotError(f"unexpected schema: {list(frame.columns)}")
    frame.columns=["date","open","high","low","close","volume","trading_value","market_cap"]
    frame["date"]=pd.to_datetime(frame.date,errors="raise").dt.strftime("%Y-%m-%d")
    frame.insert(1,"symbol",SYMBOL); frame.insert(2,"ticker",TICKER)
    frame["source"]="pykrx"; frame["source_operation"]="get_index_ohlcv"; frame["krx_screen_operation"]=KRX_SCREEN_OPERATION
    frame["date_semantics"]="KRX_TRADING_DATE_DAILY_FINAL_VALUE_PIT_UNRESOLVED"
    numeric=["open","high","low","close","volume","trading_value","market_cap"]
    if frame[numeric].apply(pd.to_numeric,errors="coerce").isna().any().any(): raise VKOSPIPilotError("null/non-numeric fields")
    return frame.sort_values("date").reset_index(drop=True)

def fetch_bounded_pilot(start: date,end: date,*,stock_module=None)->pd.DataFrame:
    if (end-start).days not in (2,3,4): raise VKOSPIPilotError("pilot must span 3-5 calendar days")
    stock=stock_module or _stock_module()
    try:
        with redirect_stdout(StringIO()),redirect_stderr(StringIO()): response=stock.get_index_ohlcv(start.strftime("%Y%m%d"),end.strftime("%Y%m%d"),TICKER)
    except Exception as error: raise VKOSPIPilotError(f"retry-zero pilot failed: {type(error).__name__}: {error}") from None
    return normalize_response(response)
