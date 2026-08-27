from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from scripts.manual.pilot import pilot_pykrx_fundamentals as runner
from scripts.manual.pilot import pykrx_fundamentals_pilot_support as support


def test_matrix_is_fixed_bounded_and_contains_full_market_and_bounded_qa():
    assert support.PYKRX_VERSION == "1.2.8"
    assert len(support.PROBE_MATRIX) == support.MAX_BUSINESS_REQUESTS == 7
    assert support.MAX_RAW_HTTP_REQUESTS == 15
    assert [item.operation for item in support.PROBE_MATRIX].count("market") == 4
    assert [item.operation for item in support.PROBE_MATRIX].count("symbol") == 3
    assert all(item.bld.endswith(("03501", "03502")) for item in support.PROBE_MATRIX)
    assert [item.scope["date"] for item in support.PROBE_MATRIX[2:4]] == ["20080102", "20080102"]
    assert all("source_coverage" in item.name for item in support.PROBE_MATRIX[2:4])


def test_runner_refuses_live_execution_without_explicit_confirmation(monkeypatch):
    monkeypatch.setattr("sys.argv", ["pilot_pykrx_fundamentals.py"])
    assert runner.main() == 2


def test_runner_is_landing_only_single_stream_without_dataset_contract_or_retry():
    source = inspect.getsource(runner.run_pilot)
    assert "DatasetContract" not in source
    assert "data/normalized" not in source.lower()
    assert "to_parquet" not in source
    assert '"retry_count": 0' in source
    assert "for attempt" not in source
    assert "Thread" not in source and "asyncio" not in source
    assert runner.D_OWNED_LOCK_PATH.name == "d_owned_krx_short_selling.lock"


def test_classification_uses_verified_raw_output_schema_not_dataframe_aliases():
    body = b'{"output":[{"ISU_SRT_CD":"005930","ISU_ABBRV":"x","TDD_CLSPRC":"1","EPS":"1","PER":"1","BPS":"1","PBR":"1","DPS":"0","DVD_YLD":"0"}]}'
    assert support.classify_business_body(support.PROBE_MATRIX[0], body) == ("SUCCESS", 1)
    with pytest.raises(support.PilotStopped, match="EXPECTED_OUTPUT_MISSING"):
        support.classify_business_body(support.PROBE_MATRIX[0], b'{"OutBlock_1":[]}')


def test_shared_lock_fails_closed_when_active(tmp_path: Path):
    lock = tmp_path / "d_owned_krx_short_selling.lock"
    lock.write_text("{}", encoding="utf-8")
    with pytest.raises(support.PilotLocked):
        with support.shared_d_owned_krx_lock(lock, run_id="c008"):
            pass


def test_raw_html_and_error_payload_stop_before_schema_assumptions():
    with pytest.raises(support.PilotStopped, match="HTML_OR_RESTRICTION"):
        support.classify_business_body(support.PROBE_MATRIX[0], b"<html>blocked</html>")
    with pytest.raises(support.PilotStopped, match="SOURCE_ERROR_PAYLOAD"):
        support.classify_business_body(support.PROBE_MATRIX[0], b'{"_error_code":"x"}')


def test_credit_matrix_is_bounded_landing_only_and_preserves_source_codes():
    probes = support.CREDIT_PROBE_MATRIX
    assert len(probes) == 7
    assert [probe.operation for probe in probes].count("bond_snapshot") == 4
    assert [probe.operation for probe in probes].count("bond_range") == 3
    assert [probe.scope.get("code") for probe in probes[-3:]] == ["3009", "3010", "4000"]
    assert {probe.bld.rsplit("/", 1)[-1] for probe in probes} == {"MDCSTAT11401", "MDCSTAT11402"}
    assert support.classify_business_body(probes[3], b'{"output":[]}') == ("COVERAGE_EMPTY", 0)
    body = b'{"output":[{"DISCLS_DD":"2026/08/12","LST_ORD_BAS_YD":"4.496","CMP_YD":"-0.008"}]}'
    assert support.classify_business_body(probes[4], body) == ("SUCCESS", 1)
    assert len(support.CREDIT_BACKFILL_MATRIX) == 42
    assert {probe.scope["code"] for probe in support.CREDIT_BACKFILL_MATRIX} == {"3009", "3010", "4000"}
    assert support.CREDIT_BACKFILL_MATRIX[0].scope["fromdate"] == "20000105"
    assert support.CREDIT_BACKFILL_MATRIX[-1].scope["todate"] == "20260812"


def test_index_backfill_uses_five_range_native_series_and_bounded_chunks():
    probes = support.INDEX_BACKFILL_MATRIX
    assert len(probes) == 70
    assert {probe.scope["ticker"] for probe in probes} == {"1001", "1028", "2001", "2203", "5300"}
    assert {probe.operation for probe in probes} == {"index_range"}
    assert probes[0].scope["fromdate"] == "20000101"
    assert probes[-1].scope["todate"] == "20260812"
    assert support.MIN_BUSINESS_INTERVAL_SECONDS == 3.0
    assert support.MAX_JITTER_SECONDS == 1.0


def test_optimization_matrix_uses_all_market_and_new_sector_dates_only():
    probes = support.OPTIMIZATION_PROBE_MATRIX
    assert len(probes) == 9
    assert [p.operation for p in probes].count("foreign_all") == 3
    assert [p.scope.get("market") for p in probes if p.operation == "market"] == ["ALL"] * 3
    assert [p.operation for p in probes].count("sector") == 3
