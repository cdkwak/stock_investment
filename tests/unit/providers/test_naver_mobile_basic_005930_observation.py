from datetime import datetime, timezone

from stock_data.providers.naver_mobile_basic_005930_observation import naver_mobile_basic_005930_quote


def test_reuses_exact_mobile_domestic_contract_for_005930() -> None:
    source = naver_mobile_basic_005930_quote({
        "itemCode": "005930", "closePrice": "71,200", "marketStatus": "OPEN", "localTradedAt": "2026-08-21T13:26:15+09:00", "delayTime": 0,
        "stockExchangeType": {"code": "KS", "zoneId": "Asia/Seoul", "nationType": "KOR", "stockType": "domestic", "delayTime": 0, "startTime": "0900", "endTime": "1530"},
    }, retrieved_at=datetime(2026, 8, 21, 4, 26, 45, tzinfo=timezone.utc))
    assert source.value.identity.symbol == "005930"
    assert source.value.unit == "KRW per share"
    assert source.value.value == 71200.0
