import pytest

from scripts.manual.pilot.pilot_ls_t8428_t1633_followup import (
    HISTORY_DATES, MAX_DATA_CALLS, MAX_T1633_CALLS, MAX_T8428_PAGES,
    audit_t8428_pages, date_profile, frozen_plan, parse_rows, plan_digest,
    program_identity_residuals, t1633_scopes,
)


def test_frozen_plan_is_exact_and_bounded():
    scopes = t1633_scopes()
    assert len(scopes) == MAX_T1633_CALLS == 12
    assert MAX_T8428_PAGES == 5 and MAX_DATA_CALLS == 17
    assert len([x for x in scopes if x["measure"] == "1"]) == 2
    assert {(x["market_name"], x["date"]) for x in scopes if x["measure"] == "0"} == {
        (market, date) for market in ("kospi", "kosdaq") for date in HISTORY_DATES
    }
    assert len(plan_digest(frozen_plan())) == 64


def test_valid_empty_is_distinct_from_shape_failure():
    rows, empty = parse_rows({"rsp_cd": "00000", "rsp_msg": "no data"}, "t1633OutBlock1")
    assert rows == [] and empty
    with pytest.raises(ValueError, match="shape"):
        parse_rows({"rsp_cd": "00000", "unexpected": []}, "t1633OutBlock1")
    with pytest.raises(ValueError, match="code"):
        parse_rows({"rsp_cd": "99999"}, "t1633OutBlock1")


def test_date_profile_requires_unique_strict_source_dates():
    assert date_profile([{"date": "20260814"}, {"date": "20260813"}]) == {
        "rows": 2, "unique_dates": 2, "date_min": "20260813", "date_max": "20260814", "strict_descending": True,
    }
    duplicate = date_profile([{"date": "20260814"}, {"date": "20260814"}])
    assert duplicate["unique_dates"] == 1 and not duplicate["strict_descending"]
    with pytest.raises(ValueError, match="date shape"):
        date_profile([{"date": "bad"}])


def test_pagination_audit_accepts_only_identical_boundary_overlap():
    row2 = {"date": "20260813", "value": 2}
    audit = audit_t8428_pages([[{"date": "20260814", "value": 1}, row2], [row2, {"date": "20260812", "value": 3}]])
    assert audit["physical_rows"] == 4 and audit["unique_dates"] == 3
    assert audit["boundary_overlaps"] == ["20260813"] and audit["conflicting_overlaps"] == []
    conflict = audit_t8428_pages([[row2], [{"date": "20260813", "value": 99}]])
    assert conflict["conflicting_overlaps"] == ["20260813"]
    with pytest.raises(ValueError, match="reverse chronological"):
        audit_t8428_pages([[{"date": "20260813"}, {"date": "20260814"}]])


def test_program_identity_residuals_preserve_source_rounding():
    row = {"tot1": 100, "tot2": 80, "tot3": 20, "cha1": 30, "cha2": 35, "cha3": -5, "bcha1": 70, "bcha2": 45, "bcha3": 25}
    assert program_identity_residuals(row) == {
        "total_buy_minus_sell_minus_net": 0,
        "arbitrage_buy_minus_sell_minus_net": 0,
        "non_arbitrage_buy_minus_sell_minus_net": 0,
        "components_minus_total_net": 0,
    }
