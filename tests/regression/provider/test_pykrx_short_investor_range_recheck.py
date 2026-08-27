import json

import pytest

from scripts.manual.pilot import pykrx_short_investor_range_recheck_support as support
from scripts.manual.pilot.pilot_pykrx_short_investor_range_recheck import _all_possible_probes, _boundary_probes


def body(dates):
    rows = [
        {"TRD_DD": f"{date[:4]}/{date[4:6]}/{date[6:]}",
         "STR_CONST_VAL1": "10", "STR_CONST_VAL2": "2",
         "STR_CONST_VAL3": "3", "STR_CONST_VAL4": "0",
         "STR_CONST_VAL5": "15"}
        for date in dates
    ]
    return json.dumps({"OutBlock_1": rows}).encode()


def test_frozen_plan_is_bounded_and_exact():
    probes = _all_possible_probes()
    assert len(probes) == support.MAX_BUSINESS_REQUESTS == 18
    assert support.MAX_RAW_HTTP_REQUESTS == 26
    assert len(support.RECENT_20) == 20
    assert len(support.RECENT_60) == 60
    assert probes[0].expected_business_data == {
        "strtDd": "20200106", "endDd": "20200110", "inqCondTpCd": "1",
        "mktTpCd": "1", "bld": support.BUSINESS_BLD,
    }


def test_boundary_plan_is_four_exact_sixty_date_scopes():
    probes = _boundary_probes()
    assert len(probes) == 4
    assert len(support.BOUNDARY_60) == 60
    assert support.BOUNDARY_60[0] == "20170522"
    assert support.BOUNDARY_60[-1] == "20170814"
    assert {(probe.market, probe.metric) for probe in probes} == {
        ("KOSPI", "volume"), ("KOSPI", "trading_value"),
        ("KOSDAQ", "volume"), ("KOSDAQ", "trading_value"),
    }


def test_known_positive_exact_multirow_passes_with_descending_raw():
    probe = support.make_probe("known", "KOSPI", "volume", support.KNOWN_DATES, "known_positive")
    result = support.classify(probe, body(reversed(support.KNOWN_DATES)))
    assert result["classification"] == "REGRESSION_PASS_MULTIROW"
    assert result["source_rows"] == 5
    assert result["raw_order"] == "descending"
    assert not result["missing_dates"]


def test_range_end_only_is_not_success():
    probe = support.make_probe("known", "KOSPI", "trading_value", support.KNOWN_DATES, "known_positive")
    result = support.classify(probe, body([support.KNOWN_DATES[-1]]))
    assert result["classification"] == "REGRESSION_FAIL_RANGE_COLLAPSE"
    assert len(result["missing_dates"]) == 4


def test_valid_empty_is_distinct_from_source_error():
    probe = support.make_probe("old", "KOSPI", "volume", support.KNOWN_DATES, "historical")
    assert support.classify(probe, b'{"OutBlock_1":[]}')["classification"] == "VALID_EMPTY"
    with pytest.raises(support.RecheckStopped, match="SOURCE_ERROR"):
        support.classify(probe, b'{"_error_code":"bad"}')
    placeholder = {"TRD_DD": "", **{f"STR_CONST_VAL{i}": "0" for i in range(1, 6)}}
    encoded = json.dumps({"OutBlock_1": [placeholder]}).encode()
    assert support.classify(probe, encoded)["classification"] == "VALID_EMPTY_PLACEHOLDER"


def test_total_relationship_and_negative_values_fail_closed():
    probe = support.make_probe("known", "KOSPI", "volume", support.KNOWN_DATES, "known_positive")
    bad = json.loads(body([support.KNOWN_DATES[0]]))
    bad["OutBlock_1"][0]["STR_CONST_VAL5"] = "14"
    with pytest.raises(support.RecheckStopped, match="INVESTOR_TOTAL_MISMATCH"):
        support.classify(probe, json.dumps(bad).encode())
    bad["OutBlock_1"][0]["STR_CONST_VAL1"] = "-1"
    with pytest.raises(support.RecheckStopped, match="INVALID_NONNEGATIVE_INTEGER"):
        support.classify(probe, json.dumps(bad).encode())
