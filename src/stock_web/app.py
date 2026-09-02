"""FastAPI application factory for the local read-only dashboard.

Boundaries (same as the PySide6 app): never calls a provider, never writes
market or account data, reads only retained local artifacts under the project
root. Presentation lives in templates/static; data access goes through the
typed services in ``stock_web.api``.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from stock_web.api.fmt import format_kst

PACKAGE_ROOT = Path(__file__).resolve().parent


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
