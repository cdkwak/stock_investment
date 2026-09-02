"""JSON API for the home page.

Every section is optional: when a retained dataset is missing, stale, or its
semantics are unverified, the section is omitted or carries ``status`` and the
page renders "표시 불가" instead of a fallback number.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import json
import math

from fastapi import APIRouter
from fastapi.responses import Response


def _jsonable(value: object) -> object:
    """Deep-convert numpy/pandas scalars so the payload never depends on the caller."""
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return None if (math.isnan(value) or math.isinf(value)) else value
    if hasattr(value, "item"):
        try:
            return _jsonable(value.item())
        except Exception:
            return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def json_response(payload: object) -> Response:
    return Response(json.dumps(_jsonable(payload), ensure_ascii=False), media_type="application/json")


def build_router(project_root: Path) -> APIRouter:
    router = APIRouter()

    @router.get("/home")
    def home() -> Response:
        from stock_web.api import home_data

        return json_response(home_data.build_home_payload(project_root))

    @router.get("/chart")
    def chart(symbol: str = "KOSPI", range: str = "6M") -> Response:
        from stock_web.api import home_data

        return json_response(home_data.build_chart_payload(project_root, symbol=symbol, range_key=range))

    @router.get("/ping")
    def ping() -> dict[str, str]:
        return {"status": "ok", "observed_at_utc": datetime.now(timezone.utc).isoformat()}

    return router
