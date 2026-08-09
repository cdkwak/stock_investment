import json
from pathlib import Path

from stock_data.pipelines.backfill_state import BackfillState
from stock_data.pipelines.equity_batch_collection import collect_equity_batch
from stock_data.providers.data_go_kr.client import DataGoKrResult, write_landing_pages_atomic


def _page(date, *, universe=False):
    item = ({
        "basDt": date, "srtnCd": "A005930", "isinCd": "KR7005930003",
        "itmsNm": "Samsung", "mrktCtg": "KOSPI", "corpNm": "Samsung Corp",
        "crno": "1234567890123",
    } if universe else {
        "basDt": date, "srtnCd": "005930", "isinCd": "KR7005930003",
        "itmsNm": "Samsung", "mrktCtg": "KOSPI", "mkp": "100",
        "hipr": "110", "lopr": "90", "clpr": "105", "trqu": "10",
        "trPrc": "1050", "lstgStCnt": "1000", "mrktTotAmt": "105000",
        "vs": "5", "fltRt": "5.0",
    })
    return {"response": {"header": {"resultCode": "00", "resultMsg": "OK"},
                         "body": {"items": {"item": [item]}, "pageNo": 1,
                                  "numOfRows": 9999, "totalCount": 1}}}


class _Client:
    calls = []

    def __init__(self, *, endpoint, **kwargs):
        self.universe = "GetKrxListedInfoService" in endpoint

    def fetch_all(self, *, filters, **kwargs):
        date = filters["basDt"]
        self.calls.append((self.universe, date))
        page = _page(date, universe=self.universe)
        item = page["response"]["body"]["items"]["item"][0]
        return DataGoKrResult((item,), (page,), 1)


def test_multi_date_batch_atomic_resume_and_staged_reuse(tmp_path: Path, monkeypatch):
    import stock_data.pipelines.equity_batch_collection as module
    monkeypatch.setattr(module, "DataGoKrClient", _Client)
    monkeypatch.setattr(module, "service_key_from_environment", lambda root: "fixture")
    _Client.calls = []
    dates = ["20200102", "20200103"]

    staged_path = tmp_path / "data/landing/data_go_kr/stock_price/20200102.json"
    write_landing_pages_atomic((_page("20200102"),), staged_path)
    state = BackfillState.load(
        tmp_path / "data/state/kr_equity_price_cap_daily.json", "kr_equity_price_cap_daily")
    state.mark_staged("20200102")

    result = collect_equity_batch(tmp_path, dates, chunk_size=2, sleep_fn=lambda _: None)
    assert result == {"equity": 1, "universe": 2}
    assert (False, "20200102") not in _Client.calls

    price_state = BackfillState.load(
        tmp_path / "data/state/kr_equity_price_cap_daily.json", "kr_equity_price_cap_daily")
    universe_state = BackfillState.load(
        tmp_path / "data/state/kr_equity_universe_daily.json", "kr_equity_universe_daily")
    assert price_state.completed_partitions == set(dates)
    assert universe_state.completed_partitions == set(dates)
    assert not price_state.staged_partitions and not universe_state.staged_partitions

    _Client.calls = []
    assert collect_equity_batch(tmp_path, dates, chunk_size=2, sleep_fn=lambda _: None) == {
        "equity": 0, "universe": 0}
    assert _Client.calls == []
