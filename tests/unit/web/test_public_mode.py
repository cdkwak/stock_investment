from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
from types import SimpleNamespace

from fastapi.testclient import TestClient

from stock_web import __main__ as web_main
from stock_web.api import home_data, regime as regime_api, scanner as scanner_api, stocks_page
from stock_web.app import create_app
from stock_web.auth import set_pin
from tests.unit.web import ASGITestClient, make_project, new_temp_root


REPOSITORY_ROOT = Path(__file__).parents[3]
PUBLIC_BANNER = "게스트 모드 · 개인 계좌 정보 없음 · 판단 참고용, 투자 조언 아님"
EXPECTED_PUBLIC_WATCHLIST = [
    ("KOSPI200 IT ETF", "139260"),
    ("TIGER 레버리지", "123320"),
    ("TIGER 200IT레버리지", "243880"),
    ("삼성전자", "005930"),
    ("SK하이닉스", "000660"),
    ("QQQ", "QQQ"),
    ("SPY", "SPY"),
    ("SOXX", "SOXX"),
    ("TLT", "TLT"),
]


def _public_project() -> Path:
    root = make_project(new_temp_root())
    destination = root / "config/public_watchlist.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(REPOSITORY_ROOT / "config/public_watchlist.json", destination)
    return root


def test_public_mode_blocks_private_apis_and_all_api_writes(monkeypatch) -> None:
    root = _public_project()
    monkeypatch.setenv("STOCK_WEB_PUBLIC_MODE", "1")
    client = ASGITestClient(create_app(root))

    blocked = [
        client.get("/api/account"),
        client.get("/api/cash-flows"),
        client.get("/api/net-worth"),
        client.get("/api/trade-journal"),
        client.get("/api/manual/accounts"),
        client.post("/api/cash-flows", json={}),
        client.delete("/api/cash-flows", json={}),
        client.post("/api/trade-journal/manual", json={}),
        client.delete("/api/trade-journal/manual", json={}),
        client.post("/api/net-worth", json={}),
        client.post("/api/manual/accounts", json={}),
        client.post("/api/watchlists", json={}),
        client.post("/api/watchlist/items", json={}),
        client.delete("/api/watchlist/items", json={}),
        client.post("/api/watchlist/items/move", json={}),
        client.post("/api/watch-conditions", json={}),
        client.post("/api/research/candidates", json={}),
        client.post("/api/not-a-route", json={}),
        client.delete("/api/not-a-route", json={}),
    ]

    assert all(response.status_code == 404 for response in blocked)
    assert all(response.json() == {"error": "guest mode"} for response in blocked)


def test_public_home_and_stocks_use_only_the_fixed_public_watchlist(monkeypatch) -> None:
    root = _public_project()
    monkeypatch.setenv("STOCK_WEB_PUBLIC_MODE", "1")
    home_data._HOME_CACHE.clear()
    private_watchlist = root / "artifacts/local_user/watchlists.json"
    private_watchlist.parent.mkdir(parents=True, exist_ok=True)
    private_watchlist.write_text('{"private_marker":"must-not-be-read"}', encoding="utf-8")
    private_account = root / "data/local/account_snapshots/kb_self.json"
    private_account.parent.mkdir(parents=True, exist_ok=True)
    private_account.write_text('{"cash_krw":999999999}', encoding="utf-8")
    before = {
        private_watchlist: private_watchlist.read_bytes(),
        private_account: private_account.read_bytes(),
    }
    forbidden_calls: list[str] = []
    monkeypatch.setattr(
        home_data, "build_account",
        lambda _root: forbidden_calls.append("account") or {},
    )
    monkeypatch.setattr(
        stocks_page, "LocalWatchlistService",
        lambda *_args, **_kwargs: forbidden_calls.append("watchlist service"),
    )
    monkeypatch.setattr(
        scanner_api, "load_conditions",
        lambda _root: forbidden_calls.append("scanner conditions") or {},
    )
    monkeypatch.setattr(
        scanner_api, "_read_cache",
        lambda *_args, **_kwargs: forbidden_calls.append("scanner cache read"),
    )
    monkeypatch.setattr(
        scanner_api, "_write_cache",
        lambda *_args, **_kwargs: forbidden_calls.append("scanner cache write"),
    )
    monkeypatch.setattr(
        regime_api, "resolve_rules_path",
        lambda *_args, **_kwargs: forbidden_calls.append("private rules") or Path("missing"),
    )
    client = ASGITestClient(create_app(root))

    home_response = client.get("/api/home")
    stocks_response = client.get("/api/stocks")

    assert home_response.status_code == stocks_response.status_code == 200
    home = home_response.json()
    sections = home["sections"]
    assert sections["account"] == {"guest": True}
    assert sections["regime"]["rules"] is None
    assert "research_current" in sections["regime"]
    for private_key in (
        "cash_krw", "invest_total_krw", "net_worth_krw", "manual_accounts",
        "return_metrics", "recent_cashflows", "total_asset_history",
    ):
        assert private_key not in home_response.text

    configured = json.loads(
        (root / "config/public_watchlist.json").read_text(encoding="utf-8"),
    )["items"]
    expected = [(item["name"], item["symbol"]) for item in configured]
    assert expected == EXPECTED_PUBLIC_WATCHLIST
    home_rows = sections["watchlist"]["rows"]
    stock_rows = stocks_response.json()["table"]
    assert [(row["name"], row["symbol"]) for row in home_rows] == expected
    assert [(row["name"], row["symbol"]) for row in stock_rows] == expected
    assert all(row["held"] is False for row in home_rows)
    assert all(row["held"] is False for row in stock_rows)
    assert "held_count" not in sections["watchlist"]
    assert forbidden_calls == []
    assert not (root / "artifacts/local_user/scanner_cache.json").exists()
    assert {path: path.read_bytes() for path in before} == before


def test_public_pages_show_banner_hide_account_nav_and_render_placeholders(monkeypatch) -> None:
    root = _public_project()
    monkeypatch.setenv("STOCK_WEB_PUBLIC_MODE", "1")
    app = create_app(root)
    client = ASGITestClient(app)

    assert app.state.public_mode is True
    assert client.get("/", client_host="8.8.8.8").status_code == 403
    assert client.get("/", client_host="100.107.40.4").status_code == 200
    for path in ("/", "/market", "/stocks", "/research", "/data"):
        page = client.get(path)
        assert page.status_code == 200
        assert PUBLIC_BANNER in page.text
        assert 'data-public="1"' in page.text
        assert 'href="/account"' not in page.text
    stocks = client.get("/stocks").text
    assert '<details class="card stocks-management" open hidden>' in stocks
    account = client.get("/account")
    assert account.status_code == 200
    assert PUBLIC_BANNER in account.text
    assert "게스트 모드에서는 계좌 화면이 없습니다" in account.text
    assert "/static/account.js" not in account.text
    assert "데이터셋 상태, 실행 기록, 자격증명 만료 정보는 비공개입니다." in client.get("/data").text


def test_private_mode_remains_the_default(monkeypatch) -> None:
    monkeypatch.delenv("STOCK_WEB_PUBLIC_MODE", raising=False)
    root = make_project(new_temp_root())
    app = create_app(root)
    client = ASGITestClient(app)

    assert app.state.public_mode is False
    assert client.get("/api/account").status_code == 200
    page = client.get("/")
    assert PUBLIC_BANNER not in page.text
    assert 'data-public="0"' in page.text
    assert 'href="/account"' in page.text


def test_public_mode_keeps_pin_lock_without_writing_a_session_secret(monkeypatch) -> None:
    root = _public_project()
    set_pin(root, "2468", iterations=1_000)
    monkeypatch.setenv("STOCK_WEB_PUBLIC_MODE", "1")
    client = TestClient(
        create_app(root), base_url="https://dashboard.test", follow_redirects=False,
        client=("100.107.40.4", 50000),
    )
    relay = {"X-Forwarded-For": "100.86.222.47"}

    assert client.get("/", headers=relay).status_code == 303
    login = client.post(
        "/login", headers=relay, data={"pin": "2468", "next": "/"},
    )
    assert login.status_code == 303
    assert client.get("/", headers=relay).status_code == 200
    assert not (root / "data/local/web_session_secret").exists()


def test_public_and_private_home_documents_use_separate_memory_cache_keys(monkeypatch) -> None:
    root = new_temp_root().resolve()
    home_data._HOME_CACHE.clear()
    monkeypatch.setattr(
        home_data, "_build_home_payload_uncached",
        lambda _root, public_mode=False: {"guest": public_mode},
    )

    private = home_data.build_home_payload(root, public_mode=False)
    public = home_data.build_home_payload(root, public_mode=True)

    assert private == {"guest": False}
    assert public == {"guest": True}
    assert set(home_data._HOME_CACHE) == {str(root), f"{root}|guest"}


def test_public_cli_flag_sets_environment_before_app_creation(monkeypatch) -> None:
    # Record an environment mutation so pytest restores the variable even though
    # main() assigns it directly rather than through monkeypatch.
    monkeypatch.setenv("STOCK_WEB_PUBLIC_MODE", "0")
    fake_app = SimpleNamespace(
        state=SimpleNamespace(project_root=Path("fixture-root"), public_mode=True),
    )
    calls: dict[str, object] = {}

    def fake_create_app():
        calls["env"] = os.environ.get("STOCK_WEB_PUBLIC_MODE")
        return fake_app

    monkeypatch.setattr(web_main, "create_app", fake_create_app)
    monkeypatch.setattr(
        home_data, "warm_home_payload",
        lambda root, **kwargs: calls.update({"warm_root": root, "warm": kwargs}),
    )
    monkeypatch.setattr(
        web_main.uvicorn, "run",
        lambda app, **kwargs: calls.update({"app": app, "run": kwargs}),
    )
    monkeypatch.setattr(
        "sys.argv", ["stock_web", "--public", "--port", "8790"],
    )

    assert web_main.main() == 0
    assert calls["env"] == "1"
    assert calls["warm"] == {"public_mode": True, "interval_seconds": 55}
    assert calls["run"] == {"host": "127.0.0.1", "port": 8790}
