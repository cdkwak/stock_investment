"""Strict parser for UR-187's one Cboe CDN delayed VIX payload."""
from __future__ import annotations
import math
from datetime import datetime, timezone
from typing import Any

class CboeVixPayloadError(ValueError): pass

def parse_payload(payload:object)->dict[str,object]:
    if not isinstance(payload,dict) or set(payload)!={"data"} or not isinstance(payload["data"],dict): raise CboeVixPayloadError("PAYLOAD_SCHEMA_UNBOUND")
    data:dict[str,Any]=payload["data"]
    symbol=data.get("symbol"); description=data.get("symbol_description")
    if symbol not in {"_VIX","VIX"} or not isinstance(description,str) or "Volatility Index" not in description: raise CboeVixPayloadError("IDENTITY_UNBOUND")
    try: value=float(data["last_trade_price"])
    except (KeyError,TypeError,ValueError): raise CboeVixPayloadError("INDEX_POINT_VALUE_UNBOUND") from None
    if not math.isfinite(value) or value<0: raise CboeVixPayloadError("INDEX_POINT_VALUE_INVALID")
    raw_time=data.get("last_trade_time")
    if not isinstance(raw_time,str): raise CboeVixPayloadError("PROVIDER_TIMESTAMP_UNBOUND")
    try: provider_at=datetime.fromisoformat(raw_time.replace("Z","+00:00"))
    except ValueError: raise CboeVixPayloadError("PROVIDER_TIMESTAMP_INVALID") from None
    if provider_at.tzinfo is None or provider_at.utcoffset() is None: raise CboeVixPayloadError("PROVIDER_TIMESTAMP_TIMEZONE_UNBOUND")
    delayed=data.get("is_delayed"); session=data.get("market_status")
    if delayed is not True: raise CboeVixPayloadError("DECLARED_DELAY_UNBOUND")
    if not isinstance(session,str) or not session: raise CboeVixPayloadError("SESSION_UNBOUND")
    return {"value":value,"provider_at":provider_at.astimezone(timezone.utc),"session":session,"delay":"DELAYED","symbol":symbol,"description":description}
