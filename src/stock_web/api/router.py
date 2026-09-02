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

from fastapi import APIRouter, Request
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


def json_response(payload: object, *, status_code: int = 200) -> Response:
    return Response(
        json.dumps(_jsonable(payload), ensure_ascii=False),
        status_code=status_code,
        media_type="application/json",
    )


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

    @router.get("/account")
    def account() -> Response:
        from stock_web.api.account_page import build_account_page_data

        return json_response(build_account_page_data(project_root))

    @router.get("/manual/accounts")
    def manual_accounts() -> Response:
        from stock_web.api.account_page import build_manual_account_data

        return json_response(build_manual_account_data(project_root))

    @router.post("/manual/accounts")
    async def save_manual(request: Request) -> Response:
        from stock_web.api.account_page import AccountInputError, save_manual_accounts

        if request.client is None or request.client.host not in {"127.0.0.1", "::1"}:
            return json_response({"error": "로컬 접속에서만 저장할 수 있습니다."}, status_code=403)
        try:
            payload = await request.json()
            saved = save_manual_accounts(project_root, payload)
        except (ValueError, AccountInputError) as error:
            return json_response({"error": str(error)}, status_code=400)
        from stock_web.api import home_data

        home_data._HOME_CACHE.pop(str(project_root.resolve()), None)
        return json_response(saved)

    @router.get("/net-worth")
    def net_worth() -> Response:
        from stock_web.api.account_page import build_net_worth_data

        return json_response(build_net_worth_data(project_root))

    @router.post("/net-worth")
    async def save_net_worth_snapshot(request: Request) -> Response:
        from stock_web.api.account_page import AccountInputError, save_net_worth

        if request.client is None or request.client.host not in {"127.0.0.1", "::1"}:
            return json_response({"error": "로컬 접속에서만 저장할 수 있습니다."}, status_code=403)
        try:
            payload = await request.json()
            saved = save_net_worth(project_root, payload)
        except (ValueError, AccountInputError) as error:
            return json_response({"error": str(error)}, status_code=400)
        from stock_web.api import home_data

        home_data._HOME_CACHE.pop(str(project_root.resolve()), None)
        return json_response(saved)

    @router.get("/ping")
    def ping() -> dict[str, str]:
        return {"status": "ok", "observed_at_utc": datetime.now(timezone.utc).isoformat()}

    return router
