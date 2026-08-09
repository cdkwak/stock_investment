from copy import deepcopy
import json
from pathlib import Path

import pandas as pd
import pytest

from stock_data.contracts.kr_equity import (
    KR_EQUITY_MARKET_CAP_DAILY, KR_EQUITY_PRICE_DAILY,
)
from stock_data.providers.data_go_kr.client import (
    DataGoKrApiError, DataGoKrClient, DataGoKrHttpError,
    _service_key_for_requests_params, write_landing_pages_atomic,
)
from stock_data.providers.data_go_kr.stock_price import (
    STOCK_PRICE_ENDPOINT, normalize_stock_price_items,
)
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic
from stock_data.validation.kr_equity import (
    EquityValidationError, validate_equity_market_cap, validate_equity_price,
)


FIXTURE = Path(__file__).parent / "fixtures" / "data_go_kr_stock_price_page.json"


class Response:
    status_code = 200

    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return deepcopy(self.payload)


class ForbiddenResponse(Response):
    status_code = 403


class Session:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, endpoint, **kwargs):
        self.calls.append((endpoint, kwargs))
        return Response(self.pages[len(self.calls) - 1])


def sample_payload():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def sample_item():
    return sample_payload()["response"]["body"]["items"]["item"][0]


def test_official_sample_maps_to_separate_contracts() -> None:
    result = normalize_stock_price_items([sample_item()])
    assert result.price.to_dict("records") == [{
        "date": "2022-09-19", "market": "KOSDAQ", "symbol": "900110",
        "open": 173, "high": 176, "low": 167, "close": 167,
        "volume": 2788311, "trading_value": 475708047,
    }]
    assert result.market_cap.to_dict("records") == [{
        "date": "2022-09-19", "market": "KOSDAQ", "symbol": "900110",
        "market_cap": 36728652350, "shares_outstanding": 219932050,
    }]


def test_pagination_uses_total_count_and_never_exposes_key() -> None:
    first = sample_payload()
    second = sample_payload()
    first["response"]["body"].update(totalCount=2, pageNo=1)
    second["response"]["body"].update(totalCount=2, pageNo=2)
    second["response"]["body"]["items"]["item"][0].update(
        basDt="20220920", srtnCd="005930", isinCd="KR7005930003",
        itmsNm="삼성전자", mrktCtg="KOSPI",
    )
    session = Session([first, second])
    result = DataGoKrClient(
        endpoint=STOCK_PRICE_ENDPOINT, service_key="fixture-secret", session=session,
    ).fetch_all(filters={"beginBasDt": "20220919"}, num_of_rows=1)
    assert result.total_count == 2 and len(result.items) == 2 and len(session.calls) == 2
    assert [call[1]["params"]["pageNo"] for call in session.calls] == [1, 2]
    assert all(call[1]["params"]["resultType"] == "json" for call in session.calls)


def test_encoded_and_decoded_portal_keys_prepare_identical_params() -> None:
    encoded = "dummy%2Bvalue%3D"
    decoded = "dummy+value="
    assert _service_key_for_requests_params(encoded) == decoded
    assert _service_key_for_requests_params(decoded) == decoded
    sessions = [Session([sample_payload()]), Session([sample_payload()])]
    DataGoKrClient(
        endpoint=STOCK_PRICE_ENDPOINT, service_key=encoded, session=sessions[0],
    ).fetch_page()
    DataGoKrClient(
        endpoint=STOCK_PRICE_ENDPOINT, service_key=decoded, session=sessions[1],
    ).fetch_page()
    assert sessions[0].calls[0][1]["params"]["serviceKey"] == decoded
    assert sessions[1].calls[0][1]["params"]["serviceKey"] == decoded


@pytest.mark.parametrize("code", ["01", "10", "12", "20", "22", "30", "31", "32", "99"])
def test_documented_api_errors_are_explicit_and_not_retried(code: str) -> None:
    payload = {"response": {"header": {
        "resultCode": code, "resultMsg": "failure serviceKey=fixture-secret",
    }, "body": {}}}
    session = Session([payload])
    client = DataGoKrClient(
        endpoint=STOCK_PRICE_ENDPOINT, service_key="fixture-secret", session=session,
    )
    with pytest.raises(DataGoKrApiError) as caught:
        client.fetch_all()
    assert caught.value.code == code
    assert "fixture-secret" not in str(caught.value)
    assert len(session.calls) == 1


def test_http_4xx_reports_only_status_and_is_not_retried() -> None:
    class ForbiddenSession(Session):
        def get(self, endpoint, **kwargs):
            self.calls.append((endpoint, kwargs))
            return ForbiddenResponse({"possibleEcho": "must-not-be-reported"})

    session = ForbiddenSession([])
    client = DataGoKrClient(
        endpoint=STOCK_PRICE_ENDPOINT, service_key="fixture-secret", session=session,
    )
    with pytest.raises(DataGoKrHttpError, match="HTTP 403") as caught:
        client.fetch_page()
    assert "fixture-secret" not in str(caught.value)
    assert "must-not-be-reported" not in str(caught.value)
    assert len(session.calls) == 1


@pytest.mark.parametrize(
    ("code", "category"),
    [("20", "permission"), ("30", "authentication"),
     ("31", "authentication"), ("32", "ip_registration")],
)
def test_http_403_with_official_error_body_is_classified(code: str, category: str) -> None:
    class ClassifiedSession(Session):
        def get(self, endpoint, **kwargs):
            self.calls.append((endpoint, kwargs))
            return ForbiddenResponse({"OpenAPI_ServiceResponse": {"cmmMsgHeader": {
                "returnReasonCode": code,
                "returnAuthMsg": "documented failure",
            }}})

    client = DataGoKrClient(
        endpoint=STOCK_PRICE_ENDPOINT, service_key="fixture-secret",
        session=ClassifiedSession([]),
    )
    with pytest.raises(DataGoKrApiError) as caught:
        client.fetch_page()
    assert caught.value.code == code and caught.value.category == category


def test_valid_empty_page_is_distinct_from_failure() -> None:
    payload = sample_payload()
    payload["response"]["body"].update(totalCount=0, items={})
    result = DataGoKrClient(
        endpoint=STOCK_PRICE_ENDPOINT, service_key="fixture-secret",
        session=Session([payload]),
    ).fetch_all()
    assert result.total_count == 0 and result.items == () and len(result.pages) == 1


def test_valid_zero_is_preserved_and_invalid_ohlc_is_rejected() -> None:
    zero = sample_item()
    zero.update(mkp="0", hipr="0", lopr="0", clpr="0", trqu="0", trPrc="0")
    result = normalize_stock_price_items([zero])
    assert result.price.loc[0, ["open", "high", "low", "close", "volume"]].eq(0).all()
    invalid = sample_item()
    invalid.update(mkp="180", hipr="176")
    with pytest.raises(EquityValidationError, match="OHLC"):
        normalize_stock_price_items([invalid])


def test_duplicate_key_and_unsupported_market_are_rejected() -> None:
    with pytest.raises(EquityValidationError, match="duplicates"):
        normalize_stock_price_items([sample_item(), sample_item()])
    item = sample_item()
    item["mrktCtg"] = "KONEX"
    with pytest.raises(ValueError, match="unsupported"):
        normalize_stock_price_items([item])


def test_landing_and_normalized_parquet_are_atomic(tmp_path: Path) -> None:
    payload = sample_payload()
    landing = tmp_path / "landing" / "20220919.json"
    write_landing_pages_atomic((payload,), landing)
    assert json.loads(landing.read_text(encoding="utf-8")) == [payload]
    assert "serviceKey" not in landing.read_text(encoding="utf-8")

    normalized = normalize_stock_price_items([sample_item()])
    for frame, contract, validator in (
        (normalized.price, KR_EQUITY_PRICE_DAILY, validate_equity_price),
        (normalized.market_cap, KR_EQUITY_MARKET_CAP_DAILY, validate_equity_market_cap),
    ):
        root = tmp_path / contract.name
        write_dataset_atomic(frame, root, contract, validator)
        restored = read_dataset(root, contract, validator)
        pd.testing.assert_frame_equal(restored, frame)
        assert not list(root.rglob("*.tmp"))
