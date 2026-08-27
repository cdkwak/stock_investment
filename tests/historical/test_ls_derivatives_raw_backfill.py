import pytest

from scripts.manual.backfill.ls_derivatives_raw_backfill import (
    EXPECTED_ROW_KEYS, FROM_DATE, TO_DATE, request_block, scopes, validate_payload,
)


def test_scopes_are_exact_18_product_session_combinations():
    values = scopes()
    assert len(values) == 18
    assert len({(v["asset_code"], v["product_code"], v["requested_session_code"]) for v in values}) == 18
    assert {v["requested_session_code"] for v in values} == {"D", "N", "U"}
    assert {v["from_date"] for v in values} == {FROM_DATE}
    assert {v["to_date"] for v in values} == {TO_DATE}


def test_request_block_preserves_raw_codes():
    scope = scopes()[0]
    assert request_block(scope) == {
        "tm_rng": "D", "fot_clsf_cd": "F", "bsc_asts_id": "K2I",
        "gubun2": "1", "gubun3": "1", "from_date": FROM_DATE, "to_date": TO_DATE,
    }


def test_validate_payload_requires_exact_schema_echo_range_and_unique_dates():
    scope = scopes()[0]
    row = {key: 0 for key in EXPECTED_ROW_KEYS}
    row["date"] = FROM_DATE
    payload = {
        "rsp_cd": "00000",
        "t8462OutBlock": {"tm_rng": "D", "fot_clsf_cd": "F", "bsc_asts_id": "K2I"},
        "t8462OutBlock1": [row],
    }
    assert validate_payload(payload, scope) == [row]
    payload["t8462OutBlock1"] = [row, row]
    with pytest.raises(ValueError, match="duplicated"):
        validate_payload(payload, scope)


def test_validate_payload_rejects_missing_source_field():
    scope = scopes()[0]
    row = {key: 0 for key in EXPECTED_ROW_KEYS}
    row["date"] = FROM_DATE
    row.pop("sa_18")
    payload = {
        "rsp_cd": "00000",
        "t8462OutBlock": {"tm_rng": "D", "fot_clsf_cd": "F", "bsc_asts_id": "K2I"},
        "t8462OutBlock1": [row],
    }
    with pytest.raises(ValueError, match="schema"):
        validate_payload(payload, scope)
