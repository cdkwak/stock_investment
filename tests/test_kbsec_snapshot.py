from datetime import datetime, timezone
import json
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import requests

from stock_data.contracts.kbsec_snapshot import KBSEC_SNAPSHOT_CONTRACTS
from stock_data.pipelines.kbsec_snapshot import collect_kb_market_summary
from stock_data.providers.kbsec.client import KBSecResponse
from stock_data.providers.kbsec.client import KBSecBusinessError, KBSecClient, KBSecHTTPError
from stock_data.providers.kbsec.market_summary import normalize_market_summary


FIXTURE = Path(__file__).parent / "fixtures/kbsec_ivsa0070.json"


def response():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return KBSecResponse("200", "0024", payload["dataBody"], payload)


def test_contracts_are_separate_provisional_normalized_snapshots():
    assert len(KBSEC_SNAPSHOT_CONTRACTS) == 7
    assert all(c.layer == "normalized" and c.source == "kb_securities_open_api" for c in KBSEC_SNAPSHOT_CONTRACTS)
    assert not any(c.name in {"kr_equity_price_daily", "kr_equity_market_cap_daily", "kr_equity_universe_daily"} for c in KBSEC_SNAPSHOT_CONTRACTS)
    for contract in KBSEC_SNAPSHOT_CONTRACTS:
        assert {"snapshot_date", "market_date", "collected_at", "source", "source_operation", "is_provisional"} <= set(contract.column_names)


def test_fixture_normalizes_all_seven_datasets_and_preserves_zero():
    frames = normalize_market_summary(response(), collected_at=datetime(2026, 8, 11, 1, 31, tzinfo=timezone.utc))
    assert set(frames) == {c.name for c in KBSEC_SNAPSHOT_CONTRACTS}
    assert frames["kb_market_breadth_snapshot"].set_index("market").loc["KOSPI", "advancing"] == 500
    assert frames["kb_program_trading_snapshot"].iloc[0]["non_arbitrage_net_buy"] == -2345
    assert frames["kb_investor_flow_snapshot"].iloc[0]["star_futures_net_buy"] == 0
    assert all(frame["is_provisional"].all() for frame in frames.values())


def test_pipeline_writes_lossless_landing_and_separate_normalized_parquet(tmp_path):
    client = Mock(); client.market_summary.return_value = response()
    counts = collect_kb_market_summary(tmp_path, client=client, collected_at=datetime(2026, 8, 11, 1, 31, tzinfo=timezone.utc))
    assert all(counts.values())
    landing = next((tmp_path / "data/landing/kbsec/IVSA0070").glob("*.json"))
    landed = json.loads(landing.read_text(encoding="utf-8"))
    assert landed["source"] == "kb_securities_open_api" and landed["operation"] == "IVSA0070"
    assert landed["raw_response"] == response().raw_payload
    for contract in KBSEC_SNAPSHOT_CONTRACTS:
        stored = list((tmp_path / "data/normalized" / contract.name).rglob("data.parquet"))
        assert stored and len(pd.read_parquet(stored[0])) > 0
    assert not (tmp_path / "data/normalized/kr_equity_price_daily").exists()


def test_client_uses_nested_read_only_request_and_process_code_is_diagnostic():
    payload = response().raw_payload
    http = Mock(status_code=200); http.json.return_value = payload
    session = Mock(); session.post.return_value = http
    client = KBSecClient(base_url="https://example.test", app_key="key", app_secret="secret", session=session)
    client.access_token = Mock(return_value="token")
    result = client.market_summary()
    assert result.process_code == "0024"
    assert result.result_message == "success" and result.process_message == "done"
    call = session.post.call_args
    assert call.args[0] == "https://example.test/api/v1/ivsa0070"
    assert call.kwargs["json"] == {"dataHeader": {"ipAddr": "127.0.0.1", "macAddr": "00:00:00:00:00:00"}, "dataBody": {}}


def test_client_rejects_business_failure_without_exposing_payload():
    payload = response().raw_payload.copy(); payload["dataHeader"] = {
        "resultCode": "400", "resultMessage": "rejected",
        "processCode": "1001", "processMessage": "invalid request",
    }
    http = Mock(status_code=200); http.json.return_value = payload
    session = Mock(); session.post.return_value = http
    client = KBSecClient(base_url="https://example.test", app_key="key", app_secret="secret", session=session)
    client.access_token = Mock(return_value="token")
    try: client.market_summary()
    except KBSecBusinessError as error:
        assert str(error) == "KB market summary rejected"
        assert error.http_status == 200 and error.result_code == "400"
        assert error.result_message == "rejected" and error.process_message == "invalid request"
    else: raise AssertionError("business failure was accepted")


def test_token_business_failure_keeps_only_safe_diagnostics():
    payload = {"dataHeader": {"resultCode": "400", "resultMessage": "bad secret",
               "processCode": "AUTH01", "processMessage": "key rejected"}, "dataBody": {}}
    http = Mock(status_code=200); http.json.return_value = payload
    session = Mock(); session.post.return_value = http
    client = KBSecClient(base_url="https://example.test", app_key="key", app_secret="secret", session=session)
    try: client.access_token()
    except KBSecBusinessError as error:
        assert error.http_status == 200 and error.result_code == "400" and error.process_code == "AUTH01"
        assert "secret" not in error.result_message and "key" not in error.process_message
    else: raise AssertionError("token business failure was accepted")


class RecordingAdapter(requests.adapters.BaseAdapter):
    def __init__(self): self.requests = []
    def send(self, request, **kwargs):
        self.requests.append(request)
        reply = requests.Response(); reply.status_code = 200; reply.request = request
        reply.headers["Content-Type"] = "application/json"
        reply._content = json.dumps({
            "dataHeader": {"resultCode": "200", "processCode": "0000"},
            "dataBody": {"access_token": "returned-token", "token_type": "Bearer",
                         "expires_in": 86400},
        }).encode()
        return reply
    def close(self): pass


def test_prepared_token_request_has_exact_serialized_keys_and_endpoint():
    adapter = RecordingAdapter(); session = requests.Session(); session.mount("https://", adapter)
    client = KBSecClient(base_url="https://developer.kbsec.com:32484", app_key="unit-app-key",
                         app_secret="unit-app-secret", session=session)
    assert client.access_token() == "returned-token"
    assert len(adapter.requests) == 1
    prepared = adapter.requests[0]
    serialized = json.loads(prepared.body)
    assert prepared.url == "https://developer.kbsec.com:32484/oauth2/token"
    assert set(serialized) == {"dataHeader", "dataBody"}
    assert set(serialized["dataHeader"]) == {"ipAddr", "macAddr"}
    assert set(serialized["dataBody"]) == {"grantType", "appKey", "appSecret"}
    assert serialized["dataBody"]["grantType"] == "client_credentials"
    assert "ordrCtnMtrCsntF" not in prepared.body.decode()


def test_token_and_ivsa0070_payloads_are_isolated():
    http = Mock(status_code=200); http.json.return_value = response().raw_payload
    session = Mock(); session.post.return_value = http
    client = KBSecClient(base_url="https://example.test", app_key="key", app_secret="secret", session=session)
    client.access_token = Mock(return_value="token")
    client.market_summary()
    request = session.post.call_args
    assert request.args[0].endswith("/api/v1/ivsa0070")
    assert request.kwargs["json"]["dataBody"] == {}
    assert not ({"grant_type", "grantType", "appKey", "appSecret", "ordrCtnMtrCsntF"} & set(request.kwargs["json"]["dataBody"]))


def test_http_500_json_keeps_safe_structured_diagnostics():
    payload = {"error": "server_error", "error_description": "failed unit-app-secret",
        "dataHeader": {"resultCode": "500", "resultMessage": "unit-app-key rejected",
                       "processCode": "E500", "processMessage": "Bearer token-value failed"}}
    http = Mock(status_code=500, headers={"Content-Type": "application/json; charset=utf-8"})
    http.json.return_value = payload
    session = Mock(); session.post.return_value = http
    client = KBSecClient(base_url="https://example.test", app_key="unit-app-key",
                         app_secret="unit-app-secret", session=session)
    try: client.access_token()
    except KBSecHTTPError as error:
        details = error.details
        assert str(error) == "KB API HTTP 500"
        assert details is not None and details.http_status == 500
        assert details.content_type == "application/json" and details.response_is_json is True
        assert details.error_code == "server_error" and details.process_code == "E500"
        exposed = repr(details)
        assert "unit-app-key" not in exposed and "unit-app-secret" not in exposed and "token-value" not in exposed
    else: raise AssertionError("HTTP 500 was accepted")


def test_http_500_text_keeps_only_redacted_short_excerpt():
    body = "failure app-key-value app-secret-value Authorization: Bearer live-token " + "x" * 500
    http = Mock(status_code=500, headers={"Content-Type": "text/plain; charset=utf-8"}, text=body)
    http.json.side_effect = ValueError("not json")
    session = Mock(); session.post.return_value = http
    client = KBSecClient(base_url="https://example.test", app_key="app-key-value",
                         app_secret="app-secret-value", session=session)
    try: client.access_token()
    except KBSecHTTPError as error:
        details = error.details
        assert details is not None and details.response_is_json is False
        assert details.content_type == "text/plain" and details.text_excerpt is not None
        assert len(details.text_excerpt) <= 300
        assert "app-key-value" not in details.text_excerpt
        assert "app-secret-value" not in details.text_excerpt
        assert "live-token" not in details.text_excerpt
    else: raise AssertionError("HTTP 500 was accepted")
