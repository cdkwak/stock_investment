"""FastAPI application factory for the local read-only dashboard.

Boundaries (same as the PySide6 app): never calls a provider and never writes
retained market/account datasets. Explicit loopback-only user inputs use their
local atomic stores. Presentation lives in templates/static; data access goes
through the typed services in ``stock_web.api``.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import ipaddress

from stock_web.api.fmt import format_kst

PACKAGE_ROOT = Path(__file__).resolve().parent
TAILSCALE_NETWORK = ipaddress.ip_network("100.64.0.0/10")
TAILSCALE_NETWORK_V6 = ipaddress.ip_network("fd7a:115c:a1e0::/48")
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "testclient"}


def client_allowed(request: Request) -> bool:
    """Loopback or a Tailscale (CGNAT-range) peer; anything else is refused."""
    client = request.client
    if client is None:
        return True  # in-process test clients without a transport address
    if not _private_address(str(client.host)):
        return False
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        # `tailscale serve` (HTTPS on the tailnet) relays the request and reports the real
        # peer here (observed live 2026-09-03: the relay hop itself connects from the peer's
        # tailnet address). Both the hop and the reported peer must be private; a public
        # client cannot forge its way in by adding the header because its hop is refused.
        return all(_private_address(part.strip()) for part in forwarded.split(",") if part.strip())
    return True


def _private_address(host: str) -> bool:
    if host in LOOPBACK_HOSTS:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address in TAILSCALE_NETWORK or address in TAILSCALE_NETWORK_V6


def _project_root() -> Path:
    override = os.environ.get("STOCK_WEB_PROJECT_ROOT")
    if override:
        return Path(override).resolve()
    return PACKAGE_ROOT.parents[1]


def create_app(project_root: Path | None = None) -> FastAPI:
    root = (project_root or _project_root()).resolve()
    app = FastAPI(title="Stock Investment Dashboard", docs_url=None, redoc_url=None)
    app.state.project_root = root
    app.mount("/static", StaticFiles(directory=PACKAGE_ROOT / "static"), name="static")
    templates = Jinja2Templates(directory=PACKAGE_ROOT / "templates")
    static_root = PACKAGE_ROOT / "static"
    templates.env.globals["static_version"] = str(
        int(max(p.stat().st_mtime for p in static_root.glob("*")) if any(static_root.glob("*")) else 0)
    )
    templates.env.globals["format_kst"] = format_kst

    from stock_web.api.router import build_router

    app.include_router(build_router(root), prefix="/api")

    @app.middleware("http")
    async def _private_network_only(request: Request, call_next):
        # The dashboard has no login. When bound beyond loopback it may only be reached from
        # this machine or over the user's Tailscale network (CGNAT range 100.64.0.0/10);
        # every other client address is refused before any handler runs.
        if not client_allowed(request):
            return PlainTextResponse("이 대시보드는 로컬 또는 Tailscale 기기에서만 열 수 있습니다.", status_code=403)
        return await call_next(request)

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request, symbol: str = "") -> HTMLResponse:
        return templates.TemplateResponse(
            request, "home.html", {"page": "home", "initial_symbol": symbol.strip()},
        )

    @app.get("/data", response_class=HTMLResponse)
    def data_page(request: Request, status: str = "OPERATIONAL") -> HTMLResponse:
        from stock_web.api.data_page import build_data_page_context

        context = build_data_page_context(root, status)
        context.update({"request": request, "page": "data"})
        return templates.TemplateResponse(request, "data.html", context)

    @app.get("/account", response_class=HTMLResponse)
    def account_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "account.html", {"page": "account"})

    @app.get("/stocks", response_class=HTMLResponse)
    def stocks_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "stocks.html", {"page": "stocks"})

    @app.get("/market", response_class=HTMLResponse)
    def market_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "market.html", {"page": "market"})

    return app
