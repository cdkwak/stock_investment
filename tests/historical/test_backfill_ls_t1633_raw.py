import pytest

from scripts.manual.backfill.backfill_ls_t1633_raw import (
    MAX_PAGES_PER_STREAM, STREAMS, audit_rows, fixed_point_boundary, frozen_plan, plan_digest, request_block,
)


def test_plan_is_bounded_raw_only():
    plan = frozen_plan()
    assert len(STREAMS) == 4 and plan["max_data_calls"] == 4 * MAX_PAGES_PER_STREAM == 64
    assert plan["retry_count"] == 0 and plan["normalized_writes"] is False
    assert len(plan_digest(plan)) == 64


def test_request_preserves_market_measure_and_cursor():
    block = request_block(STREAMS[0], "20200102")
    assert block["gubun"] == "0" and block["gubun1"] == "0"
    assert block["date"] == "20200102" and block["fdate"] == "20000101"


def test_row_audit_accepts_source_rounding_only():
    row = {"date": "20260814", "tot1": "100", "tot2": "80", "tot3": "20",
        "cha1": "30", "cha2": "35", "cha3": "-5", "bcha1": "70", "bcha2": "45", "bcha3": "25"}
    result = audit_rows([row])
    assert result["rows"] == 1 and max(result["max_abs_identity_residuals"].values()) == 0
    row["tot3"] = "18"
    with pytest.raises(ValueError, match="arithmetic"):
        audit_rows([row])


def test_fixed_point_boundary_stops_repeated_floor_row():
    assert fixed_point_boundary([{"date": "20010801"}], "20010801", "20010801")
    assert not fixed_point_boundary([{"date": "20010801"}], "20010802", "20010801")
