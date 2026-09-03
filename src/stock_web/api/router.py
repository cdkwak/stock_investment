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

    def loopback(request: Request) -> bool:
        """True only for a direct connection from this machine.

        Requests relayed by `tailscale serve` arrive from 127.0.0.1 but carry forwarding
        headers; those are remote users and must not unlock the write endpoints.
        """
        if request.client is None or request.client.host not in {"127.0.0.1", "::1"}:
            return False
        headers = request.headers
        return not any(
            name in headers
            for name in ("x-forwarded-for", "tailscale-user-login", "tailscale-user-name")
        )

    def clear_home_cache() -> None:
        from stock_web.api import home_data

        home_data._HOME_CACHE.pop(str(project_root.resolve()), None)

    @router.get("/home")
    def home() -> Response:
        from stock_web.api import home_data

        return json_response(home_data.build_home_payload(project_root))

    @router.get("/chart")
    def chart(symbol: str = "KOSPI", range: str = "6M") -> Response:
        from stock_web.api import home_data

        return json_response(home_data.build_chart_payload(project_root, symbol=symbol, range_key=range))

    @router.get("/market")
    def market() -> Response:
        from stock_web.api.market_page import build_market_page_payload

        return json_response(build_market_page_payload(project_root))

    @router.get("/market/chart")
    def market_chart(
        symbol: str = "KOSPI", interval: str = "1d", range: str = "6M",
        indicators: str = "ma5,ma20,ma60,ma120,volume",
    ) -> Response:
        from stock_web.api.market_page import build_market_chart_payload

        return json_response(build_market_chart_payload(
            project_root, symbol=symbol, interval=interval,
            range_key=range, indicators=indicators,
        ))

    @router.get("/account")
    def account() -> Response:
        from stock_web.api.account_page import build_account_page_data

        return json_response(build_account_page_data(project_root))

    @router.get("/cash-flows")
    def cash_flows() -> Response:
        from stock_web.api.account_page import AccountInputError, build_cash_flow_data

        try:
            return json_response(build_cash_flow_data(project_root))
        except AccountInputError as error:
            return json_response({"error": str(error)}, status_code=400)

    @router.post("/cash-flows")
    async def save_cash_flow_entry(request: Request) -> Response:
        from stock_web.api.account_page import AccountInputError, save_cash_flow

        if not loopback(request):
            return json_response({"error": "로컬 접속에서만 저장할 수 있습니다."}, status_code=403)
        try:
            saved = save_cash_flow(project_root, await request.json())
        except (ValueError, AccountInputError) as error:
            return json_response({"error": str(error)}, status_code=400)
        clear_home_cache()
        return json_response(saved)

    @router.delete("/cash-flows")
    async def delete_cash_flow_entry(request: Request) -> Response:
        from stock_web.api.account_page import AccountInputError, delete_cash_flow

        if not loopback(request):
            return json_response({"error": "로컬 접속에서만 저장할 수 있습니다."}, status_code=403)
        try:
            saved = delete_cash_flow(project_root, await request.json())
        except (ValueError, AccountInputError) as error:
            return json_response({"error": str(error)}, status_code=400)
        clear_home_cache()
        return json_response(saved)

    @router.get("/trade-journal")
    def trade_journal(days: int = 60) -> Response:
        from stock_web.api.account_page import AccountInputError
        from stock_web.api.trade_journal import build_trade_journal

        try:
            return json_response(build_trade_journal(project_root, days=days))
        except AccountInputError as error:
            return json_response({"error": str(error)}, status_code=400)

    @router.post("/trade-journal/manual")
    async def save_manual_trade_journal_entry(request: Request) -> Response:
        from stock_web.api.account_page import AccountInputError
        from stock_web.api.trade_journal import build_trade_journal, save_manual_entry

        if not loopback(request):
            return json_response({"error": "로컬 접속에서만 저장할 수 있습니다."}, status_code=403)
        try:
            save_manual_entry(project_root, await request.json())
            return json_response(build_trade_journal(project_root))
        except (ValueError, AccountInputError) as error:
            return json_response({"error": str(error)}, status_code=400)

    @router.delete("/trade-journal/manual")
    async def delete_manual_trade_journal_entry(request: Request) -> Response:
        from stock_web.api.account_page import AccountInputError
        from stock_web.api.trade_journal import build_trade_journal, delete_manual_entry

        if not loopback(request):
            return json_response({"error": "로컬 접속에서만 저장할 수 있습니다."}, status_code=403)
        try:
            delete_manual_entry(project_root, await request.json())
            return json_response(build_trade_journal(project_root))
        except (ValueError, AccountInputError) as error:
            return json_response({"error": str(error)}, status_code=400)

    @router.get("/stocks")
    def stocks() -> Response:
        from stock_web.api.stocks_page import build_stocks_page_data

        return json_response(build_stocks_page_data(project_root))

    @router.get("/stocks/search")
    def stock_search(q: str = "") -> Response:
        from stock_web.api.stocks_page import search_stocks

        return json_response(search_stocks(project_root, q))

    @router.get("/stock-detail")
    def stock_detail(symbol: str, market: str = "") -> Response:
        from stock_web.api.stock_detail import build_stock_detail_payload

        try:
            return json_response(build_stock_detail_payload(
                project_root, symbol=symbol, market=market,
            ))
        except ValueError as error:
            return json_response({"error": str(error)}, status_code=400)

    @router.get("/stock-sparklines")
    def stock_sparklines(symbols: str = "") -> Response:
        from stock_web.api.stock_detail import build_stock_sparklines

        try:
            return json_response(build_stock_sparklines(project_root, symbols=symbols))
        except ValueError as error:
            return json_response({"error": str(error)}, status_code=400)

    @router.get("/scanner")
    def scanner(
        min_value: float = 1_000_000_000.0,
        min_cap: float = 100_000_000_000.0,
        all: int = 0,
    ) -> Response:
        from stock_web.api.scanner import build_scanner

        try:
            result = build_scanner(
                project_root,
                avg_value_20d_min=min_value,
                market_cap_min=min_cap,
                apply_liquidity_filter=all != 1,
            )
        except ValueError as error:
            return json_response({"error": str(error)}, status_code=400)
        return json_response(result)

    @router.post("/watchlists")
    async def save_watchlist(request: Request) -> Response:
        from stock_web.api.stocks_page import StocksInputError, mutate_watchlist

        if not loopback(request):
            return json_response({"error": "로컬 접속에서만 저장할 수 있습니다."}, status_code=403)
        try:
            saved = mutate_watchlist(project_root, await request.json())
        except (KeyError, ValueError, StocksInputError) as error:
            return json_response({"error": str(error)}, status_code=400)
        clear_home_cache()
        return json_response(saved)

    @router.post("/watchlist/items")
    async def add_watchlist(request: Request) -> Response:
        from stock_web.api.stocks_page import StocksInputError, add_watchlist_item

        if not loopback(request):
            return json_response({"error": "로컬 접속에서만 저장할 수 있습니다."}, status_code=403)
        try:
            saved = add_watchlist_item(project_root, await request.json())
        except (KeyError, ValueError, StocksInputError) as error:
            return json_response({"error": str(error)}, status_code=400)
        clear_home_cache()
        return json_response(saved)

    @router.delete("/watchlist/items")
    async def delete_watchlist(request: Request) -> Response:
        from stock_web.api.stocks_page import StocksInputError, remove_watchlist_item

        if not loopback(request):
            return json_response({"error": "로컬 접속에서만 저장할 수 있습니다."}, status_code=403)
        try:
            saved = remove_watchlist_item(project_root, await request.json())
        except (KeyError, ValueError, StocksInputError) as error:
            return json_response({"error": str(error)}, status_code=400)
        clear_home_cache()
        return json_response(saved)

    @router.post("/watchlist/items/move")
    async def reorder_watchlist(request: Request) -> Response:
        from stock_web.api.stocks_page import StocksInputError, move_watchlist_item

        if not loopback(request):
            return json_response({"error": "로컬 접속에서만 저장할 수 있습니다."}, status_code=403)
        try:
            saved = move_watchlist_item(project_root, await request.json())
        except (KeyError, ValueError, StocksInputError) as error:
            return json_response({"error": str(error)}, status_code=400)
        clear_home_cache()
        return json_response(saved)

    @router.post("/watch-conditions")
    async def watch_conditions(request: Request) -> Response:
        from stock_web.api.stocks_page import StocksInputError, save_conditions

        if not loopback(request):
            return json_response({"error": "로컬 접속에서만 저장할 수 있습니다."}, status_code=403)
        try:
            saved = save_conditions(project_root, await request.json())
        except (ValueError, StocksInputError) as error:
            return json_response({"error": str(error)}, status_code=400)
        clear_home_cache()
        return json_response(saved)

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
        clear_home_cache()
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
        clear_home_cache()
        return json_response(saved)

    @router.get("/ping")
    def ping() -> dict[str, str]:
        return {"status": "ok", "observed_at_utc": datetime.now(timezone.utc).isoformat()}

    return router
