"""Zero-network verifier for one retained A007 Investor H1 diagnostic run."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.manual import a007_investor_h1_diagnostic_support as support
from scripts.manual.pykrx_short_selling_pilot_support import PilotStopped


LANDING_ROOT = ROOT / "data/landing/diagnostics/a007_investor_h1"
BODY = "response.json"
PROVENANCE = "response.json.provenance.json"
MANIFEST = "manifest.json"
LEDGER = "call_ledger.jsonl"
EVIDENCE_ROOT = "offline_verifications"
_REPARSE_POINT = 0x400
_SENSITIVE_KEYS = {
    "authorization", "cookie", "krx_id", "krx_pw", "password", "passwd",
    "api_key", "apikey", "service_key", "secret", "token",
}


def _canonical(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _inside(path: Path, root: Path) -> Path:
    root = root.resolve()
    try:
        relative = path.absolute().relative_to(root)
    except ValueError as error:
        raise PilotStopped("OFFLINE_PATH_ESCAPE") from error
    current = root
    for component in relative.parts:
        current /= component
        if current.exists():
            status = current.lstat()
            if current.is_symlink() or bool(getattr(status, "st_file_attributes", 0) & _REPARSE_POINT):
                raise PilotStopped("OFFLINE_PATH_REDIRECT")
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise PilotStopped("OFFLINE_PATH_ESCAPE") from error
    return resolved


def _json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PilotStopped(f"OFFLINE_JSON_INVALID:{path.name}") from error
    if not isinstance(value, dict):
        raise PilotStopped(f"OFFLINE_JSON_NOT_OBJECT:{path.name}")
    return value


def _has_sensitive_key(value: object) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if normalized in _SENSITIVE_KEYS or _has_sensitive_key(nested):
                return True
    elif isinstance(value, list):
        return any(_has_sensitive_key(item) for item in value)
    return False


def _configured_secrets(project_root: Path) -> tuple[bytes, ...]:
    values: set[bytes] = set()
    for key in ("KRX_ID", "KRX_PW"):
        value = os.environ.get(key, "").strip()
        if len(value) >= 4:
            values.add(value.encode("utf-8"))
    env_file = project_root / ".env"
    if env_file.is_file() and not env_file.is_symlink():
        for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            if key.strip() not in ("KRX_ID", "KRX_PW"):
                continue
            value = value.strip().strip('"').strip("'")
            if len(value) >= 4:
                values.add(value.encode("utf-8"))
    return tuple(sorted(values))


def _credential_scan(paths: dict[str, Path], parsed: tuple[object, ...], project_root: Path) -> None:
    if any(_has_sensitive_key(value) for value in parsed):
        raise PilotStopped("OFFLINE_CREDENTIAL_KEY_PRESENT")
    blob = b"\n".join(path.read_bytes() for path in paths.values())
    if any(secret in blob for secret in _configured_secrets(project_root)):
        raise PilotStopped("OFFLINE_CONFIGURED_CREDENTIAL_PRESENT")


def _assert_original_hashes(paths: dict[str, Path], expected: dict[str, str]) -> None:
    if {name: _sha(path) for name, path in paths.items()} != expected:
        raise PilotStopped("OFFLINE_ORIGINAL_ARTIFACT_CHANGED")


def _atomic_new(
    path: Path,
    body: bytes,
    project_root: Path,
    *,
    assert_source_unchanged,
) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    _inside(path.parent, project_root)
    _inside(path, project_root)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            assert_source_unchanged()
            os.link(temporary, path)
        except FileExistsError:
            assert_source_unchanged()
            if _inside(path, project_root).read_bytes() != body:
                raise PilotStopped("OFFLINE_EVIDENCE_COLLISION")
            return "ALREADY_VERIFIED"
    finally:
        temporary.unlink(missing_ok=True)
    return "VERIFIED"


def verify_retained_run(
    *, project_root: Path, run_dir: Path, write_evidence: bool = False,
) -> dict[str, object]:
    project_root = project_root.resolve()
    run_dir = _inside(run_dir, project_root)
    expected_parent = (project_root / "data/landing/diagnostics/a007_investor_h1").resolve()
    if run_dir.parent.resolve() != expected_parent:
        raise PilotStopped("OFFLINE_RUN_LOCATION_INVALID")
    paths = {name: _inside(run_dir / name, project_root) for name in (BODY, PROVENANCE, MANIFEST, LEDGER)}
    if any(not path.is_file() for path in paths.values()):
        raise PilotStopped("OFFLINE_REQUIRED_ARTIFACT_MISSING")
    original_hashes = {name: _sha(path) for name, path in paths.items()}
    manifest = _json(paths[MANIFEST])
    provenance = _json(paths[PROVENANCE])
    body_object = _json(paths[BODY])
    dates = support.expected_dates(project_root)
    try:
        ledger = [json.loads(line) for line in paths[LEDGER].read_text(encoding="utf-8").splitlines()]
    except (UnicodeError, json.JSONDecodeError) as error:
        raise PilotStopped("OFFLINE_LEDGER_INVALID") from error
    if not ledger or any(not isinstance(item, dict) for item in ledger):
        raise PilotStopped("OFFLINE_LEDGER_INVALID")
    _credential_scan(paths, (manifest, provenance, body_object, ledger), project_root)
    expected_manifest = support.manifest_payload(
        run_id=run_dir.name, created_at_utc=str(manifest.get("created_at_utc")), dates=dates,
    )
    if manifest != expected_manifest:
        raise PilotStopped("OFFLINE_MANIFEST_MISMATCH")
    responses = [item for item in ledger if item.get("event") == "HTTP_RESPONSE"]
    auth = [item for item in responses if item.get("authentication") is True]
    business = [item for item in responses if item.get("authentication") is False]
    scopes = [item for item in ledger if item.get("event") == "SCOPE_STARTED"]
    passed = [item for item in ledger if item.get("event") == "DIAGNOSTIC_PASSED"]
    if (
        len(responses) != 6 or len(auth) != 5 or len(business) != 1
        or [item.get("raw_sequence") for item in responses] != list(range(1, 7))
        or any(item.get("status_code") != 200 for item in responses)
        or len(scopes) != 1 or len(passed) != 1 or ledger[-1] is not passed[0]
        or any(item.get("event") == "DIAGNOSTIC_STOPPED" for item in ledger)
    ):
        raise PilotStopped("OFFLINE_LEDGER_CHAIN_MISMATCH")
    expected_scope = {
        "bld": support.BUSINESS_BLD, "scope": support.SCOPE_ID,
        "params": support.SCOPE, "business_request_limit": 1,
    }
    if any(scopes[0].get(key) != value for key, value in expected_scope.items()):
        raise PilotStopped("OFFLINE_REQUEST_SCOPE_MISMATCH")
    call = business[0]
    parsed_url = urlsplit(str(call.get("url", "")))
    if (
        call.get("method") != "POST" or call.get("raw_sequence") != 6
        or parsed_url.scheme != "https" or parsed_url.netloc != "data.krx.co.kr"
        or parsed_url.path != support.BUSINESS_ENDPOINT_PATH or parsed_url.query or parsed_url.fragment
        or call.get("scope") != support.SCOPE_ID
        or call.get("body_file") != BODY or call.get("provenance_file") != PROVENANCE
    ):
        raise PilotStopped("OFFLINE_BUSINESS_CALL_MISMATCH")
    body = paths[BODY].read_bytes()
    body_sha = hashlib.sha256(body).hexdigest()
    if (
        call.get("response_sha256") != body_sha or call.get("response_bytes") != len(body)
        or provenance.get("body_sha256") != body_sha or provenance.get("response_bytes") != len(body)
        or provenance.get("http_status_code") != 200 or provenance.get("raw_sequence") != 6
        or provenance.get("run_id") != run_dir.name or provenance.get("scope_id") != support.SCOPE_ID
        or provenance.get("scope_sha256") != support.scope_sha256(dates)
        or provenance.get("expected_dates") != list(dates)
        or provenance.get("ledger_relative_path") != paths[LEDGER].relative_to(project_root).as_posix()
    ):
        raise PilotStopped("OFFLINE_PROVENANCE_CHAIN_MISMATCH")

    classification = support.classify_response(body, dates)
    if (
        classification.classification != "PRE_AVAILABILITY_COLLAPSE"
        or classification.source_rows != 1 or classification.observed_dates != ("20120104",)
        or classification.positive_total_dates != 0
    ):
        raise PilotStopped("OFFLINE_NOT_EXACT_H1_COLLAPSE")
    terminal = passed[0]
    if any(terminal.get(key) != value for key, value in {
        "scope": support.SCOPE_ID, "classification": "PRE_AVAILABILITY_COLLAPSE",
        "source_rows": 1, "observed_dates": ["20120104"], "positive_total_dates": 0,
        "raw_http_requests": 6, "business_requests": 1,
    }.items()):
        raise PilotStopped("OFFLINE_TERMINAL_EVENT_MISMATCH")
    if classification.source_current_datetime is None:
        raise PilotStopped("CURRENT_DATETIME_REQUIRED_FOR_H1_EVIDENCE")
    source_time = datetime.strptime(
        classification.source_current_datetime, "%Y.%m.%d %p %I:%M:%S"
    ).replace(tzinfo=ZoneInfo("Asia/Seoul"))
    recorded_time = datetime.fromisoformat(str(call["recorded_at_utc"])).astimezone(ZoneInfo("Asia/Seoul"))
    if abs((recorded_time - source_time).total_seconds()) > 300:
        raise PilotStopped("CURRENT_DATETIME_LEDGER_TIME_MISMATCH")
    evidence = {
        "verification_schema": "a007.investor_h1.offline_verification", "version": 1,
        "run_id": run_dir.name, "classification": classification.classification,
        "source_rows": 1, "observed_dates": ["20120104"], "positive_total_dates": 0,
        "expected_date_count": len(dates), "coverage_start": dates[0], "coverage_end": dates[-1],
        "source_current_datetime": classification.source_current_datetime,
        "raw_http_calls": 6, "authentication_calls": 5, "business_calls": 1, "http_200_calls": 6,
        "original_artifact_sha256": original_hashes, "body_sha256": body_sha,
        "scope_sha256": support.scope_sha256(dates),
        "request_evidence": {
            "bld": support.BUSINESS_BLD, "params": dict(support.SCOPE), "method": "POST",
            "endpoint": call["url"],
            "limitation": "retained ledger validates scope; serialized wire-body bytes were not retained",
        },
        "credential_scan": "CONFIGURED_VALUES_AND_SENSITIVE_KEYS_ABSENT",
        "network_calls": 0,
    }
    digest = hashlib.sha256(_canonical(evidence)).hexdigest()
    evidence["verification_sha256"] = digest
    target = run_dir / EVIDENCE_ROOT / f"{digest}.json"
    status = "DRY_RUN_PASS"
    if write_evidence:
        status = _atomic_new(
            target, json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"),
            project_root,
            assert_source_unchanged=lambda: _assert_original_hashes(paths, original_hashes),
        )
    _assert_original_hashes(paths, original_hashes)
    return {"status": status, "path": target.relative_to(project_root).as_posix(), **evidence}


def main() -> int:
    parser = argparse.ArgumentParser(description="Zero-network retained Investor H1 verifier")
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--write-append-only-evidence", action="store_true")
    args = parser.parse_args()
    result = verify_retained_run(
        project_root=args.project_root, run_dir=args.run_dir,
        write_evidence=args.write_append_only_evidence,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
