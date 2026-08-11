from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest

from scripts.manual import pilot_pykrx_etf as runner
from scripts.manual import pykrx_etf_pilot_support as support


def test_fixed_matrix_is_full_market_primary_and_symbol_qa_bounded():
    assert support.PYKRX_VERSION == "1.2.8"
    assert len(support.PROBE_MATRIX) == support.MAX_BUSINESS_REQUESTS == 5
    assert support.MAX_RAW_HTTP_REQUESTS == 13
    assert [item.operation for item in support.PROBE_MATRIX].count("market") == 2
    assert [item.operation for item in support.PROBE_MATRIX].count("symbol") == 3
    assert all(item.bld.endswith("04301") for item in support.PROBE_MATRIX[:2])
    assert all(item.bld.endswith("04501") for item in support.PROBE_MATRIX[2:])
    assert support.PROBE_MATRIX[1].scope["date"] == "20080102"
    assert all(item.scope.get("symbol", "069500") == "069500" for item in support.PROBE_MATRIX)


def test_cli_requires_explicit_live_confirmation(monkeypatch):
    monkeypatch.setattr("sys.argv", ["pilot_pykrx_etf.py"])
    assert runner.main() == 2


def test_runner_is_landing_only_and_excludes_current_master_and_tracking_calls():
    source = inspect.getsource(runner.run_pilot)
    assert "DatasetContract" not in source
    assert "data/normalized" not in source.lower()
    assert "to_parquet" not in source
    assert '"retry_count": 0' in source
    assert "Thread" not in source and "asyncio" not in source
    assert '"MDCSTAT04601", "MDCSTAT05901", "MDCSTAT06001"' in source
    assert runner.D_OWNED_LOCK_PATH.name == "d_owned_krx_short_selling.lock"


def test_retained_c005_full_market_row_schema_and_ohlc_audit():
    row = {
        "ISU_SRT_CD":"069500", "ISU_CD":"KR7069500007", "SECUGRP_ID":"EF", "ISU_ABBRV":"KODEX 200",
        "TDD_CLSPRC":"98,265", "CMPPREVDD_PRC":"175", "FLUC_TP_CD":"1", "FLUC_RT":"0.18", "NAV":"98,250.40",
        "TDD_OPNPRC":"98,835", "TDD_HGPRC":"100,345", "TDD_LWPRC":"97,260", "ACC_TRDVOL":"13,128,894",
        "ACC_TRDVAL":"1,293,792,635,144", "MKTCAP":"22,522,338,000,000", "INVSTASST_NETASST_TOTAMT":"22,779,356,208,586",
        "LIST_SHRS":"229,200,000", "IDX_IND_NM":"肄붿뒪??200", "OBJ_STKPRC_IDX":"977.84", "CMPPREVDD_IDX":"3.11",
        "FLUC_TP_CD1":"1", "FLUC_RT1":"0.32",
    }
    classification, rows, audit = support.classify_business_body(support.PROBE_MATRIX[0], json.dumps({"output":[row]}, ensure_ascii=False).encode())
    assert (classification, rows) == ("SUCCESS", 1)
    assert audit["ohlc_violations"] == 0
    assert audit["negative_count_violations"] == 0


def test_missing_nav_and_index_are_preserved_as_null_not_zero():
    probe = support.PROBE_MATRIX[0]
    row = {field: "" for field in probe.required_fields}
    row.update({"ISU_SRT_CD":"000000", "ISU_CD":"KR7000000000", "SECUGRP_ID":"EF", "ISU_ABBRV":"x", "ACC_TRDVOL":"0", "ACC_TRDVAL":"0"})
    _, _, audit = support.classify_business_body(probe, json.dumps({"output":[row]}).encode())
    assert audit["null_counts"]["NAV"] == 1
    assert audit["null_counts"]["OBJ_STKPRC_IDX"] == 1
    assert audit["negative_count_violations"] == 0


def test_valid_empty_and_coverage_empty_are_distinct_from_failures():
    assert support.classify_business_body(support.PROBE_MATRIX[4], b'{"output":[]}')[:2] == ("VALID_EMPTY", 0)
    assert support.classify_business_body(support.PROBE_MATRIX[1], b'{"output":[]}')[:2] == ("COVERAGE_EMPTY", 0)
    with pytest.raises(support.PilotStopped, match="ANOMALOUS_EMPTY"):
        support.classify_business_body(support.PROBE_MATRIX[0], b'{"output":[]}')
    with pytest.raises(support.PilotStopped, match="HTML_OR_RESTRICTION"):
        support.classify_business_body(support.PROBE_MATRIX[1], b"<html>blocked</html>")


def test_active_shared_lock_fails_closed_without_touching_it(tmp_path: Path):
    lock = tmp_path / "d_owned_krx_short_selling.lock"
    lock.write_text("active", encoding="utf-8")
    before = lock.read_bytes()
    with pytest.raises(support.PilotLocked):
        with support.shared_d_owned_krx_lock(lock, run_id="c011"):
            pass
    assert lock.read_bytes() == before

