from __future__ import annotations

import hashlib
from pathlib import Path

from scripts.manual.audit import verify_a007_investor_h2 as verifier


RUN_ID = "20260813T105434Z_e4ea0268a64947a293293e5989f42c8c"
ARTIFACTS = (
    "response.json", "response.json.provenance.json", "manifest.json", "call_ledger.jsonl",
)


def _snapshot(root: Path) -> dict[str, str]:
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def test_actual_h2_run_reproduces_exact_audit_without_writes():
    run = Path("data/landing/diagnostics/a007_investor_h2") / RUN_ID
    originals_before = _snapshot(run)
    evidence_before = _snapshot(run / "offline_verifications")
    result = verifier.verify_retained_run(project_root=Path("."), run_dir=run)
    assert result["status"] == "DRY_RUN_PASS"
    assert result["classification"] == "PRE_AVAILABILITY_COLLAPSE"
    assert result["observed_dates"] == ["20140103"]
    assert result["expected_date_count"] == 494
    assert result["raw_http_calls"] == result["http_200_calls"] == 6
    assert result["authentication_calls"] == 5 and result["business_calls"] == 1
    assert result["network_calls"] == 0
    assert result["body_sha256"] == "f2c0e796a69b989dd1a0b6048d7e4b13d23e0e6e0907bb26d976f3166ae49f4a"
    assert _snapshot(run) == originals_before
    assert _snapshot(run / "offline_verifications") == evidence_before
