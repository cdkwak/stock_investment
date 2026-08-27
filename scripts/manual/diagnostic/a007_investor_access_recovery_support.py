"""Frozen one-call KRX Investor access-recovery sentinel."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from scripts.manual.diagnostic import a007_investor_h4_boundary_diagnostic_support as boundary
from scripts.manual.pilot.pykrx_short_selling_pilot_support import PilotStopped


PYKRX_VERSION = boundary.PYKRX_VERSION
BUSINESS_BLD = boundary.BUSINESS_BLD
BUSINESS_ENDPOINT_PATH = boundary.BUSINESS_ENDPOINT_PATH
MAX_BUSINESS_REQUESTS = 1
MAX_RAW_HTTP_REQUESTS = EXPECTED_RAW_HTTP_REQUESTS = 6
REQUIRE_ZERO_RETRY_AUTH_SESSION = True
SCOPE_ID = "20170519_20170522_KOSPI_trading_value_access_recovery"
SCOPE = {
    "strtDd": "20170519", "endDd": "20170522",
    "inqCondTpCd": 2, "mktTpCd": 1,
}
EXPECTED_BUSINESS_DATA = {
    "bld": BUSINESS_BLD, "strtDd": "20170519", "endDd": "20170522",
    "inqCondTpCd": "2", "mktTpCd": "1",
}
EXPECTED_DATE_COUNT = boundary.EXPECTED_DATE_COUNT
EXPECTED_DATE_SHA256 = boundary.EXPECTED_DATE_SHA256
MINIMUM_COOLDOWN_SECONDS = 6 * 60 * 60
PRIOR_RESTRICTION_AT_UTC = "2026-08-13T11:27:43.270139Z"
PRIOR_RUN_RELATIVE = Path(
    "data/landing/diagnostics/a007_investor_h4_boundary_parity/"
    "20260813T112742Z_f6827bb1340c4170b33a51f2ae8debaa"
)
PRIOR_FILES = {
    "call_ledger.jsonl": "7b5bd05cc329c0202ea4eb54621b89cb4bc1a30382369915b966373b300607ba",
    "manifest.json": "f4675ddd9b0efc2f965f2a62c951f59d84179348a35083233eb85aa7fd503e42",
    "response_01.json": "2c860edd6d3458284e3b7f2f727385462a5e2c59d3f32ec4244da90780c0dfa9",
    "response_01.json.provenance.json": "f1f44b0b375464e7a7957dd8b21501b06b20321417d8a6da3819e4b3e1456312",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_prior_restriction(project_root: Path, *, now: datetime | None = None) -> float:
    root = project_root.resolve()
    run = root / PRIOR_RUN_RELATIVE
    if not run.is_dir() or run.is_symlink():
        raise PilotStopped("PRIOR_RESTRICTION_RUN_MISSING")
    resolved = run.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as error:
        raise PilotStopped("PRIOR_RESTRICTION_PATH_ESCAPE") from error
    files = {path.name: path for path in run.iterdir() if path.is_file()}
    if set(files) != set(PRIOR_FILES) or any(
        path.is_symlink() or _sha(path) != PRIOR_FILES[name]
        for name, path in files.items()
    ):
        raise PilotStopped("PRIOR_RESTRICTION_EVIDENCE_CHANGED")
    entries = [json.loads(line) for line in files["call_ledger.jsonl"].read_text(
        encoding="utf-8"
    ).splitlines()]
    business = [entry for entry in entries if entry.get("authentication") is False]
    if (
        len(business) != 1 or business[0].get("status_code") != 403
        or business[0].get("scope") != "KOSPI_trading_value"
        or entries[-1].get("event") != "DIAGNOSTIC_STOPPED"
        or entries[-1].get("error") != "HTTP_RESTRICTION:403"
    ):
        raise PilotStopped("PRIOR_RESTRICTION_LEDGER_CHANGED")
    current = now or datetime.now(timezone.utc)
    prior = datetime.fromisoformat(PRIOR_RESTRICTION_AT_UTC.replace("Z", "+00:00"))
    elapsed = (current.astimezone(timezone.utc) - prior).total_seconds()
    if elapsed < MINIMUM_COOLDOWN_SECONDS:
        raise PilotStopped(f"COOLDOWN_NOT_ENDED:{int(elapsed)}/{MINIMUM_COOLDOWN_SECONDS}")
    return elapsed


def expected_dates(project_root: Path) -> tuple[str, ...]:
    verify_prior_restriction(project_root)
    return boundary.expected_dates(project_root)


def scope_sha256(dates: tuple[str, ...]) -> str:
    payload = {
        "bld": BUSINESS_BLD, "dates": dates, "scope": SCOPE,
        "scope_id": SCOPE_ID, "prior_files": PRIOR_FILES,
        "minimum_cooldown_seconds": MINIMUM_COOLDOWN_SECONDS,
    }
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":")
    ).encode()).hexdigest()


def manifest_payload(*, run_id: str, created_at_utc: str, dates: tuple[str, ...]) -> dict[str, object]:
    return {
        "version": 1, "run_id": run_id, "created_at_utc": created_at_utc,
        "purpose": "KRX_Investor_access_recovery_after_cooldown",
        "scope_id": SCOPE_ID, "scope": dict(SCOPE),
        "expected_dates": list(dates), "expected_date_count": EXPECTED_DATE_COUNT,
        "expected_date_sha256": EXPECTED_DATE_SHA256,
        "scope_sha256": scope_sha256(dates),
        "prior_restriction_run": PRIOR_RUN_RELATIVE.as_posix(),
        "prior_restriction_files": dict(PRIOR_FILES),
        "prior_restriction_at_utc": PRIOR_RESTRICTION_AT_UTC,
        "minimum_cooldown_seconds": MINIMUM_COOLDOWN_SECONDS,
        "business_request_limit": 1, "raw_http_request_limit": 6,
        "raw_http_requests_expected": 6, "retry_count": 0, "parallelism": 1,
        "checkpoint_writes": False, "normalized_writes": False,
        "pykrx_version": PYKRX_VERSION,
    }


classify_response = boundary.classify_response
