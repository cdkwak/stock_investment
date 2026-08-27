import pandas as pd
from stock_data.contracts.vkospi_daily import KR_VKOSPI_DAILY,KR_VKOSPI_RAW_DAILY

def _common(frame,contract):
    if frame.empty or list(frame.columns)!=list(contract.column_names): raise ValueError("VKOSPI schema/content invalid")
    dates=pd.to_datetime(frame.market_date,format="%Y-%m-%d",errors="coerce")
    if dates.isna().any() or frame.duplicated("market_date").any() or not dates.is_monotonic_increasing: raise ValueError("VKOSPI date key invalid")
    expected={"source":"KRX","source_dataset":"코스피 200 변동성지수","source_code":"1300","source_operation":"MDCSTAT01201","validation_status":"VALIDATED_SOURCE_SCHEMA"}
    for key,value in expected.items():
        if not frame[key].astype(str).eq(value).all(): raise ValueError(f"VKOSPI {key} invalid")
    if frame.response_sha256.astype(str).str.fullmatch(r"[0-9a-f]{64}").ne(True).any(): raise ValueError("VKOSPI response hash invalid")

def validate_vkospi_raw_daily(frame):
    _common(frame,KR_VKOSPI_RAW_DAILY)
    for field in ("TRD_DD","CLSPRC_IDX","PRV_DD_CMPR","UPDN_RATE","OPNPRC_IDX","HGPRC_IDX","LWPRC_IDX","FLUC_TP_CD"):
        if frame[field].isna().any(): raise ValueError(f"VKOSPI Raw {field} null")

def validate_vkospi_daily(frame):
    _common(frame,KR_VKOSPI_DAILY)
    numeric=frame[["open","high","low","close","change","change_pct"]].apply(pd.to_numeric,errors="coerce")
    if numeric[["close"]].isna().any().any(): raise ValueError("VKOSPI close invalid")
    baseline=frame.market_date.eq(frame.market_date.min())
    if numeric.loc[~baseline,["change","change_pct"]].isna().any().any(): raise ValueError("VKOSPI non-baseline change invalid")
    if not numeric[["open","high","low"]].isna().all(axis=1).eq(numeric[["open","high","low"]].isna().any(axis=1)).all(): raise ValueError("VKOSPI partial OHLC null")
    complete=numeric[["open","high","low"]].notna().all(axis=1)
    if not ((numeric.loc[complete,"high"]>=numeric.loc[complete,["open","low","close"]].max(axis=1)) & (numeric.loc[complete,"low"]<=numeric.loc[complete,["open","high","close"]].min(axis=1))).all(): raise ValueError("VKOSPI OHLC invalid")
    if not frame.pit_status.eq("PIT_LIMITED_PUBLICATION_REVISION_UNRESOLVED").all(): raise ValueError("VKOSPI PIT status invalid")
