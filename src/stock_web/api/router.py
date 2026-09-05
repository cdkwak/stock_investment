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
from fastapi.routing import APIRoute


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


def _guest_mode_blocked(method: str, path: str) -> bool:
    if method.upper() in {"POST", "DELETE"}:
        return True
    return (
        path in {"/api/account", "/api/cash-flows", "/api/net-worth", "/api/manual"}
        or path.startswith("/api/trade-journal")
        or path.startswith("/api/manual/")
        or path.startswith("/api/research/compound")
        or path.startswith("/api/research/crisis-overlay")
    )


def build_router(project_root: Path, *, public_mode: bool = False) -> APIRouter:
    class GuestGuardRoute(APIRoute):
        def get_route_handler(self):
            original = super().get_route_handler()

            async def guarded(request: Request) -> Response:
                if public_mode and _guest_mode_blocked(request.method, request.url.path):
                    return json_response({"error": "guest mode"}, status_code=404)
                return await original(request)

            return guarded

    router = APIRouter(route_class=GuestGuardRoute)

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

    def write_response(
        request: Request, *, path: str, payload: object, status_code: int,
        error_code: str, row_counts: dict[str, int] | None = None,
    ) -> Response:
        from stock_web.api.account_page import append_web_write_audit

        append_web_write_audit(
            project_root,
            path=path,
            client_kind="loopback" if loopback(request) else "relayed",
            status=status_code,
            error_code=error_code,
            row_counts=row_counts,
        )
        return json_response(payload, status_code=status_code)

    def watchlist_counts(payload: object) -> dict[str, int]:
        if not isinstance(payload, dict):
            return {}
        lists = payload.get("lists")
        if not isinstance(lists, list):
            return {}
        return {
            "lists": len(lists),
            "items": sum(
                len(item.get("items", []))
                for item in lists if isinstance(item, dict) and isinstance(item.get("items"), list)
            ),
        }

    def request_client_key(request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",", 1)[0].strip() or "relayed"
        return str(request.client.host) if request.client is not None else "in-process"

    def clear_home_cache() -> None:
        from stock_web.api import home_data

        home_data.clear_home_cache(project_root, public_mode=public_mode)

    @router.get("/home")
    def home() -> Response:
        from stock_web.api import home_data

        return json_response(home_data.build_home_payload(project_root, public_mode=public_mode))

    @router.get("/changes")
    def changes() -> Response:
        from stock_web.api.changes import build_changes

        return json_response(build_changes(project_root, public_mode=public_mode))

    @router.get("/chart")
    def chart(
        symbol: str = "KOSPI", range: str = "6M", interval: str = "day",
        indicators: str | None = None,
    ) -> Response:
        from stock_web.api import home_data

        try:
            # Keep the home chart's legacy call shape stable when the stock-only
            # interval and indicator parameters are absent.
            if indicators is None and interval == "day":
                payload = home_data.build_chart_payload(
                    project_root, symbol=symbol, range_key=range,
                )
            else:
                payload = home_data.build_chart_payload(
                    project_root, symbol=symbol, range_key=range,
                    interval=interval, indicators=indicators,
                )
        except home_data.ChartRequestError as error:
            return json_response({"error": str(error)}, status_code=400)
        return json_response(payload)

    @router.get("/market")
    def market(flows_range: str = "60D") -> Response:
        from stock_web.api.market_page import build_market_page_payload

        return json_response(build_market_page_payload(project_root, flows_range=flows_range))

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
            return write_response(
                request, path="/api/cash-flows", payload={"error": "로컬 접속에서만 저장할 수 있습니다."},
                status_code=403, error_code="LOCAL_ONLY",
            )
        try:
            saved = save_cash_flow(project_root, await request.json())
        except (ValueError, AccountInputError) as error:
            return write_response(
                request, path="/api/cash-flows", payload={"error": str(error)},
                status_code=400, error_code="VALIDATION_ERROR",
            )
        clear_home_cache()
        return write_response(
            request, path="/api/cash-flows", payload=saved, status_code=200,
            error_code="OK", row_counts={"entries": len(saved.get("entries", []))},
        )

    @router.delete("/cash-flows")
    async def delete_cash_flow_entry(request: Request) -> Response:
        from stock_web.api.account_page import AccountInputError, delete_cash_flow

        if not loopback(request):
            return write_response(
                request, path="/api/cash-flows", payload={"error": "로컬 접속에서만 저장할 수 있습니다."},
                status_code=403, error_code="LOCAL_ONLY",
            )
        try:
            saved = delete_cash_flow(project_root, await request.json())
        except (ValueError, AccountInputError) as error:
            return write_response(
                request, path="/api/cash-flows", payload={"error": str(error)},
                status_code=400, error_code="VALIDATION_ERROR",
            )
        clear_home_cache()
        return write_response(
            request, path="/api/cash-flows", payload=saved, status_code=200,
            error_code="OK", row_counts={"entries": len(saved.get("entries", []))},
        )

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
            return write_response(
                request, path="/api/trade-journal/manual", payload={"error": "로컬 접속에서만 저장할 수 있습니다."},
                status_code=403, error_code="LOCAL_ONLY",
            )
        try:
            ledger = save_manual_entry(project_root, await request.json())
            saved = build_trade_journal(project_root)
        except (ValueError, AccountInputError) as error:
            return write_response(
                request, path="/api/trade-journal/manual", payload={"error": str(error)},
                status_code=400, error_code="VALIDATION_ERROR",
            )
        return write_response(
            request, path="/api/trade-journal/manual", payload=saved, status_code=200,
            error_code="OK", row_counts={"entries": len(ledger.get("entries", []))},
        )

    @router.post("/journal/note")
    async def save_journal_note(request: Request) -> Response:
        from stock_web.api.home_cards import JournalNoteError, append_journal_note

        if not loopback(request):
            return write_response(
                request, path="/api/journal/note", payload={"error": "로컬 접속에서만 저장할 수 있습니다."},
                status_code=403, error_code="LOCAL_ONLY",
            )
        try:
            saved = append_journal_note(project_root, await request.json())
        except JournalNoteError as error:
            return write_response(
                request, path="/api/journal/note", payload={"error": str(error)},
                status_code=400, error_code="VALIDATION_ERROR",
            )
        return write_response(
            request, path="/api/journal/note", payload=saved, status_code=200,
            error_code="OK", row_counts={"notes": 1},
        )

    @router.delete("/trade-journal/manual")
    async def delete_manual_trade_journal_entry(request: Request) -> Response:
        from stock_web.api.account_page import AccountInputError
        from stock_web.api.trade_journal import build_trade_journal, delete_manual_entry

        if not loopback(request):
            return write_response(
                request, path="/api/trade-journal/manual", payload={"error": "로컬 접속에서만 저장할 수 있습니다."},
                status_code=403, error_code="LOCAL_ONLY",
            )
        try:
            ledger = delete_manual_entry(project_root, await request.json())
            saved = build_trade_journal(project_root)
        except (ValueError, AccountInputError) as error:
            return write_response(
                request, path="/api/trade-journal/manual", payload={"error": str(error)},
                status_code=400, error_code="VALIDATION_ERROR",
            )
        return write_response(
            request, path="/api/trade-journal/manual", payload=saved, status_code=200,
            error_code="OK", row_counts={"entries": len(ledger.get("entries", []))},
        )

    @router.get("/stocks")
    def stocks() -> Response:
        from stock_web.api.stocks_page import build_stocks_page_data

        return json_response(build_stocks_page_data(project_root, public_mode=public_mode))

    @router.get("/stocks/search")
    def stock_search(q: str = "") -> Response:
        from stock_web.api.stocks_page import search_stocks

        return json_response(search_stocks(project_root, q))

    @router.get("/stocks/resolve")
    def stock_resolve(code: str = "") -> Response:
        from stock_web.api.symbol_resolver import resolve_symbol_code

        return json_response(resolve_symbol_code(project_root, code))

    @router.get("/stock-detail")
    def stock_detail(symbol: str, market: str = "") -> Response:
        from stock_web.api.stock_detail import build_stock_detail_payload

        try:
            return json_response(build_stock_detail_payload(
                project_root, symbol=symbol, market=market, public_mode=public_mode,
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
        from stock_web.api.home_data import build_public_scanner
        from stock_web.api.scanner import build_scanner

        try:
            builder = build_public_scanner if public_mode else build_scanner
            result = builder(
                project_root, avg_value_20d_min=min_value,
                market_cap_min=min_cap, apply_liquidity_filter=all != 1,
            )
        except ValueError as error:
            return json_response({"error": str(error)}, status_code=400)
        return json_response(result)

    @router.post("/watchlists")
    async def save_watchlist(request: Request) -> Response:
        from stock_web.api.stocks_page import StocksInputError, mutate_watchlist

        if not loopback(request):
            return write_response(
                request, path="/api/watchlists", payload={"error": "로컬 접속에서만 저장할 수 있습니다."},
                status_code=403, error_code="LOCAL_ONLY",
            )
        try:
            saved = mutate_watchlist(project_root, await request.json())
        except (KeyError, ValueError, StocksInputError) as error:
            return write_response(
                request, path="/api/watchlists", payload={"error": str(error)},
                status_code=400, error_code="VALIDATION_ERROR",
            )
        clear_home_cache()
        return write_response(
            request, path="/api/watchlists", payload=saved, status_code=200,
            error_code="OK", row_counts=watchlist_counts(saved),
        )

    @router.post("/watchlist/items")
    async def add_watchlist(request: Request) -> Response:
        from stock_web.api.stocks_page import StocksInputError, add_watchlist_item

        if not loopback(request):
            return write_response(
                request, path="/api/watchlist/items", payload={"error": "로컬 접속에서만 저장할 수 있습니다."},
                status_code=403, error_code="LOCAL_ONLY",
            )
        try:
            saved = add_watchlist_item(project_root, await request.json())
        except (KeyError, ValueError, StocksInputError) as error:
            return write_response(
                request, path="/api/watchlist/items", payload={"error": str(error)},
                status_code=400, error_code="VALIDATION_ERROR",
            )
        clear_home_cache()
        return write_response(
            request, path="/api/watchlist/items", payload=saved, status_code=200,
            error_code="OK", row_counts=watchlist_counts(saved),
        )

    @router.delete("/watchlist/items")
    async def delete_watchlist(request: Request) -> Response:
        from stock_web.api.stocks_page import StocksInputError, remove_watchlist_item

        if not loopback(request):
            return write_response(
                request, path="/api/watchlist/items", payload={"error": "로컬 접속에서만 저장할 수 있습니다."},
                status_code=403, error_code="LOCAL_ONLY",
            )
        try:
            saved = remove_watchlist_item(project_root, await request.json())
        except (KeyError, ValueError, StocksInputError) as error:
            return write_response(
                request, path="/api/watchlist/items", payload={"error": str(error)},
                status_code=400, error_code="VALIDATION_ERROR",
            )
        clear_home_cache()
        return write_response(
            request, path="/api/watchlist/items", payload=saved, status_code=200,
            error_code="OK", row_counts=watchlist_counts(saved),
        )

    @router.post("/watchlist/items/move")
    async def reorder_watchlist(request: Request) -> Response:
        from stock_web.api.stocks_page import StocksInputError, move_watchlist_item

        if not loopback(request):
            return write_response(
                request, path="/api/watchlist/items/move", payload={"error": "로컬 접속에서만 저장할 수 있습니다."},
                status_code=403, error_code="LOCAL_ONLY",
            )
        try:
            saved = move_watchlist_item(project_root, await request.json())
        except (KeyError, ValueError, StocksInputError) as error:
            return write_response(
                request, path="/api/watchlist/items/move", payload={"error": str(error)},
                status_code=400, error_code="VALIDATION_ERROR",
            )
        clear_home_cache()
        return write_response(
            request, path="/api/watchlist/items/move", payload=saved, status_code=200,
            error_code="OK", row_counts=watchlist_counts(saved),
        )

    @router.post("/watch-conditions")
    async def watch_conditions(request: Request) -> Response:
        from stock_web.api.stocks_page import StocksInputError, save_conditions

        if not loopback(request):
            return write_response(
                request, path="/api/watch-conditions", payload={"error": "로컬 접속에서만 저장할 수 있습니다."},
                status_code=403, error_code="LOCAL_ONLY",
            )
        try:
            saved = save_conditions(project_root, await request.json())
        except (ValueError, StocksInputError) as error:
            return write_response(
                request, path="/api/watch-conditions", payload={"error": str(error)},
                status_code=400, error_code="VALIDATION_ERROR",
            )
        clear_home_cache()
        return write_response(
            request, path="/api/watch-conditions", payload=saved, status_code=200,
            error_code="OK", row_counts={"conditions": len(saved.get("conditions", []))},
        )

    @router.get("/manual/accounts")
    def manual_accounts() -> Response:
        from stock_web.api.account_page import build_manual_account_data

        return json_response(build_manual_account_data(project_root))

    @router.post("/manual/accounts")
    async def save_manual(request: Request) -> Response:
        from stock_web.api.account_page import AccountInputError, save_manual_accounts

        if not loopback(request):
            return write_response(
                request, path="/api/manual/accounts", payload={"error": "로컬 접속에서만 저장할 수 있습니다."},
                status_code=403, error_code="LOCAL_ONLY",
            )
        try:
            payload = await request.json()
            saved = save_manual_accounts(project_root, payload)
        except (ValueError, AccountInputError) as error:
            return write_response(
                request, path="/api/manual/accounts", payload={"error": str(error)},
                status_code=400, error_code="VALIDATION_ERROR",
            )
        clear_home_cache()
        return write_response(
            request, path="/api/manual/accounts", payload=saved, status_code=200,
            error_code="OK", row_counts={
                "accounts": len(saved.get("accounts", [])),
                "positions": sum(len(item.get("positions", [])) for item in saved.get("accounts", [])),
            },
        )

    @router.post("/manual/dividends")
    async def save_manual_dividend(request: Request) -> Response:
        from stock_web.api.account_page import AccountInputError, save_dividend

        if not loopback(request):
            return write_response(
                request, path="/api/manual/dividends",
                payload={"error": "로컬 접속에서만 저장할 수 있습니다."},
                status_code=403, error_code="LOCAL_ONLY",
            )
        try:
            saved = save_dividend(project_root, await request.json())
        except (ValueError, AccountInputError) as error:
            return write_response(
                request, path="/api/manual/dividends", payload={"error": str(error)},
                status_code=400, error_code="VALIDATION_ERROR",
            )
        return write_response(
            request, path="/api/manual/dividends", payload=saved,
            status_code=200, error_code="OK",
            row_counts={"entries": len(saved.get("entries", []))},
        )

    @router.get("/net-worth")
    def net_worth() -> Response:
        from stock_web.api.account_page import build_net_worth_data

        return json_response(build_net_worth_data(project_root))

    @router.get("/research")
    def research() -> Response:
        from stock_web.api.research_page import build_research_payload

        return json_response(build_research_payload(project_root))

    @router.get("/research/forward")
    def research_forward() -> Response:
        from stock_web.api.research_page import build_forward_payload

        return json_response(build_forward_payload(project_root))

    @router.get("/research/experiment")
    def research_experiment(request: Request) -> Response:
        from stock_web.api.research_page import (
            ExperimentRateLimitError,
            ResearchInputError,
            evaluate_experiment,
        )

        try:
            payload = evaluate_experiment(
                project_root, request.query_params,
                client_key=request_client_key(request),
            )
        except ExperimentRateLimitError as error:
            return json_response({"error": str(error)}, status_code=429)
        except ResearchInputError as error:
            return json_response({"error": str(error)}, status_code=400)
        payload["can_register"] = loopback(request)
        return json_response(payload)

    @router.post("/research/candidates")
    async def research_candidates(request: Request) -> Response:
        from stock_web.api.research_page import ResearchInputError, register_experiment_candidate

        if not loopback(request):
            return json_response({"error": "후보 등록은 PC에서만 할 수 있습니다."}, status_code=403)
        try:
            payload = register_experiment_candidate(project_root, await request.json())
        except (ValueError, ResearchInputError) as error:
            return json_response({"error": str(error)}, status_code=400)
        except RuntimeError as error:
            return json_response({"error": str(error)}, status_code=500)
        return json_response(payload)

    @router.get("/research/compound/grid")
    def research_compound_grid(request: Request) -> Response:
        from stock_web.api.research_page import (
            CompoundGridNotFound,
            build_compound_grid_payload,
        )

        try:
            payload = build_compound_grid_payload(
                project_root,
                basket=str(request.query_params.get("basket") or ""),
                product=str(request.query_params.get("product") or ""),
            )
        except CompoundGridNotFound as error:
            return json_response({"error": str(error)}, status_code=404)
        return json_response(payload)

    @router.get("/research/crisis-overlay")
    def research_crisis_overlay() -> Response:
        from stock_web.api.research_page import (
            CrisisOverlayNotFound,
            build_crisis_overlay_payload,
        )

        try:
            payload = build_crisis_overlay_payload(project_root)
        except CrisisOverlayNotFound as error:
            return json_response({"error": str(error)}, status_code=404)
        return json_response(payload)

    @router.post("/research/compound/holdout-view")
    async def research_compound_holdout_view(request: Request) -> Response:
        from stock_web.api.research_page import (
            CompoundGridNotFound,
            ResearchInputError,
            record_compound_holdout_view,
        )

        try:
            payload = record_compound_holdout_view(
                project_root, await request.json(),
                client_key=request_client_key(request),
            )
        except CompoundGridNotFound as error:
            return json_response({"error": str(error)}, status_code=404)
        except (ValueError, ResearchInputError) as error:
            return json_response({"error": str(error)}, status_code=400)
        return json_response(payload)

    @router.get("/research/compound/run")
    def research_compound_run_status() -> Response:
        from stock_web.api.research_page import build_compound_run_status

        return json_response(build_compound_run_status(project_root))

    @router.post("/research/compound/run")
    async def research_compound_run(request: Request) -> Response:
        from stock_web.api.research_page import (
            CompoundRunConflict,
            ResearchInputError,
            start_compound_run,
        )

        if not loopback(request):
            return json_response({"error": "계산 실행은 PC에서만 할 수 있습니다."}, status_code=403)
        try:
            payload = start_compound_run(project_root, await request.json())
        except CompoundRunConflict as error:
            return json_response({"error": str(error)}, status_code=409)
        except (ValueError, ResearchInputError) as error:
            return json_response({"error": str(error)}, status_code=400)
        return json_response(payload, status_code=202)

    @router.post("/net-worth")
    async def save_net_worth_snapshot(request: Request) -> Response:
        from stock_web.api.account_page import AccountInputError, save_net_worth

        if not loopback(request):
            return write_response(
                request, path="/api/net-worth", payload={"error": "로컬 접속에서만 저장할 수 있습니다."},
                status_code=403, error_code="LOCAL_ONLY",
            )
        try:
            payload = await request.json()
            saved = save_net_worth(project_root, payload)
        except (ValueError, AccountInputError) as error:
            return write_response(
                request, path="/api/net-worth", payload={"error": str(error)},
                status_code=400, error_code="VALIDATION_ERROR",
            )
        clear_home_cache()
        latest = saved.get("latest") if isinstance(saved, dict) else None
        return write_response(
            request, path="/api/net-worth", payload=saved, status_code=200,
            error_code="OK", row_counts={
                "assets": len(latest.get("assets", [])) if isinstance(latest, dict) else 0,
                "liabilities": len(latest.get("liabilities", [])) if isinstance(latest, dict) else 0,
            },
        )

    @router.get("/ping")
    def ping() -> dict[str, str]:
        return {"status": "ok", "observed_at_utc": datetime.now(timezone.utc).isoformat()}

    if public_mode:
        @router.api_route("/{guest_path:path}", methods=["POST", "DELETE"])
        def reject_unknown_guest_write(guest_path: str) -> Response:
            return json_response({"error": "guest mode"}, status_code=404)

    return router
