from datetime import date, datetime, time, timedelta, timezone
import json

import pandas as pd
import pytest

import scripts.manual.collect.refresh_global_current as refresh
from stock_data.contracts.global_etf import GLOBAL_ETF_PRICE_DAILY
from stock_data.contracts.registry import CONTRACTS
from stock_data.providers.public_http_capture import capture_public_response
from stock_data.providers.yahoo import ETF_REGISTRY, _epoch, fetch_global_etf
from stock_data.storage.contract_parquet import read_dataset
from stock_data.validation.global_market import validate_global_etf


class Response:
    status_code = 200
    headers = {"Content-Type": "application/json"}

    def __init__(self, payload):
        self._payload = payload
        self.content = json.dumps(payload).encode()
        self.text = self.content.decode()

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


def _payload(days=("2026-08-13", "2026-08-14")):
    stamps = [int(datetime.combine(date.fromisoformat(day), time(16), tzinfo=timezone.utc).timestamp()) for day in days]
    count = len(stamps)
    return {"chart": {"error": None, "result": [{
        "meta": {"symbol": "SOXX", "instrumentType": "ETF", "dataGranularity": "1d",
                 "currency": "USD", "exchangeName": "NMS"},
        "timestamp": stamps,
        "indicators": {
            "quote": [{"open": [200.0, 202.0][:count], "high": [203.0, 205.0][:count],
                       "low": [199.0, 201.0][:count], "close": [202.0, 204.0][:count],
                       "volume": [1000, 1200][:count]}],
            "adjclose": [{"adjclose": [201.5, 203.5][:count]}],
        },
    }]}}


class Backend:
    def __init__(self, payload=None):
        self.payload = payload or _payload()
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return Response(self.payload)


def test_global_etf_contract_and_registry_are_generic():
    assert CONTRACTS["global_etf_price_daily"] is GLOBAL_ETF_PRICE_DAILY
    assert ETF_REGISTRY["SOXX"]["automation_enabled"] is True
    assert ETF_REGISTRY["SOXX"]["validation"] == "global_etf_price_daily_v1"
    assert ETF_REGISTRY["SOXX"]["instrument_type"] == "ETF"
    assert ETF_REGISTRY["SOXX"]["official_cusip"] == "464287523"


def test_fetch_etf_preserves_adjusted_close_identity_and_landing(tmp_path):
    backend = Backend()
    frame = fetch_global_etf(
        "SOXX", date(2026, 8, 13), date(2026, 8, 14), session=backend,
        capture_root=tmp_path, retrieved_at=datetime(2026, 8, 18, tzinfo=timezone.utc),
    )
    validate_global_etf(frame)
    assert frame.symbol.eq("SOXX").all() and frame.source_ticker.eq("SOXX").all()
    assert frame.adjusted_close.tolist() == [201.5, 203.5]
    assert frame.volume.tolist() == [1000, 1200]
    record = json.loads(next(tmp_path.rglob("call.json")).read_text())
    assert record["operation"] == "etf_chart_daily"
    assert record["request_parameters"]["includeAdjustedClose"] == "true"


def test_etf_validation_rejects_duplicate_and_wrong_instrument(tmp_path):
    frame = fetch_global_etf(
        "SOXX", date(2026, 8, 13), date(2026, 8, 14), session=Backend(),
        retrieved_at=datetime(2026, 8, 18, tzinfo=timezone.utc),
    )
    with pytest.raises(ValueError, match="duplicate"):
        validate_global_etf(pd.concat([frame, frame.iloc[[0]]], ignore_index=True))
    payload = _payload(); payload["chart"]["result"][0]["meta"]["instrumentType"] = "INDEX"
    with pytest.raises(RuntimeError, match="identity"):
        fetch_global_etf("SOXX", date(2026, 8, 13), date(2026, 8, 14), session=Backend(payload))


def test_initial_etf_vertical_slice_is_capture_first_promotable_and_idempotent(tmp_path, monkeypatch):
    root = tmp_path / "project"; root.mkdir()

    def fake_fetch(symbol, start, end, *, session, capture_root):
        params = {"period1": _epoch(start), "period2": _epoch(end + timedelta(days=1)),
                  "interval": "1d", "events": "history", "includeAdjustedClose": "true"}
        response = session.get("https://query1.finance.yahoo.com/v8/finance/chart/SOXX", params=params)
        capture_public_response(
            root=capture_root, provider="yahoo", operation="etf_chart_daily",
            request_url="https://query1.finance.yahoo.com/v8/finance/chart/SOXX",
            request_parameters={"symbol": symbol, **params}, response=response,
        )
        return fetch_global_etf(
            symbol, start, end, session=Backend(_payload((end.isoformat(),))),
            retrieved_at=datetime(2026, 8, 18, tzinfo=timezone.utc),
        ).tail(1).reset_index(drop=True)

    monkeypatch.setattr(refresh, "fetch_global_etf", fake_fetch)
    result = refresh.prepare_phase(
        root, "yahoo_etf", start=date(2026, 8, 14), end=date(2026, 8, 14), session=Backend(),
    )
    assert result["http_calls"] == 1 and result["normalized_mutation"] is False
    assert not (root / "data/normalized/global_etf_price_daily").exists()
    checkpoint = root / "data/state/global_current_refresh" / result["run_id"] / "checkpoint.json"
    promoted = refresh.promote_phase(root, checkpoint, approval_digest=result["approval_digest"])
    assert promoted["status"] == "PROMOTED"
    repeated = refresh.promote_phase(root, checkpoint, approval_digest=result["approval_digest"])
    assert repeated["status"] == "PROMOTED"
    stored = read_dataset(
        root / "data/normalized/global_etf_price_daily",
        GLOBAL_ETF_PRICE_DAILY, validate_global_etf,
    )
    assert stored.symbol.tolist() == ["SOXX"] and stored.date.tolist() == ["2026-08-14"]

    class NoNetwork:
        def get(self, *args, **kwargs):
            raise AssertionError("an already-retained ETF window must not call the provider")

    operation_rerun = refresh.prepare_phase(
        root, "yahoo_etf", start=date(2026, 8, 14), end=date(2026, 8, 14),
        session=NoNetwork(),
    )
    assert operation_rerun["status"] == "NOOP_IDEMPOTENT"
    assert operation_rerun["http_calls"] == 0
    assert operation_rerun["normalized_mutation"] is False

    # Min/max coverage alone must not turn an absent in-range session into a no-op.
    with pytest.raises(AssertionError, match="must not call"):
        refresh.prepare_phase(
            root, "yahoo_etf", start=date(2026, 8, 13), end=date(2026, 8, 13),
            session=NoNetwork(),
        )
