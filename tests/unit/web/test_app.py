from __future__ import annotations

import json
from pathlib import Path

import pytest

try:
    from fastapi.testclient import TestClient
except RuntimeError:  # local env may not have the declared ``test`` extra yet
    from tests.unit.web import ASGITestClient as TestClient

from stock_web.api import home_data
from stock_web.api.data_page import load_scheduler_receipts
from stock_web.app import create_app
from tests.unit.web import make_project, new_temp_root


def test_home_chart_and_data_routes_are_provider_free_and_successful(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_project(new_temp_root())
    monkeypatch.setenv("STOCK_WEB_RULES_PATH", str(root / "missing-rules.md"))
    home_data._HOME_CACHE.clear()
    client = TestClient(create_app(root))

    home = client.get("/api/home")
    chart = client.get("/api/chart", params={"symbol": "KOSPI", "range": "3M"})
    data_page = client.get("/data", params={"status": "DAILY"})

    assert home.status_code == 200
    assert chart.status_code == 200
    assert chart.json()["symbol"] == "KOSPI"
    assert chart.json()["candles"]
    assert data_page.status_code == 200
    assert "TEST_TASK" in data_page.text
    assert "kr_index_daily" in data_page.text


def test_chart_endpoint_does_not_use_the_home_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str]] = []

    def fake_chart(_root: Path, *, symbol: str, range_key: str) -> dict[str, str]:
        calls.append((symbol, range_key))
        return {"symbol": symbol, "range": range_key}

    monkeypatch.setattr(home_data, "build_chart_payload", fake_chart)
    client = TestClient(create_app(new_temp_root()))

    assert client.get("/api/chart?symbol=KOSPI&range=3M").status_code == 200
    assert client.get("/api/chart?symbol=KOSPI&range=3M").status_code == 200
    assert calls == [("KOSPI", "3M"), ("KOSPI", "3M")]


def test_phone_css_keeps_the_topbar_on_one_row() -> None:
    css = (Path(__file__).parents[3] / "src/stock_web/static/app.css").read_text(encoding="utf-8")
    assert "@media (max-width: 599px)" in css
    assert 'content: "SI"' in css
    assert "overflow-x: auto" in css
    assert "flex-wrap: nowrap" in css


def test_data_page_uses_korean_grouped_health_and_kst_receipts() -> None:
    root = make_project(new_temp_root())
    failed = root / "artifacts/scheduler_logs/FAILED_TASK_last.json"
    failed.write_text(json.dumps({
        "task_name": "FAILED_TASK", "status": "FAIL",
        "finished_at_utc": "2026-09-16T22:00:00+00:00",
        "api_calls": 2, "terminal_exit_code": 1,
    }), encoding="utf-8")

    receipts = load_scheduler_receipts(root)
    page = TestClient(create_app(root)).get("/data?status=ALL")

    assert receipts[0]["task"] == "FAILED_TASK"
    assert receipts[0]["finished_label"] == "09-17 07:00"
    assert receipts[0]["result_code"] == 1
    assert page.status_code == 200
    for header in ("데이터셋", "신선도", "최신", "예상", "운영 상태", "차단 사유", "자동화"):
        assert header in page.text
    assert "정상" in page.text
    assert "일별 · KR 지수" in page.text
    assert 'title="CURRENT"' in page.text
    assert "마지막 실행(KST)" in page.text
    assert "09-17 09:01" in page.text
    assert "데이터셋·상태 검색" in page.text


def test_dashboard_static_polish_contracts_are_present() -> None:
    root = Path(__file__).parents[3] / "src/stock_web"
    app_js = (root / "static/app.js").read_text(encoding="utf-8")
    account_js = (root / "static/account.js").read_text(encoding="utf-8")
    css = (root / "static/app.css").read_text(encoding="utf-8")

    assert "window.SIChart = { renderLineChart }" in app_js
    assert "si-benchmark-line" in app_js
    assert "LightweightCharts.createChart" in app_js
    assert "LightweightCharts" not in account_js
    assert "return_pct_modified_dietz" in account_js
    assert "tile-sub-note" in app_js
    assert "summary-separator" in app_js
    assert "regime-title-line" in app_js
    assert ".market-main-chart { width: 100%; height: 440px; }" in css
    assert ".market-main-chart { height: 280px; }" in css
    assert ".data-health-table td::before" in css


def test_private_network_guard_allows_loopback_and_tailscale_only() -> None:
    from tests.unit.web import ASGITestClient, make_project, new_temp_root

    client = ASGITestClient(create_app(make_project(new_temp_root())))
    assert client.get("/", client_host="127.0.0.1").status_code == 200
    assert client.get("/", client_host="100.107.40.4").status_code == 200
    assert client.get("/", client_host="100.127.255.254").status_code == 200
    refused = client.get("/", client_host="192.168.0.20")
    assert refused.status_code == 403
    assert "Tailscale" in refused.text
    assert client.get("/api/home", client_host="8.8.8.8").status_code == 403
    # Writes stay loopback-only even for Tailscale peers.
    assert client.post(
        "/api/watchlist/items", json={"list_id": "favorites", "market": "KRX", "symbol": "123320"},
        client_host="100.107.40.4",
    ).status_code == 403
