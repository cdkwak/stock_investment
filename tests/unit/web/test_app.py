from __future__ import annotations

from pathlib import Path

import pytest

try:
    from fastapi.testclient import TestClient
except RuntimeError:  # local env may not have the declared ``test`` extra yet
    from tests.unit.web import ASGITestClient as TestClient

from stock_web.api import home_data
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
