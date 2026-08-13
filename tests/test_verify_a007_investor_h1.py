from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil

import pytest

from scripts.manual import a007_investor_h1_diagnostic_support as support
from scripts.manual import verify_a007_investor_h1 as verifier
from scripts.manual.pykrx_short_selling_pilot_support import PilotStopped


RUN_ID = "20260813T103525Z_47ad701d154b430e89f18434bd152031"
ARTIFACTS = (
    "response.json", "response.json.provenance.json", "manifest.json", "call_ledger.jsonl",
)


def _actual_run() -> Path:
    return Path("data/landing/diagnostics/a007_investor_h1") / RUN_ID


def _copy_run(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    project = tmp_path / "project"
    run = project / "data/landing/diagnostics/a007_investor_h1" / RUN_ID
    run.mkdir(parents=True)
    for name in ARTIFACTS:
        shutil.copyfile(_actual_run() / name, run / name)
    dates = tuple(json.loads((run / "manifest.json").read_text("utf-8"))["expected_dates"])
    monkeypatch.setattr(support, "expected_dates", lambda unused: dates)
    return project, run


def _hashes(run: Path) -> dict[str, str]:
    return {name: hashlib.sha256((run / name).read_bytes()).hexdigest() for name in ARTIFACTS}


def _evidence_snapshot(run: Path) -> dict[str, str]:
    root = run / verifier.EVIDENCE_ROOT
    if not root.exists():
        return {}
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*")) if path.is_file()
    }


def test_actual_h1_run_reproduces_exact_audit_without_writes():
    run = _actual_run()
    before = _hashes(run)
    evidence_before = _evidence_snapshot(run)
    result = verifier.verify_retained_run(
        project_root=Path("."), run_dir=run, write_evidence=False,
    )
    assert result["status"] == "DRY_RUN_PASS"
    assert result["classification"] == "PRE_AVAILABILITY_COLLAPSE"
    assert result["raw_http_calls"] == 6
    assert result["authentication_calls"] == 5
    assert result["business_calls"] == 1
    assert result["http_200_calls"] == 6
    assert result["source_rows"] == 1
    assert result["observed_dates"] == ["20120104"]
    assert result["positive_total_dates"] == 0
    assert result["network_calls"] == 0
    assert result["body_sha256"] == "6ead29ac104ea3da7499b31e089e2f3634107d452a62278fc02d3859f4003c32"
    assert _hashes(run) == before
    assert _evidence_snapshot(run) == evidence_before


def test_append_only_evidence_is_content_addressed_idempotent_and_preserves_originals(tmp_path, monkeypatch):
    project, run = _copy_run(tmp_path, monkeypatch)
    before = _hashes(run)
    first = verifier.verify_retained_run(project_root=project, run_dir=run, write_evidence=True)
    second = verifier.verify_retained_run(project_root=project, run_dir=run, write_evidence=True)
    assert first["status"] == "VERIFIED"
    assert second["status"] == "ALREADY_VERIFIED"
    assert first["verification_sha256"] == second["verification_sha256"]
    evidence = project / first["path"]
    assert evidence.name == first["verification_sha256"] + ".json"
    assert json.loads(evidence.read_text("utf-8"))["verification_sha256"] == first["verification_sha256"]
    assert _hashes(run) == before


@pytest.mark.parametrize("artifact,mutate,reason", [
    ("response.json", lambda value: value.replace(b'"0"', b'"1"', 1), "OFFLINE_PROVENANCE_CHAIN_MISMATCH"),
    ("manifest.json", lambda value: value.replace(b'"expected_date_count": 502', b'"expected_date_count": 501'), "OFFLINE_MANIFEST_MISMATCH"),
    ("call_ledger.jsonl", lambda value: value.replace(b'"raw_sequence": 6', b'"raw_sequence": 7'), "OFFLINE_LEDGER_CHAIN_MISMATCH"),
])
def test_tampered_retained_artifacts_are_rejected(tmp_path, monkeypatch, artifact, mutate, reason):
    project, run = _copy_run(tmp_path, monkeypatch)
    path = run / artifact
    path.write_bytes(mutate(path.read_bytes()))
    with pytest.raises(PilotStopped, match=reason):
        verifier.verify_retained_run(project_root=project, run_dir=run)


def test_configured_credential_or_sensitive_key_is_rejected(tmp_path, monkeypatch):
    project, run = _copy_run(tmp_path, monkeypatch)
    secret = "unique-h1-password"
    (project / ".env").write_text(f"KRX_PW={secret}\n", encoding="utf-8")
    body = json.loads((run / "response.json").read_text("utf-8"))
    body["password"] = secret
    (run / "response.json").write_text(json.dumps(body), encoding="utf-8")
    with pytest.raises(PilotStopped, match="OFFLINE_CREDENTIAL_KEY_PRESENT"):
        verifier.verify_retained_run(project_root=project, run_dir=run)


def test_existing_content_address_collision_is_rejected(tmp_path, monkeypatch):
    project, run = _copy_run(tmp_path, monkeypatch)
    dry = verifier.verify_retained_run(project_root=project, run_dir=run)
    target = project / dry["path"]
    target.parent.mkdir(parents=True)
    target.write_text("collision", encoding="utf-8")
    with pytest.raises(PilotStopped, match="OFFLINE_EVIDENCE_COLLISION"):
        verifier.verify_retained_run(project_root=project, run_dir=run, write_evidence=True)


def test_original_mutation_during_evidence_write_is_detected(tmp_path, monkeypatch):
    project, run = _copy_run(tmp_path, monkeypatch)
    dry = verifier.verify_retained_run(project_root=project, run_dir=run)
    target = project / dry["path"]
    original_assert = verifier._assert_original_hashes
    calls = 0

    def mutate_at_publish_boundary(paths, expected):
        nonlocal calls
        calls += 1
        if calls == 1:
            ledger = run / "call_ledger.jsonl"
            ledger.write_bytes(ledger.read_bytes() + b"\n")
        return original_assert(paths, expected)

    monkeypatch.setattr(verifier, "_assert_original_hashes", mutate_at_publish_boundary)
    with pytest.raises(PilotStopped, match="OFFLINE_ORIGINAL_ARTIFACT_CHANGED"):
        verifier.verify_retained_run(project_root=project, run_dir=run, write_evidence=True)
    assert calls == 1
    assert not target.exists()


def test_existing_evidence_acceptance_rechecks_originals(tmp_path, monkeypatch):
    project, run = _copy_run(tmp_path, monkeypatch)
    first = verifier.verify_retained_run(project_root=project, run_dir=run, write_evidence=True)
    target = project / first["path"]
    evidence_before = target.read_bytes()
    original_assert = verifier._assert_original_hashes
    calls = 0

    def mutate_before_acceptance(paths, expected):
        nonlocal calls
        calls += 1
        if calls == 2:
            ledger = run / "call_ledger.jsonl"
            ledger.write_bytes(ledger.read_bytes() + b"\n")
        return original_assert(paths, expected)

    monkeypatch.setattr(verifier, "_assert_original_hashes", mutate_before_acceptance)
    with pytest.raises(PilotStopped, match="OFFLINE_ORIGINAL_ARTIFACT_CHANGED"):
        verifier.verify_retained_run(project_root=project, run_dir=run, write_evidence=True)
    assert calls == 2
    assert target.read_bytes() == evidence_before


def test_cli_defaults_to_dry_run(monkeypatch, capsys):
    run = _actual_run()
    monkeypatch.setattr("sys.argv", ["verify_a007_investor_h1.py", str(run)])
    assert verifier.main() == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "DRY_RUN_PASS"
    assert output["network_calls"] == 0
