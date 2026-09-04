"""FastAPI application factory for the local read-only dashboard.

Boundaries (same as the PySide6 app): never calls a provider and never writes
retained market/account datasets. Explicit loopback-only user inputs use their
local atomic stores. Presentation lives in templates/static; data access goes
through the typed services in ``stock_web.api``.
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
from pathlib import Path
import secrets
import time
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from stock_web.api.fmt import format_kst
from stock_web.auth import (
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE_SECONDS,
    PinFailureLimiter,
    create_session_cookie,
    pin_is_configured,
    verify_pin,
    verify_session_cookie,
)

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


def _loopback_address(host: str) -> bool:
    if host in LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _request_is_loopback(request: Request) -> bool:
    if request.client is None:
        return True
    forwarded = [
        part.strip()
        for part in request.headers.get("x-forwarded-for", "").split(",")
        if part.strip()
    ]
    return _loopback_address(str(request.client.host)) and all(
        _loopback_address(part) for part in forwarded
    )


def _client_key(request: Request) -> str:
    forwarded = [
        part.strip()
        for part in request.headers.get("x-forwarded-for", "").split(",")
        if part.strip()
    ]
    if forwarded:
        return forwarded[0]
    return str(request.client.host) if request.client is not None else "in-process"


def _safe_next(value: str | None) -> str:
    candidate = value or "/"
    parsed = urlsplit(candidate)
    if (
        not candidate.startswith("/")
        or candidate.startswith("//")
        or "\\" in candidate
        or any(ord(character) < 32 or ord(character) == 127 for character in candidate)
        or parsed.scheme
        or parsed.netloc
    ):
        return "/"
    return urlunsplit(("", "", parsed.path or "/", parsed.query, ""))


def _project_root() -> Path:
    override = os.environ.get("STOCK_WEB_PROJECT_ROOT")
    if override:
        return Path(override).resolve()
    return PACKAGE_ROOT.parents[1]


def create_app(project_root: Path | None = None) -> FastAPI:
    root = (project_root or _project_root()).resolve()
    public_mode = os.environ.get("STOCK_WEB_PUBLIC_MODE") == "1"
    app = FastAPI(title="Stock Investment Dashboard", docs_url=None, redoc_url=None)
    app.state.project_root = root
    app.state.public_mode = public_mode
    app.mount("/static", StaticFiles(directory=PACKAGE_ROOT / "static"), name="static")
    templates = Jinja2Templates(directory=PACKAGE_ROOT / "templates")
    static_root = PACKAGE_ROOT / "static"
    templates.env.globals["static_version"] = str(
        int(max(p.stat().st_mtime for p in static_root.glob("*")) if any(static_root.glob("*")) else 0)
    )
    templates.env.globals["format_kst"] = format_kst
    templates.env.globals["public_mode"] = public_mode
    pin_failures = PinFailureLimiter()
    guest_session_secret = secrets.token_bytes(32) if public_mode else None

    def session_cookie_is_valid(value: str | None) -> bool:
        if not public_mode:
            return verify_session_cookie(root, value)
        if not value or guest_session_secret is None:
            return False
        try:
            expiry_text, supplied_signature = value.split(".", 1)
            if not expiry_text.isascii() or not expiry_text.isdigit():
                return False
            expiry = int(expiry_text)
        except (AttributeError, TypeError, ValueError):
            return False
        expected = hmac.new(
            guest_session_secret, expiry_text.encode("ascii"), hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, supplied_signature) and expiry > int(time.time())

    def new_session_cookie() -> str:
        if not public_mode:
            return create_session_cookie(root)
        assert guest_session_secret is not None
        expiry_text = str(int(time.time()) + SESSION_MAX_AGE_SECONDS)
        signature = hmac.new(
            guest_session_secret, expiry_text.encode("ascii"), hashlib.sha256,
        ).hexdigest()
        return f"{expiry_text}.{signature}"

    from stock_web.api.router import build_router

    app.include_router(build_router(root, public_mode=public_mode), prefix="/api")

    @app.middleware("http")
    async def _private_network_only(request: Request, call_next):
        # Public addresses are refused before the optional PIN guard is considered.
        if not client_allowed(request):
            return PlainTextResponse("이 대시보드는 로컬 또는 Tailscale 기기에서만 열 수 있습니다.", status_code=403)
        path = request.url.path
        pin_exempt = path == "/login" or path == "/static" or path.startswith("/static/")
        if (
            _request_is_loopback(request)
            or not pin_is_configured(root)
            or pin_exempt
            or session_cookie_is_valid(request.cookies.get(SESSION_COOKIE_NAME))
        ):
            return await call_next(request)
        if path == "/api" or path.startswith("/api/"):
            return JSONResponse({"error": "pin_required"}, status_code=401)
        if request.method in {"GET", "HEAD"}:
            next_path = path
            if request.url.query:
                next_path = f"{next_path}?{request.url.query}"
            return RedirectResponse(
                f"/login?{urlencode({'next': next_path})}", status_code=303,
            )
        return JSONResponse({"error": "pin_required"}, status_code=401)

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request, next: str = "/") -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "login.html",
            {"next_path": _safe_next(next), "error": ""},
        )

    @app.post("/login", response_class=HTMLResponse)
    async def login(request: Request) -> HTMLResponse:
        body = await request.body()
        fields = parse_qs(
            body.decode("utf-8", errors="replace") if len(body) <= 4096 else "",
            keep_blank_values=True,
        )
        next_path = _safe_next(fields.get("next", ["/"])[0])
        client_key = _client_key(request)
        if pin_failures.is_locked(client_key):
            return templates.TemplateResponse(
                request,
                "login.html",
                {"next_path": next_path, "error": "잠시 후 다시 시도하세요"},
                status_code=429,
            )
        candidate = fields.get("pin", [""])[0]
        if not pin_is_configured(root):
            return RedirectResponse(next_path, status_code=303)
        if not verify_pin(root, candidate):
            locked = pin_failures.record_failure(client_key)
            return templates.TemplateResponse(
                request,
                "login.html",
                {
                    "next_path": next_path,
                    "error": "잠시 후 다시 시도하세요" if locked else "PIN이 맞지 않습니다",
                },
                status_code=429 if locked else 401,
            )
        pin_failures.reset(client_key)
        response = RedirectResponse(next_path, status_code=303)
        response.set_cookie(
            SESSION_COOKIE_NAME,
            new_session_cookie(),
            max_age=SESSION_MAX_AGE_SECONDS,
            path="/",
            secure=True,
            httponly=True,
            samesite="lax",
        )
        return response

    @app.post("/logout")
    def logout() -> RedirectResponse:
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie(
            SESSION_COOKIE_NAME,
            path="/",
            secure=True,
            httponly=True,
            samesite="lax",
        )
        return response

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request, symbol: str = "") -> HTMLResponse:
        return templates.TemplateResponse(
            request, "home.html", {"page": "home", "initial_symbol": symbol.strip()},
        )

    @app.get("/data", response_class=HTMLResponse)
    def data_page(request: Request, status: str = "OPERATIONAL") -> HTMLResponse:
        if public_mode:
            return templates.TemplateResponse(
                request, "data.html", {"page": "data"},
            )
        from stock_web.api.data_page import build_data_page_context

        context = build_data_page_context(root, status)
        context.update({"request": request, "page": "data"})
        return templates.TemplateResponse(request, "data.html", context)

    @app.get("/research", response_class=HTMLResponse)
    def research_page(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "research.html", {"page": "research"})

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
