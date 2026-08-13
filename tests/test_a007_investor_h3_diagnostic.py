from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.manual import a007_investor_h3_diagnostic_support as support
from scripts.manual import diagnose_a007_investor_h3 as runner
from scripts.manual.pykrx_short_selling_pilot_support import PilotStopped


FIXTURE_DATES = ("20140106", "20140107", "20160106")


def _row(day: str, total: int = 10):
    return {
        "TRD_DD": f"{day[:4]}/{day[4:6]}/{day[6:]}",
        "STR_CONST_VAL1": str(total), "STR_CONST_VAL2": "0",
        "STR_CONST_VAL3": "0", "STR_CONST_VAL4": "0", "STR_CONST_VAL5": str(total),
    }


def _body(dates=FIXTURE_DATES, total=10):
    return json.dumps({"OutBlock_1": [_row(day, total) for day in dates]}).encode()


def test_h3_plan_is_frozen_to_one_kospi_volume_request():
    assert support.SCOPE == {
        "strtDd": "20140106", "endDd": "20160106", "inqCondTpCd": 1, "mktTpCd": 1,
    }
    assert support.EXPECTED_DATE_COUNT == 494
    assert support.EXPECTED_DATE_SHA256 == "f7e9ea0562ab3b198d690300e8eb4faad015d56b6bea0c4d9919cf599332f28e"
    assert support.MAX_BUSINESS_REQUESTS == 1
    assert support.MAX_RAW_HTTP_REQUESTS == support.EXPECTED_RAW_HTTP_REQUESTS == 6
    assert support.REQUIRE_ZERO_RETRY_AUTH_SESSION is True
    assert runner.D_OWNED_LOCK_PATH.name == "d_owned_krx_short_selling.lock"
    assert runner.LANDING_ROOT.name == "a007_investor_h3"
    assert support.EXPECTED_BUSINESS_DATA == {
        "bld": support.BUSINESS_BLD, "strtDd": "20140106", "endDd": "20160106",
        "inqCondTpCd": "1", "mktTpCd": "1",
    }


def test_expected_494_dates_are_bound_to_exact_retained_sources():
    dates = support.expected_dates(Path("."))
    assert len(dates) == 494
    assert dates[0] == "20140106" and dates[-1] == "20160106"
    digest = hashlib.sha256(("\n".join(dates) + "\n").encode()).hexdigest()
    assert digest == support.EXPECTED_DATE_SHA256


def test_h3_classifies_only_exact_full_set_or_exact_end_date_zero_collapse():
    assert support.classify_response(_body(), FIXTURE_DATES).classification == "H3_FULL_RANGE_AVAILABLE"
    collapsed = support.classify_response(_body(("20160106",), total=0), FIXTURE_DATES)
    assert collapsed.classification == "PRE_AVAILABILITY_COLLAPSE"
    assert collapsed.source_rows == 1 and collapsed.positive_total_dates == 0
    with pytest.raises(PilotStopped, match="AMBIGUOUS_STOP:2/3"):
        support.classify_response(_body(FIXTURE_DATES[-2:]), FIXTURE_DATES)
    with pytest.raises(PilotStopped, match="AMBIGUOUS_STOP:1/3"):
        support.classify_response(_body(("20160106",), total=1), FIXTURE_DATES)


@pytest.mark.parametrize("body,reason", [
    (b"<html>restriction</html>", "HTML_OR_RESTRICTION_RESPONSE"),
    (b'{"OutBlock_1":[]}', "ANOMALOUS_EMPTY_RANGE"),
    (b'{"OutBlock_1":{},"extra":1}', "TOP_LEVEL_SCHEMA_MISMATCH"),
    (json.dumps({"OutBlock_1": [{**_row("20160106", 0), "extra": "x"}]}).encode(), "SCHEMA_MISMATCH"),
    (json.dumps({"OutBlock_1": [{**_row("20160106", 0), "TRD_DD": "bad"}]}).encode(), "INVALID_DATE"),
    (json.dumps({"OutBlock_1": [{**_row("20160106", 0), "STR_CONST_VAL1": "-1"}]}).encode(), "NEGATIVE_VALUE"),
    (json.dumps({"OutBlock_1": [{**_row("20160106", 0), "STR_CONST_VAL1": "1"}]}).encode(), "INVESTOR_TOTAL_MISMATCH"),
])
def test_h3_strict_html_schema_date_and_domain_gates(body, reason):
    with pytest.raises(PilotStopped, match=reason):
        support.classify_response(body, FIXTURE_DATES)


def test_h3_cli_refuses_without_all_confirmations_before_network(monkeypatch):
    called = False

    def forbidden(**kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(runner, "run_diagnostic", forbidden)
    monkeypatch.setattr("sys.argv", ["diagnose_a007_investor_h3.py"])
    assert runner.main() == 2
    assert called is False
