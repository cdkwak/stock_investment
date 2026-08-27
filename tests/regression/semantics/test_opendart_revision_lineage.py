import json
from pathlib import Path

import pytest

from stock_data.audit.opendart_revision_lineage import (
    OpenDartLineageAuditError,
    audit_revision_lineage,
)


def _write_run(root: Path, *, parent_key: bool = False) -> None:
    requests = [
        {"operation": "list", "public_parameters": {
            "bgn_de": "20220401", "end_de": "20220415", "last_reprt_at": "N"}},
        {"operation": "fricDecsn", "public_parameters": {}},
        {"operation": "pifricDecsn", "public_parameters": {}},
    ]
    list_row = {"rcept_no": "20220406002324", "rm": "correction exists"}
    terms_row = {"rcept_no": "20220614000068"}
    if parent_key:
        terms_row["original_rcept_no"] = "20220406002324"
    payloads = {
        "manifest.json": {"run_id": "retained", "requests": requests},
        "checkpoint.json": {"status": "COMPLETE"},
        "response_01_list.json": {"status": "000", "list": [list_row]},
        "response_03_pifricDecsn.json": {"status": "000", "list": [terms_row]},
    }
    for name, payload in payloads.items():
        (root / name).write_text(json.dumps(payload), encoding="utf-8")


def test_retained_shape_proves_mismatch_but_not_parent_edge(tmp_path: Path) -> None:
    _write_run(tmp_path)
    result = audit_revision_lineage(tmp_path)
    assert result["list_receipts"] == ["20220406002324"]
    assert result["terms_receipts"] == ["20220614000068"]
    assert result["terms_receipts_outside_list_window"] == ["20220614000068"]
    assert result["lineage_status"] == "PARENT_EDGE_UNAVAILABLE_IN_RETAINED_EVIDENCE"
    assert result["date_filter_status"] == "SEMANTICS_UNRESOLVED"
    assert result["canonicalization_allowed"] is False


def test_only_an_explicit_source_key_can_enable_lineage(tmp_path: Path) -> None:
    _write_run(tmp_path, parent_key=True)
    result = audit_revision_lineage(tmp_path)
    assert result["explicit_parent_keys_present"] == ["original_rcept_no"]
    assert result["canonicalization_allowed"] is True


def test_non_complete_pilot_fails_closed(tmp_path: Path) -> None:
    _write_run(tmp_path)
    (tmp_path / "checkpoint.json").write_text('{"status":"STOPPED"}', encoding="utf-8")
    with pytest.raises(OpenDartLineageAuditError, match="not COMPLETE"):
        audit_revision_lineage(tmp_path)
