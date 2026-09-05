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
    assert "정시" in page.text
    assert "일별 · KR 지수" in page.text
    assert 'title="CURRENT"' in page.text
    assert "마지막 실행(KST)" in page.text
    assert "09-17 09:01" in page.text
    assert "데이터셋·상태 검색" in page.text


def test_dashboard_static_polish_contracts_are_present() -> None:
    root = Path(__file__).parents[3] / "src/stock_web"
    app_js = (root / "static/app.js").read_text(encoding="utf-8")
    account_js = (root / "static/account.js").read_text(encoding="utf-8")
    account_css = (root / "static/account.css").read_text(encoding="utf-8")
    account_html = (root / "templates/account.html").read_text(encoding="utf-8")
    css = (root / "static/app.css").read_text(encoding="utf-8")

    assert "window.SIChart = { renderLineChart }" in app_js
    assert "si-benchmark-line" in app_js
    assert "LightweightCharts.createChart" in app_js
    assert "LightweightCharts" not in account_js
    assert "return_pct_modified_dietz" in account_js
    assert "visibleHoldingRows" in account_js
    assert "renderAllocation" in account_js
    assert 'postJson("/api/manual/dividends"' in account_js
    assert 'id="net-worth-overlay"' in account_html
    assert 'id="allocation-tabs"' in account_html
    assert 'id="dividend-chart"' in account_html
    assert ".account-main-grid" in account_css
    assert ".allocation-card { order: 2; }" in account_css
    assert "tile-sub-note" in app_js
    assert "summary-separator" in app_js
    assert "regime-title-line" in app_js
    assert ".market-main-chart { width: 100%; height: 440px; }" in css
    assert ".market-main-chart { height: 280px; }" in css
    assert ".data-health-table td::before" in css


def test_home_chart_stops_at_content_height_and_resizes() -> None:
    root = Path(__file__).parents[3] / "src/stock_web"
    script = (root / "static/app.js").read_text(encoding="utf-8")
    css = (root / "static/app.css").read_text(encoding="utf-8")

    # Columns start-align and the chart card never grows: the 531px blank card below the
    # chart (review 10:30, regression of b643579) came from the left column stretching.
    assert ".home-dashboard { align-items: start; }" in css
    assert ".chart-card { display: flex; flex: 0 0 auto; flex-direction: column;" in css
    assert "align-self: start" in css
    assert ".chart { width: 100%; flex: 1 1 auto; min-height: 380px; max-height: 540px; }" in css
    assert "@media (max-width: 768px)" in css
    assert ".chart { flex: none; height: 320px; min-height: 320px; }" in css
    assert "chart.applyOptions({ height })" in script
    assert "chart.resize(width, height)" in script
    assert "priceScale(\"vol\").applyOptions" in script
    assert "rsiPanelSeries.setData" in script
    assert "rsiOverlaySeries.setData" in script
    assert "price_display" in script and "ma60_display" in script
    assert ".tiles.collapsed .tile:nth-child(n+13)" in css
    assert ".tiles.collapsed .tile:nth-child(n+9)" in css
    assert "@media (min-width: 1200px)" in css
    assert "현재가 · 마감 기준" in script
    assert "밤사이 ${overnight.currency" in script
    assert "changeCell(b.d1_pct, b.d1_note)" in script
    assert "changeCell(b.d20_pct, b.d20_note)" in script


def test_regime_evidence_is_collapsed_persisted_and_expanded() -> None:
    root = Path(__file__).parents[3] / "src/stock_web"
    script = (root / "static/app.js").read_text(encoding="utf-8")
    template = (root / "templates/home.html").read_text(encoding="utf-8")
    css = (root / "static/app.css").read_text(encoding="utf-8")

    assert 'data-expanded="false"' in template
    assert 'id="regime-evidence-strip" hidden' in template
    assert 'const regimeEvidenceStorageKey = "si.regime.evidence"' in script
    assert 'open ? "open" : "closed"' in script
    assert 'replace(/^신호 (?=\\d+\\/3)/, "자료 ")' in script
    for label in ("판정 규칙",):
        assert label in script
    assert "점수 = RSI14(&gt;70 +1 / &lt;30 −1)" in script
    assert "−2 침체 · −1 약세 · 0 중립 · +1 강세 · +2 과열" in script
    assert "regime-score-meter" in script
    assert ".regime-card .regime-verdict.is-cool" in css
    assert "금리차 1개월 −0.25%p" in script
    assert ".regime-evidence-strip[hidden] { display: none; }" in css
    assert 'SK하이닉스(ADR) · NASDAQ · 원주 <a href="/stocks?symbol=000660">000660</a>' in script


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


def test_tailscale_serve_relay_is_treated_as_remote() -> None:
    from tests.unit.web import ASGITestClient, make_project, new_temp_root

    client = ASGITestClient(create_app(make_project(new_temp_root())))
    relayed = {"X-Forwarded-For": "100.86.222.47", "Tailscale-User-Login": "user@example.com"}
    # A tailnet peer relayed by `tailscale serve` (hop = loopback) may read...
    assert client.get("/", client_host="127.0.0.1", headers=relayed).status_code == 200
    # ...but must not unlock loopback-only writes.
    assert client.post(
        "/api/watchlist/items", json={"list_id": "favorites", "market": "KRX", "symbol": "123320"},
        client_host="127.0.0.1", headers=relayed,
    ).status_code == 403
    # A forwarded header from a non-local hop is refused, and a forwarded public address too.
    assert client.get("/", client_host="192.168.0.20", headers=relayed).status_code == 403
    assert client.get(
        "/", client_host="127.0.0.1", headers={"X-Forwarded-For": "8.8.8.8"},
    ).status_code == 403


def test_tailscale_serve_observed_shape_hop_is_the_peer_address() -> None:
    """Observed live 2026-09-03: the relay connects from the peer's tailnet address itself."""
    from tests.unit.web import ASGITestClient, make_project, new_temp_root

    client = ASGITestClient(create_app(make_project(new_temp_root())))
    relayed = {"X-Forwarded-For": "100.86.222.47", "Tailscale-User-Login": "user@example.com"}
    assert client.get("/", client_host="100.86.222.47", headers=relayed).status_code == 200
    v6 = {"X-Forwarded-For": "fd7a:115c:a1e0::8432:2805"}
    assert client.get("/", client_host="fd7a:115c:a1e0::1234", headers=v6).status_code == 200
    assert client.post(
        "/api/watchlist/items", json={"list_id": "favorites", "market": "KRX", "symbol": "123320"},
        client_host="100.86.222.47", headers=relayed,
    ).status_code == 403


def test_journal_note_endpoint_is_loopback_only_and_hidden_in_public_mode(monkeypatch) -> None:
    import json as _json

    from tests.unit.web import ASGITestClient, make_project, new_temp_root

    root = make_project(new_temp_root())
    journal_dir = root / "vault/일지"
    journal_dir.mkdir(parents=True, exist_ok=True)
    settings = root / "artifacts/local_user/web_settings.json"
    settings.parent.mkdir(parents=True, exist_ok=True)
    settings.write_text(_json.dumps({"journal_dir": str(journal_dir)}), encoding="utf-8")
    client = ASGITestClient(create_app(root))
    assert client.post(
        "/api/journal/note", json={"text": "반도체 추세 유지, 관망"}, client_host="100.107.40.4",
    ).status_code == 403
    assert client.post(
        "/api/journal/note", json={"text": "총 1,200,000원 매수"}, client_host="127.0.0.1",
    ).status_code == 400
    saved = client.post("/api/journal/note", json={"text": "반도체 추세 유지, 관망"}, client_host="127.0.0.1")
    assert saved.status_code == 200, saved.text
    body = saved.json()
    written = (journal_dir / f"{body['date']} 투자.md").read_text(encoding="utf-8")
    assert "## 오늘 판단" in written and "판단: 반도체 추세 유지, 관망" in written
    monkeypatch.setenv("STOCK_WEB_PUBLIC_MODE", "1")
    guest = ASGITestClient(create_app(root))
    assert guest.post(
        "/api/journal/note", json={"text": "관망"}, client_host="127.0.0.1",
    ).status_code == 404
