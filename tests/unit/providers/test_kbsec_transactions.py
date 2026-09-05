from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from stock_data.providers.kbsec.client import KBSecResponse
from stock_data.providers.kbsec.transactions import (
    KBSecTransactionContractError,
    KBSecTransactionsClient,
    classify_transaction,
    normalize_landing_transaction_row,
    project_transaction_page_for_landing,
    transaction_request_body,
)


def _source_row(summary: str, *, code: str = "01", amount: str = "12,345") -> dict:
    return {
        "dl_dt": "20260904",
        "dl_typ_cd": code,
        "smry_typ_cd": "007",
        "smry_nm": summary,
        "dl_amt": amount,
        "incm_tx": "000000000000003",
        "rsdnt_tx": "000000000000002",
        "cprty_ac_nm": "fixture counterparty must be removed",
        "account_number": "123456789012",
    }


def _response(rows: list[dict], *, next_key: str = "") -> KBSecResponse:
    body = {
        "grid_cnt1": f"{len(rows):04d}",
        "nxt_key": next_key,
        "Record1": rows,
    }
    raw = {
        "dataHeader": {"resultCode": "200", "processCode": "0011"},
        "dataBody": body,
    }
    return KBSecResponse("200", "0011", body, raw)


def test_client_uses_exact_read_only_swqa2301_body(monkeypatch) -> None:
    client = KBSecTransactionsClient(
        base_url="https://kb.example", app_key="fixture", app_secret="fixture",
    )
    monkeypatch.setattr(client, "access_token", lambda: "memory-only-token")
    captured = {}

    def post(path, *, headers, body):
        captured.update(path=path, headers=headers, body=body)
        return _response([]).raw_payload, 200

    monkeypatch.setattr(client, "_post", post)

    result = client.transaction_history_page(
        date(2026, 9, 1), date(2026, 9, 4), next_key="page-two",
    )

    assert result.data_body["Record1"] == []
    assert captured["path"] == "/api/v1/swqa2301"
    assert captured["body"] == transaction_request_body(
        date(2026, 9, 1), date(2026, 9, 4), next_key="page-two",
    )
    assert captured["body"]["strt_dt"] == "20260901"
    assert captured["body"]["end_dt"] == "20260904"
    assert captured["body"]["inq_clsf"] == "1"


@pytest.mark.parametrize(
    ("summary", "code", "direction", "category"),
    [
        ("오픈뱅킹 입금", "01", "IN", "DEPOSIT"),
        ("전자금융입금", "01", "IN", "DEPOSIT"),
        ("이체 입금", "01", "IN", "DEPOSIT"),
        ("송금 출금", "02", "OUT", "WITHDRAWAL"),
        ("이체 출금", "02", "OUT", "WITHDRAWAL"),
        ("현금 배당", "01", "IN", "DIVIDEND"),
        ("ETF 분배금", "01", "IN", "DIVIDEND"),
        ("원천징수 세금", "02", "OUT", "TAX"),
        ("이체 수수료", "02", "OUT", "FEE"),
        ("주식장내매수", "02", "OUT", "OTHER"),
        ("주식장내매도", "01", "IN", "OTHER"),
        ("예탁금이용료 입금", "01", "IN", "OTHER"),
        ("미확인거래", "01", "IN", "OTHER"),
    ],
)
def test_classification_is_bounded_and_unknown_stays_other(
    summary, code, direction, category,
) -> None:
    actual = classify_transaction(summary, code, "007")
    assert (actual[0].value, actual[1].value) == (direction, category)


def test_landing_projection_removes_identifiers_before_normalization() -> None:
    projected = project_transaction_page_for_landing(
        _response([_source_row("현금 배당")]),
        retrieved_at=datetime(2026, 9, 5, tzinfo=timezone.utc),
        page_number=1,
    )
    rendered = str(projected)

    assert "fixture counterparty" not in rendered
    assert "account_number" not in rendered
    assert "cprty_ac_nm" not in rendered
    normalized = normalize_landing_transaction_row(projected["rows"][0])
    assert normalized["category"] == "DIVIDEND"
    assert normalized["amount_krw"] == 12345
    assert normalized["tax_krw"] == 5
    assert len(normalized["raw_row_sha256"]) == 64


def test_unknown_direction_fails_closed_instead_of_guessing() -> None:
    with pytest.raises(KBSecTransactionContractError, match="without guessing"):
        classify_transaction("새로운 미확인 요약", "77", "999")
