from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
from typing import Mapping
import pandas as pd

BUSINESS_URL = "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
BUSINESS_BLD = "dbms/MDC/STAT/standard/MDCSTAT01201"
OFFICIAL_CODE = "1300"
REQUEST_FIELDS = {
    "bld": BUSINESS_BLD, "locale": "ko_KR", "indTpCd": "1", "idxIndCd": "300",
    "share": "1", "money": "1", "csvxls_isNo": "false",
}
REQUIRED_SOURCE_FIELDS = (
    "TRD_DD", "CLSPRC_IDX", "PRV_DD_CMPR", "UPDN_RATE",
    "OPNPRC_IDX", "HGPRC_IDX", "LWPRC_IDX", "FLUC_TP_CD",
)

class VKOSPISourceError(RuntimeError): pass

@dataclass(frozen=True)
class VKOSPIPilotResult:
    rows: tuple[dict[str, object], ...]
    source_fields: tuple[str, ...]
    current_datetime: str | None

def request_payload(start: str, end: str) -> dict[str, str]:
    for value in (start, end): datetime.strptime(value, "%Y%m%d")
    if start > end: raise ValueError("start must not exceed end")
    return {**REQUEST_FIELDS, "strtDd": start, "endDd": end}

def _decimal(value: object, field: str) -> Decimal:
    try: return Decimal(str(value).replace(",", "").replace("+", "").strip())
    except InvalidOperation as error: raise VKOSPISourceError(f"INVALID_DECIMAL:{field}") from error

def parse_pilot_body(body: bytes, *, expected_closes: Mapping[str, str]) -> VKOSPIPilotResult:
    if body.lstrip().startswith(b"<"): raise VKOSPISourceError("HTML_OR_BLOCK_PAGE")
    try: payload=json.loads(body)
    except (UnicodeDecodeError,json.JSONDecodeError) as error: raise VKOSPISourceError("NON_JSON_RESPONSE") from error
    if not isinstance(payload,dict) or payload.get("_error_code") or payload.get("error"): raise VKOSPISourceError("SOURCE_ERROR_PAYLOAD")
    rows=payload.get("output")
    if not isinstance(rows,list) or not rows: raise VKOSPISourceError("OUTPUT_MISSING_OR_EMPTY")
    parsed=[]; fields=set()
    for index,row in enumerate(rows):
        if not isinstance(row,dict): raise VKOSPISourceError(f"INVALID_ROW:{index}")
        fields.update(row)
        missing=set(REQUIRED_SOURCE_FIELDS)-set(row)
        if missing: raise VKOSPISourceError(f"SCHEMA_MISSING:{','.join(sorted(missing))}")
        date=datetime.strptime(str(row["TRD_DD"]).strip(),"%Y/%m/%d").strftime("%Y-%m-%d")
        close=_decimal(row["CLSPRC_IDX"],"CLSPRC_IDX")
        parsed.append({"date":date,"close":str(close),"source_row":row})
    actual={item["date"]:item["close"] for item in parsed}
    expected={key:str(Decimal(value)) for key,value in expected_closes.items()}
    if actual != expected: raise VKOSPISourceError(f"EXACT_CLOSE_MISMATCH:actual={actual}:expected={expected}")
    return VKOSPIPilotResult(tuple(parsed),tuple(sorted(fields)),payload.get("CURRENT_DATETIME"))

def parse_history_body(body: bytes) -> tuple[list[dict[str,object]],str|None,tuple[str,...]]:
    if body.lstrip().startswith(b"<"): raise VKOSPISourceError("HTML_OR_BLOCK_PAGE")
    try: payload=json.loads(body)
    except (UnicodeDecodeError,json.JSONDecodeError) as error: raise VKOSPISourceError("NON_JSON_RESPONSE") from error
    if not isinstance(payload,dict) or payload.get("_error_code") or payload.get("error"): raise VKOSPISourceError("SOURCE_ERROR_PAYLOAD")
    rows=payload.get("output")
    if not isinstance(rows,list) or not rows: raise VKOSPISourceError("OUTPUT_MISSING_OR_EMPTY")
    fields=set()
    for index,row in enumerate(rows):
        if not isinstance(row,dict): raise VKOSPISourceError(f"INVALID_ROW:{index}")
        fields.update(row); missing=set(REQUIRED_SOURCE_FIELDS)-set(row)
        if missing: raise VKOSPISourceError(f"SCHEMA_MISSING:{','.join(sorted(missing))}")
        datetime.strptime(str(row["TRD_DD"]),"%Y/%m/%d")
        _decimal(row["CLSPRC_IDX"],"CLSPRC_IDX")
        for field in ("PRV_DD_CMPR","UPDN_RATE"):
            if str(row[field]).strip(): _decimal(row[field],field)
    dates=[row["TRD_DD"] for row in rows]
    if len(dates)!=len(set(dates)): raise VKOSPISourceError("DUPLICATE_DATE")
    return rows,payload.get("CURRENT_DATETIME"),tuple(sorted(fields))

def frames_from_history(rows,*,collected_at:str,landing_reference:str,response_sha256:str):
    raw=[]; normalized=[]
    provenance={"source":"KRX","source_dataset":"코스피 200 변동성지수","source_code":"1300","source_operation":"MDCSTAT01201","collected_at":collected_at,"landing_reference":landing_reference,"response_sha256":response_sha256,"validation_status":"VALIDATED_SOURCE_SCHEMA"}
    for row in rows:
        market_date=datetime.strptime(str(row["TRD_DD"]),"%Y/%m/%d").strftime("%Y-%m-%d")
        raw.append({"market_date":market_date,**{field:str(row[field]) for field in REQUIRED_SOURCE_FIELDS},**provenance})
        optional=lambda field: None if str(row[field]).strip()=="" else float(_decimal(row[field],field))
        normalized.append({"market_date":market_date,"open":optional("OPNPRC_IDX"),"high":optional("HGPRC_IDX"),"low":optional("LWPRC_IDX"),"close":float(_decimal(row["CLSPRC_IDX"],"CLSPRC_IDX")),"change":optional("PRV_DD_CMPR"),"change_pct":optional("UPDN_RATE"),"fluctuation_type":None if str(row["FLUC_TP_CD"]).strip()=="" else str(row["FLUC_TP_CD"]),**provenance,"pit_status":"PIT_LIMITED_PUBLICATION_REVISION_UNRESOLVED"})
    from stock_data.contracts.vkospi_daily import KR_VKOSPI_DAILY,KR_VKOSPI_RAW_DAILY
    raw_frame=pd.DataFrame(raw)[list(KR_VKOSPI_RAW_DAILY.column_names)].sort_values("market_date").reset_index(drop=True); normalized_frame=pd.DataFrame(normalized)[list(KR_VKOSPI_DAILY.column_names)].sort_values("market_date").reset_index(drop=True)
    complete=normalized_frame[["open","high","low"]].notna().all(axis=1)
    if not ((normalized_frame.loc[complete,"high"]>=normalized_frame.loc[complete,["open","low","close"]].max(axis=1)) & (normalized_frame.loc[complete,"low"]<=normalized_frame.loc[complete,["open","high","close"]].min(axis=1))).all(): raise VKOSPISourceError("OHLC_RELATIONSHIP_INVALID")
    return raw_frame,normalized_frame
