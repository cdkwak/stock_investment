"""Offline evidence check for OpenDART filing revision lineage.

This audit deliberately does not infer a parent from issuer, report name, dates,
or economic terms.  It only reports an edge when the retained source payload
contains an explicit receipt-to-receipt relationship.
"""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any


class OpenDartLineageAuditError(ValueError):
    """Retained evidence is incomplete or inconsistent."""


_EXPLICIT_PARENT_KEYS = frozenset(
    {
        "parent_rcept_no",
        "original_rcept_no",
        "previous_rcept_no",
        "supersedes_rcept_no",
        "amends_rcept_no",
    }
)


def _load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OpenDartLineageAuditError(f"cannot read retained JSON: {path.name}") from error
    if not isinstance(value, dict):
        raise OpenDartLineageAuditError(f"retained JSON root is not an object: {path.name}")
    return value


def _success_rows(payload: dict[str, Any], operation: str) -> list[dict[str, Any]]:
    if payload.get("status") != "000" or not isinstance(payload.get("list"), list):
        raise OpenDartLineageAuditError(f"{operation} is not a retained successful row response")
    rows = payload["list"]
    if any(not isinstance(row, dict) for row in rows):
        raise OpenDartLineageAuditError(f"{operation} contains a non-object row")
    return rows


def audit_revision_lineage(run_dir: Path) -> dict[str, Any]:
    """Assess only explicit lineage evidence in one completed three-call pilot."""
    manifest = _load_object(run_dir / "manifest.json")
    checkpoint = _load_object(run_dir / "checkpoint.json")
    list_payload = _load_object(run_dir / "response_01_list.json")
    terms_payload = _load_object(run_dir / "response_03_pifricDecsn.json")

    if checkpoint.get("status") != "COMPLETE":
        raise OpenDartLineageAuditError("pilot checkpoint is not COMPLETE")
    requests = manifest.get("requests")
    if not isinstance(requests, list) or len(requests) != 3:
        raise OpenDartLineageAuditError("pilot manifest is not the fixed three-call matrix")
    list_request = requests[0]
    parameters = list_request.get("public_parameters", {})
    if list_request.get("operation") != "list" or parameters.get("last_reprt_at") != "N":
        raise OpenDartLineageAuditError("disclosure list did not retain last_reprt_at=N")

    list_rows = _success_rows(list_payload, "list")
    terms_rows = _success_rows(terms_payload, "pifricDecsn")
    list_receipts = [row.get("rcept_no") for row in list_rows]
    terms_receipts = [row.get("rcept_no") for row in terms_rows]
    if any(not isinstance(value, str) or len(value) != 14 or not value.isdigit()
           for value in [*list_receipts, *terms_receipts]):
        raise OpenDartLineageAuditError("receipt identity is not a 14-digit string")

    all_rows = [*list_rows, *terms_rows]
    explicit_keys = sorted(set().union(*(set(row) for row in all_rows)) & _EXPLICIT_PARENT_KEYS)
    begin = datetime.strptime(parameters["bgn_de"], "%Y%m%d").date()
    end = datetime.strptime(parameters["end_de"], "%Y%m%d").date()
    outside_terms_receipts = [
        receipt for receipt in terms_receipts
        if not begin <= datetime.strptime(receipt[:8], "%Y%m%d").date() <= end
    ]

    return {
        "run_id": manifest.get("run_id"),
        "list_filter": {"last_reprt_at": "N", "begin": parameters["bgn_de"], "end": parameters["end_de"]},
        "list_receipts": list_receipts,
        "terms_receipts": terms_receipts,
        "receipt_sets_match": set(list_receipts) == set(terms_receipts),
        "terms_receipts_outside_list_window": outside_terms_receipts,
        "explicit_parent_keys_present": explicit_keys,
        "lineage_status": (
            "EXPLICIT_PARENT_EDGE_PRESENT"
            if explicit_keys
            else "PARENT_EDGE_UNAVAILABLE_IN_RETAINED_EVIDENCE"
        ),
        "date_filter_status": (
            "SEMANTICS_UNRESOLVED" if outside_terms_receipts else "NO_CONTRADICTION_OBSERVED"
        ),
        "canonicalization_allowed": bool(explicit_keys),
    }
