"""Zero-network verifier for one retained A007 Investor S1 diagnostic run."""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sys
from uuid import uuid4
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.manual.diagnostic import a007_investor_s1_diagnostic_support as support
from scripts.manual.pilot.pykrx_short_selling_pilot_support import PilotStopped


LANDING_ROOT = ROOT / "data/landing/diagnostics/a007_investor_s1"
BODY = "response.json"
PROVENANCE = "response.json.provenance.json"
MANIFEST = "manifest.json"
LEDGER = "call_ledger.jsonl"
EVIDENCE_ROOT = "offline_verifications"
_REPARSE_POINT = 0x400


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
            if current.is_symlink() or bool(
                getattr(status, "st_file_attributes", 0) & _REPARSE_POINT
            ):
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


def _atomic_new(path: Path, body: bytes, project_root: Path) -> str:
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
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != body:
                raise PilotStopped("OFFLINE_EVIDENCE_COLLISION")
            return "ALREADY_VERIFIED"
    finally:
        temporary.unlink(missing_ok=True)
    return "VERIFIED"


def verify_retained_run(
    *, project_root: Path, run_dir: Path, write_evidence: bool = True,
) -> dict[str, object]:
    project_root = project_root.resolve()
    run_dir = _inside(run_dir, project_root)
    if run_dir.parent.resolve() != (project_root / "data/landing/diagnostics/a007_investor_s1").resolve():
        raise PilotStopped("OFFLINE_RUN_LOCATION_INVALID")
    paths = {name: _inside(run_dir / name, project_root) for name in (BODY, PROVENANCE, MANIFEST, LEDGER)}
    if any(not path.is_file() for path in paths.values()):
        raise PilotStopped("OFFLINE_REQUIRED_ARTIFACT_MISSING")
    original_hashes = {name: _sha(path) for name, path in paths.items()}
    manifest = _json(paths[MANIFEST])
    provenance = _json(paths[PROVENANCE])
    dates = support.expected_dates(project_root)
    expected_manifest = support.manifest_payload(
        run_id=run_dir.name,
        created_at_utc=str(manifest.get("created_at_utc")), dates=dates,
    )
    if manifest != expected_manifest:
        raise PilotStopped("OFFLINE_MANIFEST_MISMATCH")

    try:
        ledger = [json.loads(line) for line in paths[LEDGER].read_text(encoding="utf-8").splitlines()]
    except (UnicodeError, json.JSONDecodeError) as error:
        raise PilotStopped("OFFLINE_LEDGER_INVALID") from error
    responses = [item for item in ledger if item.get("event") == "HTTP_RESPONSE"]
    auth = [item for item in responses if item.get("authentication") is True]
    business = [item for item in responses if item.get("authentication") is False]
    scope = [item for item in ledger if item.get("event") == "SCOPE_STARTED"]
    stopped = [item for item in ledger if item.get("event") == "DIAGNOSTIC_STOPPED"]
    if (
        len(responses) != 6 or len(auth) != 5 or len(business) != 1
        or [item.get("raw_sequence") for item in responses] != list(range(1, 7))
        or any(item.get("status_code") != 200 for item in responses)
        or len(scope) != 1 or len(stopped) != 1
        or ledger[-1] is not stopped[0]
        or stopped[0].get("error") != "TOP_LEVEL_SCHEMA_MISMATCH"
        or any(item.get("event") == "DIAGNOSTIC_PASSED" for item in ledger)
    ):
        raise PilotStopped("OFFLINE_LEDGER_CHAIN_MISMATCH")
    expected_scope = {
        "bld": support.BUSINESS_BLD, "scope": support.SCOPE_ID,
        "params": support.SCOPE, "business_request_limit": 1,
    }
    if any(scope[0].get(key) != value for key, value in expected_scope.items()):
        raise PilotStopped("OFFLINE_REQUEST_SCOPE_MISMATCH")
    call = business[0]
    if (
        call.get("method") != "POST" or call.get("raw_sequence") != 6
        or call.get("url") != "https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd"
        or call.get("scope") != support.SCOPE_ID
        or call.get("body_file") != BODY or call.get("provenance_file") != PROVENANCE
    ):
        raise PilotStopped("OFFLINE_BUSINESS_CALL_MISMATCH")
    body = paths[BODY].read_bytes()
    body_sha = hashlib.sha256(body).hexdigest()
    if (
        call.get("response_sha256") != body_sha
        or call.get("response_bytes") != len(body)
        or provenance.get("body_sha256") != body_sha
        or provenance.get("response_bytes") != len(body)
        or provenance.get("http_status_code") != 200
        or provenance.get("raw_sequence") != 6
        or provenance.get("run_id") != run_dir.name
        or provenance.get("scope_id") != support.SCOPE_ID
        or provenance.get("scope_sha256") != support.scope_sha256(dates)
        or provenance.get("expected_dates") != list(dates)
        or provenance.get("ledger_relative_path") != paths[LEDGER].relative_to(project_root).as_posix()
    ):
        raise PilotStopped("OFFLINE_PROVENANCE_CHAIN_MISMATCH")

    classification = support.classify_response(body, dates)
    if classification.source_current_datetime is None:
        raise PilotStopped("CURRENT_DATETIME_REQUIRED_FOR_S1_EVIDENCE")
    source_time = datetime.strptime(
        classification.source_current_datetime, "%Y.%m.%d %p %I:%M:%S"
    ).replace(tzinfo=ZoneInfo("Asia/Seoul"))
    recorded_time = datetime.fromisoformat(str(call["recorded_at_utc"])).astimezone(
        ZoneInfo("Asia/Seoul")
    )
    if abs((recorded_time - source_time).total_seconds()) > 300:
        raise PilotStopped("CURRENT_DATETIME_LEDGER_TIME_MISMATCH")
    if classification.positive_total_dates != len(dates):
        raise PilotStopped("OFFLINE_NOT_ALL_DATES_POSITIVE")

    evidence = {
        "verification_schema": "a007.investor_s1.offline_verification",
        "version": 1,
        "run_id": run_dir.name,
        "classification": classification.classification,
        "source_rows": classification.source_rows,
        "expected_date_count": len(dates),
        "positive_total_dates": classification.positive_total_dates,
        "coverage_start": dates[0], "coverage_end": dates[-1],
        "source_current_datetime": classification.source_current_datetime,
        "raw_http_calls": len(responses), "authentication_calls": len(auth),
        "business_calls": len(business), "http_200_calls": len(responses),
        "original_artifact_sha256": original_hashes,
        "body_sha256": body_sha,
        "scope_sha256": support.scope_sha256(dates),
        "request_evidence": {
            "bld": support.BUSINESS_BLD, "params": dict(support.SCOPE),
            "method": "POST", "endpoint": call["url"],
            "limitation": "retained ledger records validated scope, not serialized wire-body bytes",
        },
        "original_terminal_event_preserved": "DIAGNOSTIC_STOPPED:TOP_LEVEL_SCHEMA_MISMATCH",
        "network_calls": 0,
    }
    digest = hashlib.sha256(_canonical(evidence)).hexdigest()
    evidence["verification_sha256"] = digest
    target = run_dir / EVIDENCE_ROOT / f"{digest}.json"
    status = "DRY_RUN_PASS"
    if write_evidence:
        status = _atomic_new(target, json.dumps(
            evidence, ensure_ascii=False, indent=2, sort_keys=True
        ).encode("utf-8"), project_root)
    if {name: _sha(path) for name, path in paths.items()} != original_hashes:
        raise PilotStopped("OFFLINE_ORIGINAL_ARTIFACT_CHANGED")
    return {"status": status, "path": target.relative_to(project_root).as_posix(), **evidence}


def main() -> int:
    parser = argparse.ArgumentParser(description="Zero-network retained Investor S1 verifier")
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
