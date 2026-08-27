from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest

from stock_data.providers.kbsec.account import (
    KBSecAccountContractError,
    normalize_domestic_balance_payload,
)


def domestic_balance_payload() -> dict:
    return {
        "dataHeader": {
            "resultCode": "200",
            "processCode": "0011",
            "processTime": "20260622162350500",
        },
        "dataBody": {
            "grid_cnt1": "0001",
            "tl_data_cnt": "0001",
            "nt_asts_val_amt": "000000000001066450",
            "scrts_nt_val_amt": "000000000000426500",
            "byng_amt_sum": "000000000000360050",
            "val_amt_sum": "000000000000426500",
            "val_pl_sum": "000000000000066450",
            "Record1": [{
                "is_cd": "A005930",
                "is_nm": "Fixture Equity",
                "clsf": "현금",
                "ec_q_p6": "000000001.000000",
                "ordr_psbl_q_p6": "000000001.000000",
                "byng_avr_prc": "000000360050.00",
                "now_prc": "000000426500.00",
                "byng_amt": "000000000000360050",
                "val_amt": "000000000000426500",
                "val_pl": "000000000000066450",
            }],
        },
    }


def test_domestic_balance_projection_is_identifier_free_and_reconciled():
    payload = domestic_balance_payload()
    payload["dataBody"]["account_number"] = "fixture-private-number"

    normalized = normalize_domestic_balance_payload(
        payload, collected_at=datetime(2026, 8, 20, tzinfo=timezone.utc)
    )

    assert normalized["provider"] == "kbsec_open_api"
    assert normalized["source_operation"] == "SSQM2952"
    assert normalized["registered_holder_scope"] == "SELF"
    assert normalized["economic_attribution_scope"] == "SELF"
    assert normalized["total_assets"] == "1066450"
    assert normalized["securities_value"] == "426500"
    assert normalized["cash_balance"] is None
    assert normalized["buying_power"] is None
    assert normalized["realized_pnl"] is None
    assert normalized["positions"][0]["quantity"] == "1.000000"
    assert normalized["positions"][0]["market_value"] == "426500"
    rendered = json.dumps(normalized)
    assert "fixture-private-number" not in rendered
    assert "account_number" not in rendered


@pytest.mark.parametrize("mutation", ["partial", "count", "aggregate", "operation"])
def test_domestic_balance_projection_fails_closed(mutation):
    payload = deepcopy(domestic_balance_payload())
    if mutation == "partial":
        del payload["dataBody"]["Record1"][0]["val_amt"]
    elif mutation == "count":
        payload["dataBody"]["grid_cnt1"] = "0002"
    elif mutation == "aggregate":
        payload["dataBody"]["val_amt_sum"] = "000000000000426501"
    else:
        payload["dataHeader"]["processCode"] = "0223"

    with pytest.raises(KBSecAccountContractError):
        normalize_domestic_balance_payload(
            payload, collected_at=datetime(2026, 8, 20, tzinfo=timezone.utc)
        )


def test_overseas_sample_shape_is_not_silently_treated_as_domestic_holdings():
    payload = {
        "dataHeader": {
            "resultCode": "200",
            "processCode": "0223",
            "processTime": "20260622171110148",
        },
        "dataBody": {"Record1": [{"tfnd": "malformed fixed-width row"}]},
    }

    with pytest.raises(KBSecAccountContractError):
        normalize_domestic_balance_payload(
            payload, collected_at=datetime(2026, 8, 20, tzinfo=timezone.utc)
        )
