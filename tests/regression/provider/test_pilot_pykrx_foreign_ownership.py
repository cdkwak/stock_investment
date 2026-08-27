from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from scripts.manual.pilot import pilot_pykrx_foreign_ownership as runner
from scripts.manual.pilot import pykrx_foreign_ownership_pilot_support as support


def test_fixed_matrix_is_full_market_primary_and_symbol_qa_bounded():
    assert support.PYKRX_VERSION == "1.2.8"
    assert len(support.PROBE_MATRIX) == support.MAX_BUSINESS_REQUESTS == 20
    assert support.MAX_RAW_HTTP_REQUESTS == 28
    assert [item.operation for item in support.PROBE_MATRIX].count("market") == 9
    assert [item.operation for item in support.PROBE_MATRIX].count("symbol") == 11
    assert all(item.bld.endswith("03701") for item in support.PROBE_MATRIX[:9])
    assert all(item.bld.endswith("03702") for item in support.PROBE_MATRIX[9:])
    assert support.PROBE_MATRIX[-2].scope["symbol"] == "003410"
    assert support.PROBE_MATRIX[-1].scope["symbol"] == "030270"


def test_exact_business_payloads_are_frozen():
    market = support.PROBE_MATRIX[0]
    assert support.expected_business_payload(market) == {
        "searchType": "1", "mktId": "STK", "trdDd": "20260814",
        "isuLmtRto": "0", "bld": market.bld,
    }
    symbol = support.PROBE_MATRIX[9]
    assert support.expected_business_payload(symbol) == {
        "searchType": "2", "strtDd": "20260102", "endDd": "20260814",
        "isuCd": "KR7005930003", "bld": symbol.bld,
    }


def test_cli_requires_explicit_live_confirmation(monkeypatch):
    monkeypatch.setattr("sys.argv", ["pilot_pykrx_foreign_ownership.py"])
    assert runner.main() == 2


def test_landing_only_no_contract_normalized_retry_or_parallelism():
    source = inspect.getsource(runner.run_pilot)
    assert "DatasetContract" not in source
    assert "data/normalized" not in source.lower()
    assert "to_parquet" not in source
    assert '"retry_count": 0' in source
    assert "for attempt" not in source
    assert "Thread" not in source and "asyncio" not in source
    assert runner.D_OWNED_LOCK_PATH.name == "d_owned_krx_short_selling.lock"


def test_retained_c005_raw_symbol_semantics_and_ratio_checks():
    body = b'{"output":[{"TRD_DD":"2026/08/10","TDD_CLSPRC":"230,000","FLUC_TP_CD":"2","CMPPREVDD_PRC":"-1,000","FLUC_RT":"-0.43","LIST_SHRS":"5,846,278,608","FORN_HD_QTY":"2,725,008,527","FORN_SHR_RT":"46.61","FORN_ORD_LMT_QTY":"5,846,278,608","FORN_LMT_EXHST_RT":"46.61"}]}'
    classification, rows, audit = support.classify_business_body(support.PROBE_MATRIX[9], body)
    assert (classification, rows) == ("SUCCESS", 1)
    assert audit["ratio_relationship_violations"] == 0
    assert set(audit["null_counts"]) == set(support.MEASURE_FIELDS)


def test_missing_is_not_coerced_to_zero_and_bad_relationship_is_visible():
    probe = support.PROBE_MATRIX[9]
    row = {field: "" for field in probe.required_fields}
    row.update({"TRD_DD": "2026/08/10", "LIST_SHRS": "100", "FORN_HD_QTY": "50", "FORN_SHR_RT": "10", "FORN_ORD_LMT_QTY": "100", "FORN_LMT_EXHST_RT": "50"})
    _, _, audit = support.classify_business_body(probe, json_bytes(row))
    assert audit["ratio_relationship_violations"] == 1


def json_bytes(row):
    import json
    return json.dumps({"output": [row]}).encode("utf-8")


def test_valid_empty_distinct_from_failure_and_html():
    historical = support.PROBE_MATRIX[18]
    assert support.classify_business_body(historical, b'{"output":[]}')[:2] == ("COVERAGE_EMPTY", 0)
    with pytest.raises(support.PilotStopped, match="ANOMALOUS_EMPTY"):
        support.classify_business_body(support.PROBE_MATRIX[0], b'{"output":[] }')
    with pytest.raises(support.PilotStopped, match="HTML_OR_RESTRICTION"):
        support.classify_business_body(historical, b"<html>blocked</html>")


def test_active_shared_lock_fails_closed_without_touching_it(tmp_path: Path):
    lock = tmp_path / "d_owned_krx_short_selling.lock"
    lock.write_text("active", encoding="utf-8")
    before = lock.read_bytes()
    with pytest.raises(support.PilotLocked):
        with support.shared_d_owned_krx_lock(lock, run_id="c010"):
            pass
    assert lock.read_bytes() == before
