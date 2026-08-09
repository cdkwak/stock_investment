import json

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
