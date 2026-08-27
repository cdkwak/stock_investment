import json

import pytest

from stock_data.contracts.data_v1 import KR_MARKET_LIQUIDITY_DAILY
from stock_data.providers.data_go_kr.data_v1 import normalize_market_liquidity
from stock_data.pipelines.data_v1_collection import collect_date, collect_full_history


class Response:
    status_code = 200
    def __init__(self, payload): self.payload = payload
    def json(self): return self.payload
    def raise_for_status(self): return None


def payload():
    item = {"basDt":"20220928", "invrDpsgAmt":"1", "onbdDrvPrdTrRcAdvAmt":"2",
            "toCstRpchCndBndSlgBal":"3", "brkTrdUcolMny":"4",
            "brkTrdUcolMnyVsOppsTrdAmt":"5", "ucolMnyVsOppsTrdRlImpt":"6"}
    return {"response":{"header":{"resultCode":"00","resultMsg":"NORMAL"},
            "body":{"pageNo":1,"numOfRows":9999,"totalCount":1,"items":{"item":[item]}}}}


def empty_payload():
    return {"response":{"header":{"resultCode":"00","resultMsg":"NORMAL"},
            "body":{"pageNo":1,"numOfRows":9999,"totalCount":0,"items":{}}}}


def test_collection_is_atomic_checkpointed_and_resumable(tmp_path, monkeypatch):
    calls = []
    def fake_get(*args, **kwargs): calls.append(1); return Response(payload())
    monkeypatch.setenv("DATA_GO_KR_SERVICE_KEY", "fixture")
    monkeypatch.setattr("requests.get", fake_get)
    first = collect_full_history(project_root=tmp_path, endpoint="https://example.invalid",
        contract=KR_MARKET_LIQUIDITY_DAILY, normalizer=normalize_market_liquidity)
    second = collect_full_history(project_root=tmp_path, endpoint="https://example.invalid",
        contract=KR_MARKET_LIQUIDITY_DAILY, normalizer=normalize_market_liquidity)
    assert first.rows == second.rows == 1 and first.pages == 1 and second.pages == 0
    assert len(calls) == 1
    state = json.loads((tmp_path/"data/state/kr_market_liquidity_daily.json").read_text())
    assert state["completed_partitions"] == ["full_history"]
    assert list((tmp_path/"data/normalized/kr_market_liquidity_daily").rglob("data.parquet"))


def test_date_collection_resumes_without_duplicate(tmp_path, monkeypatch):
    calls = []
    def fake_get(*args, **kwargs): calls.append(1); return Response(payload())
    monkeypatch.setenv("DATA_GO_KR_SERVICE_KEY", "fixture")
    monkeypatch.setattr("requests.get", fake_get)
    first = collect_date(project_root=tmp_path, endpoint="https://example.invalid",
        contract=KR_MARKET_LIQUIDITY_DAILY, normalizer=normalize_market_liquidity,
        base_date="20220928", max_calls=1)
    second = collect_date(project_root=tmp_path, endpoint="https://example.invalid",
        contract=KR_MARKET_LIQUIDITY_DAILY, normalizer=normalize_market_liquidity,
        base_date="20220928", max_calls=1)
    assert first.rows == second.rows == 1 and len(calls) == 1


def test_exact_date_valid_empty_is_landing_first_and_replays_without_call(tmp_path, monkeypatch):
    calls = []
    def fake_get(*args, **kwargs): calls.append(1); return Response(empty_payload())
    monkeypatch.setenv("DATA_GO_KR_SERVICE_KEY", "fixture")
    monkeypatch.setattr("requests.get", fake_get)
    first = collect_date(project_root=tmp_path, endpoint="https://example.invalid",
        contract=KR_MARKET_LIQUIDITY_DAILY, normalizer=normalize_market_liquidity,
        base_date="20220928", max_calls=1)
    second = collect_date(project_root=tmp_path, endpoint="https://example.invalid",
        contract=KR_MARKET_LIQUIDITY_DAILY, normalizer=normalize_market_liquidity,
        base_date="20220928", max_calls=1)
    landing = tmp_path/"data/landing/data_go_kr/kr_market_liquidity_daily/20220928.json"
    state = json.loads((tmp_path/"data/state/kr_market_liquidity_daily.json").read_text())
    assert first.status == second.status == "VALID_EMPTY"
    assert first.pages == 1 and second.pages == 0 and len(calls) == 1
    assert json.loads(landing.read_text()) == [empty_payload()]
    assert state["valid_empty_partitions"] == ["20220928"]
    assert not (tmp_path/"data/normalized/kr_market_liquidity_daily").exists()


def test_exact_date_malformed_response_retains_landing_but_not_normalized(tmp_path, monkeypatch):
    malformed = payload()
    malformed["response"]["body"]["items"]["item"][0]["basDt"] = "20220927"
    monkeypatch.setenv("DATA_GO_KR_SERVICE_KEY", "fixture")
    monkeypatch.setattr("requests.get", lambda *args, **kwargs: Response(malformed))
    with pytest.raises(ValueError, match="another source date"):
        collect_date(project_root=tmp_path, endpoint="https://example.invalid",
            contract=KR_MARKET_LIQUIDITY_DAILY, normalizer=normalize_market_liquidity,
            base_date="20220928", max_calls=1)
    landing = tmp_path/"data/landing/data_go_kr/kr_market_liquidity_daily/20220928.json"
    state = json.loads((tmp_path/"data/state/kr_market_liquidity_daily.json").read_text())
    assert landing.exists()
    assert state["failed_partitions"] == {"20220928": "ValueError"}
    assert not (tmp_path/"data/normalized/kr_market_liquidity_daily").exists()
