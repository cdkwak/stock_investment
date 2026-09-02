"""Helpers for provider-free stock_web unit tests."""
from __future__ import annotations

import json
import asyncio
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

import pandas as pd


@dataclass(frozen=True)
class ASGITestResponse:
    status_code: int
    content: bytes
    headers: dict[str, str]

    @property
    def text(self) -> str:
        return self.content.decode("utf-8")

    def json(self) -> object:
        return json.loads(self.content)


class ASGITestClient:
    """Small ASGI client fallback when the declared httpx2 extra is unavailable."""

    __test__ = False

    def __init__(self, app: object):
        self.app = app

    def _request(
        self, method: str, url: str, *, params: dict[str, str] | None = None,
        json_body: object | None = None, client_host: str = "testclient",
    ) -> ASGITestResponse:
        split = urlsplit(url)
        query = split.query
        if params:
            encoded = urlencode(params)
            query = f"{query}&{encoded}" if query else encoded

        async def request() -> ASGITestResponse:
            messages: list[dict[str, object]] = []
            request_sent = False
            body = b"" if json_body is None else json.dumps(json_body, ensure_ascii=False).encode("utf-8")

            async def receive() -> dict[str, object]:
                nonlocal request_sent
                if not request_sent:
                    request_sent = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return {"type": "http.disconnect"}

            async def send(message: dict[str, object]) -> None:
                messages.append(message)

            scope = {
                "type": "http", "asgi": {"version": "3.0"},
                "http_version": "1.1", "method": method, "scheme": "http",
                "path": split.path, "raw_path": split.path.encode("ascii"),
                "query_string": query.encode("ascii"),
                "root_path": "", "headers": [
                    (b"host", b"testserver"),
                    *(([(b"content-type", b"application/json")]) if json_body is not None else []),
                ],
                "client": (client_host, 50000), "server": ("testserver", 80),
                "state": {},
            }
            await self.app(scope, receive, send)
            start = next(message for message in messages if message["type"] == "http.response.start")
            body = b"".join(
                message.get("body", b"") for message in messages
                if message["type"] == "http.response.body"
            )
            headers = {
                key.decode("latin-1"): value.decode("latin-1")
                for key, value in start.get("headers", [])
            }
            return ASGITestResponse(int(start["status"]), body, headers)

        return asyncio.run(request())

    def get(
        self, url: str, *, params: dict[str, str] | None = None,
        client_host: str = "testclient",
    ) -> ASGITestResponse:
        return self._request("GET", url, params=params, client_host=client_host)

    def post(
        self, url: str, *, json: object, client_host: str = "testclient",
    ) -> ASGITestResponse:
        return self._request("POST", url, json_body=json, client_host=client_host)


def new_temp_root() -> Path:
    """Create an isolated disposable root without pytest's broken 0700 ACL here."""
    base = Path(__file__).parents[3] / ".tmp/agents/web-dashboard-20260902/fixtures"
    root = base / uuid4().hex
    root.mkdir(parents=True)
    return root


def _write_parquet(root: Path, relative: str, frame: pd.DataFrame) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)


def make_project(root: Path) -> Path:
    dates = pd.date_range("2026-01-01", periods=260, freq="D")
    close = pd.Series([100.0 + index * 0.2 for index in range(len(dates))])
    kospi = pd.DataFrame({
        "date": dates,
        "market": "KOSPI",
        "symbol": "KOSPI",
        "open": close - 0.2,
        "high": close + 0.5,
        "low": close - 0.5,
        "close": close,
        "volume": 1_000_000,
    })
    _write_parquet(
        root,
        "data/normalized/kr_index_daily/market=KOSPI/year=2026/data.parquet",
        kospi,
    )
    sp500 = kospi.drop(columns="market").assign(symbol="SP500", close=close * 40)
    sp500["open"] = sp500["close"] - 1
    sp500["high"] = sp500["close"] + 2
    sp500["low"] = sp500["close"] - 2
    _write_parquet(
        root, "data/normalized/global_index_price_daily/year=2026/data.parquet", sp500,
    )
    wti = sp500.assign(symbol="WTI_CRUDE_OIL", close=70 + close / 20)
    wti["open"] = wti["close"] - 0.1
    wti["high"] = wti["close"] + 0.3
    wti["low"] = wti["close"] - 0.3
    _write_parquet(
        root, "data/normalized/global_commodity_futures_daily/year=2026/data.parquet", wti,
    )
    _write_parquet(
        root, "data/normalized/fred_vix_daily/year=2026/data.parquet",
        pd.DataFrame({"date": dates, "vixcls": [12 + index / 50 for index in range(260)]}),
    )
    _write_parquet(
        root, "data/normalized/kr_vkospi_daily/year=2026/data.parquet",
        pd.DataFrame({"market_date": dates, "close": [15 + index / 40 for index in range(260)]}),
    )
    _write_parquet(
        root, "data/normalized/fred_usd_fx_daily/year=2026/data.parquet",
        pd.DataFrame({"date": dates, "dexkous": [1350 + index / 10 for index in range(260)]}),
    )
    _write_parquet(
        root, "data/normalized/fred_treasury_yield_daily/year=2026/data.parquet",
        pd.DataFrame({
            "date": dates,
            "dgs2": [4.0 - index / 1000 for index in range(260)],
            "dgs10": [4.2 - index / 1200 for index in range(260)],
            "dgs30": [4.4 - index / 1400 for index in range(260)],
        }),
    )
    _write_parquet(
        root, "data/normalized/kr_credit_balance_daily/year=2026/data.parquet",
        pd.DataFrame({"date": dates, "credit_financing_total": [10e12 + index * 1e9 for index in range(260)]}),
    )
    flows = pd.DataFrame({
        "date": dates,
        "market": "KOSPI",
        "foreigner_buy_amount": 100e8,
        "foreigner_sell_amount": [90e8] * 257 + [110e8] * 3,
        "institution_buy_amount": 80e8,
        "institution_sell_amount": 75e8,
        "individual_buy_amount": 70e8,
        "individual_sell_amount": 85e8,
    })
    _write_parquet(
        root,
        "data/normalized/kr_market_investor_trading_daily/market=KOSPI/year=2026/data.parquet",
        flows,
    )

    health = {
        "schema_version": 2,
        "as_of": "2026-09-17T09:00:00+09:00",
        "datasets": [{
            "dataset": "kr_index_daily",
            "latest": "2026-09-17",
            "expected": "2026-09-17",
            "freshness": "CURRENT",
            "runtime_coverage": "VALIDATED",
        }],
    }
    health_path = root / "artifacts/daily_health/universe_data_v2_20260819.json"
    health_path.parent.mkdir(parents=True, exist_ok=True)
    health_path.write_text(json.dumps(health), encoding="utf-8")
    receipt_path = root / "artifacts/scheduler_logs/TEST_TASK_last.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps({
        "task_name": "TEST_TASK", "status": "PASS",
        "finished_at_utc": "2026-09-17T00:01:00+00:00", "api_calls": 0,
    }), encoding="utf-8")
    (receipt_path.parent / "OLD_TASK_last.json").write_text(json.dumps({
        "task_name": "OLD_TASK", "status": "PASS",
        "finished_at": "2026-09-16T23:00:00", "api_calls": 0,
    }), encoding="utf-8")
    return root
