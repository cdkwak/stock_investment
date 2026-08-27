from __future__ import annotations

import hashlib
from pathlib import Path

from scripts.manual.audit import verify_a007_investor_h3 as verifier

RUN_ID = "20260813T110213Z_581e739a8dab4e439206a73b5b838d46"


def _snapshot(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def test_actual_h3_run_reproduces_exact_audit_without_writes():
    run = Path("data/landing/diagnostics/a007_investor_h3") / RUN_ID
    before = _snapshot(run)
    result = verifier.verify_retained_run(project_root=Path("."), run_dir=run)
    assert result["status"] == "DRY_RUN_PASS"
    assert result["classification"] == "PRE_AVAILABILITY_COLLAPSE"
    assert result["observed_dates"] == ["20160106"]
    assert result["expected_date_count"] == 494
    assert result["raw_http_calls"] == result["http_200_calls"] == 6
    assert result["authentication_calls"] == 5 and result["business_calls"] == 1
    assert result["network_calls"] == 0
    assert result["body_sha256"] == "e56ece94a98a9cc2772e447bfa963deabeda9722beb62d54efa15b4e9d8e87cb"
    assert _snapshot(run) == before
