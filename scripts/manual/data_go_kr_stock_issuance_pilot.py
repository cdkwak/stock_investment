"""One-call Landing-first pilot for DATA.GO.KR stock issuance history.

The scope is frozen to the positive example retained in the official V3 guide.
It creates diagnostic evidence only: no Dataset Contract, production checkpoint,
Normalized artifact, or event interpretation is written.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import quote, unquote
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from stock_data.providers.data_go_kr.client import (  # noqa: E402
    DataGoKrClient,
    service_key_from_environment,
    write_landing_pages_atomic,
)


ENDPOINT = (
    "https://apis.data.go.kr/1160100/"
    "GetStocIssuInfoService_V3/getStocIssuInfo_V3"
)
BASE_DATE = "20231226"
PAGE_NO = 1
NUM_ROWS = 10
EXPECTED_TOTAL = 2
LANDING_RELATIVE = Path("data/landing/diagnostics/data_go_kr_stock_issuance_pilot")
CURRENT_SCOPE_LANDING_RELATIVE = Path(
    "data/landing/diagnostics/data_go_kr_stock_issuance_current_scope"
)
LOCK_RELATIVE = Path("data/state/data_go_kr_provider.lock")
SOURCE_FIELDS = {
    "basDt", "crno", "isinCd", "isinCdNm", "stckIssuCmpyNm", "scrsDcd",
    "stckIssuSqno", "stckIssuDt", "stckIssuDcnt", "scrsItmsKcd",
    "scrsItmsKcdNm", "stckIssuRcd", "stckIssuRcdNm", "issuStckCnt", "lstgDt",
}
REPARSE_POINT = 0x400


class PilotError(RuntimeError):
    pass


def _assert_plain(path: Path) -> Path:
    if not os.path.lexists(path):
        raise PilotError(f"required evidence path is missing: {path.name}")
    info = path.lstat()
    if path.is_symlink() or (getattr(info, "st_file_attributes", 0) & REPARSE_POINT):
        raise PilotError("links/reparse points are forbidden")
    return path


def _assert_topology(project_root: Path, path: Path) -> None:
    project_root = project_root.resolve()
    absolute = Path(os.path.abspath(path))
    try:
        relative = absolute.relative_to(project_root)
    except ValueError as error:
        raise PilotError("pilot path escapes the project root") from error
    current = project_root
    _assert_plain(current)
    for part in relative.parts:
        current = current / part
        if os.path.lexists(current):
            _assert_plain(current)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha(path: Path) -> str:
    return _sha_bytes(path.read_bytes())


def _secret_variants(value: str) -> set[bytes]:
    decoded = unquote(value)
    values = {value, decoded, quote(decoded, safe=""), quote(decoded, safe="~")}
    return {item.encode("utf-8") for item in values if item}


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise PilotError(f"immutable evidence already exists: {path.name}")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise PilotError(f"immutable evidence already exists: {path.name}")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def _provider_lock(project_root: Path, run_id: str):
    path = project_root / LOCK_RELATIVE
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise PilotError("DATA.GO.KR provider lock is already held") from error
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(run_id)
            stream.flush()
            os.fsync(stream.fileno())
        yield
    finally:
        try:
            owner = path.read_text(encoding="utf-8")
        except OSError as error:
            raise PilotError("provider lock ownership cannot be verified") from error
        if owner != run_id:
            raise PilotError("provider lock ownership changed")
        path.unlink()


class CaptureSession:
    """Persist exact response bytes before DataGoKrClient parses them."""

    def __init__(
        self, delegate, run_root: Path, service_key: str,
        expected_public_parameters: dict[str, str],
    ) -> None:
        self.delegate = delegate
        self.run_root = run_root
        self.secrets = _secret_variants(service_key)
        self.expected_public_parameters = expected_public_parameters
        self.calls = 0
        self.receipt: dict[str, object] | None = None

    def get(self, url, *, params, headers, timeout):
        if self.calls:
            raise PilotError("one-call budget exceeded")
        if url != ENDPOINT:
            raise PilotError("unexpected endpoint")
        public = {str(key): str(value) for key, value in params.items() if key != "serviceKey"}
        expected = self.expected_public_parameters
        if public != expected or "serviceKey" not in params:
            raise PilotError("unexpected request parameters")
        self.calls = 1
        response = self.delegate.get(url, params=params, headers=headers, timeout=timeout)
        body = response.content
        if not isinstance(body, bytes):
            raise PilotError("response content is not exact bytes")
        base = {
            "version": 1, "sequence": 1, "operation": "getStocIssuInfo_V3",
            "captured_at_utc": _now(), "endpoint": ENDPOINT,
            "public_parameters": expected, "http_status": int(response.status_code),
            "retry_count": 0, "response_bytes": len(body),
            "response_sha256": _sha_bytes(body),
        }
        call_path = self.run_root / "raw_call.json"
        if any(secret in body for secret in self.secrets):
            self.receipt = {**base, "raw_body_persisted": False, "event": "SECRET_ECHO_BLOCKED"}
            _atomic_json(call_path, self.receipt)
            raise PilotError("configured credential appeared in response; body not persisted")
        body_path = self.run_root / "raw_response.body"
        _atomic_bytes(body_path, body)
        self.receipt = {
            **base, "raw_body_persisted": True, "raw_body_file": body_path.name,
            "raw_call_file": call_path.name,
        }
        _atomic_json(call_path, self.receipt)
        self.receipt["raw_call_sha256"] = _sha(call_path)
        return response


def _optional_text(item: dict[str, object], field: str) -> str | None:
    value = item.get(field)
    if value is None or str(value).strip() in {"", "NULL"}:
        return None
    return str(value).strip()


def _validate_items(
    items: tuple[dict[str, object], ...], *, expected_count: int = EXPECTED_TOTAL,
    expected_snapshot_date: str | None = BASE_DATE,
) -> dict[str, object]:
    if len(items) != expected_count:
        raise PilotError("returned item count differs from the expected scope")
    canonical_rows: set[str] = set()
    reasons: set[str] = set()
    issue_dates: list[str] = []
    future_issue_dates: list[str] = []
    snapshot_dates: list[str] = []
    negative_issued_share_rows = 0
    invalid_issue_date_tokens: list[str] = []
    missing_issue_date_rows = 0
    for item in items:
        if set(item) != SOURCE_FIELDS:
            raise PilotError("source field set differs from the official guide")
        snapshot_date = str(item["basDt"])
        try:
            snapshot_parsed = datetime.strptime(snapshot_date, "%Y%m%d")
        except ValueError as error:
            raise PilotError("source basDt is invalid") from error
        if expected_snapshot_date is not None and snapshot_date != expected_snapshot_date:
            raise PilotError("source basDt differs from request")
        snapshot_dates.append(snapshot_date)
        corp = str(item["crno"]).strip()
        if re.fullmatch(r"\d{13}", corp) is None:
            raise PilotError("corporate registration number is invalid")
        isin = _optional_text(item, "isinCd")
        if isin is not None and re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}\d", isin) is None:
            raise PilotError("ISIN is invalid")
        issue_date = _optional_text(item, "stckIssuDt")
        if issue_date is not None:
            try:
                parsed = datetime.strptime(issue_date, "%Y%m%d")
            except ValueError:
                invalid_issue_date_tokens.append(issue_date)
            else:
                issue_dates.append(issue_date)
                if parsed > snapshot_parsed:
                    future_issue_dates.append(issue_date)
        else:
            missing_issue_date_rows += 1
        count = _optional_text(item, "issuStckCnt")
        if count is not None:
            if re.fullmatch(r"-?\d+", count) is None:
                raise PilotError("issued-share count is not a signed integer")
            if int(count) < 0:
                negative_issued_share_rows += 1
        reason = _optional_text(item, "stckIssuRcdNm")
        if reason is not None:
            reasons.add(reason)
        canonical_rows.add(json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    if len(canonical_rows) != len(items):
        raise PilotError("duplicate exact source items returned")
    return {
        "rows": len(items), "unique_exact_rows": len(canonical_rows),
        "issue_date_min": min(issue_dates) if issue_dates else None,
        "issue_date_max": max(issue_dates) if issue_dates else None,
        "future_effective_rows": len(future_issue_dates),
        "future_effective_date_min": min(future_issue_dates) if future_issue_dates else None,
        "future_effective_date_max": max(future_issue_dates) if future_issue_dates else None,
        "source_snapshot_date_min": min(snapshot_dates),
        "source_snapshot_date_max": max(snapshot_dates),
        "source_snapshot_date_distinct": len(set(snapshot_dates)),
        "issuance_reason_names": sorted(reasons),
        "unit_semantics": {"issuStckCnt": "shares"},
        "negative_issued_share_rows": negative_issued_share_rows,
        "signed_value_policy": "preserve exact signed source value",
        "invalid_issue_date_rows": len(invalid_issue_date_tokens),
        "invalid_issue_date_tokens": sorted(set(invalid_issue_date_tokens)),
        "missing_issue_date_rows": missing_issue_date_rows,
        "issue_date_policy": "preserve source token; parsed date is nullable with explicit status",
        "frequency_semantics": "daily source snapshot keyed by basDt; effective dates may be future",
        "predictive_use": "BLOCKED_NO_ANNOUNCEMENT_OR_PUBLICATION_TIME",
    }


def _legacy_assessment(value: dict[str, object]) -> dict[str, object]:
    """Return the exact shape used by immutable audits written before date summaries."""
    result = dict(value)
    for key in (
        "source_snapshot_date_min", "source_snapshot_date_max",
        "source_snapshot_date_distinct",
        "negative_issued_share_rows", "signed_value_policy",
        "invalid_issue_date_rows", "invalid_issue_date_tokens",
        "missing_issue_date_rows", "issue_date_policy",
    ):
        result.pop(key, None)
    return result


def run_pilot(project_root: Path, *, delegate=None) -> dict[str, object]:
    project_root = project_root.resolve()
    service_key = service_key_from_environment(project_root)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_") + uuid4().hex
    run_root = project_root / LANDING_RELATIVE / run_id
    public_parameters = {
        "basDt": BASE_DATE, "numOfRows": str(NUM_ROWS),
        "pageNo": str(PAGE_NO), "resultType": "json",
    }
    capture = CaptureSession(
        delegate or __import__("requests"), run_root, service_key, public_parameters,
    )
    status = "PILOT_STOPPED"
    assessment: dict[str, object] = {}
    error_type: str | None = None
    _assert_topology(project_root, project_root / LANDING_RELATIVE)
    _assert_topology(project_root, project_root / LOCK_RELATIVE)
    with _provider_lock(project_root, run_id):
        run_root.mkdir(parents=True, exist_ok=False)
        _assert_topology(project_root, run_root)
        try:
            result = DataGoKrClient(
                endpoint=ENDPOINT, service_key=service_key, session=capture,
                max_attempts=1, timeout_seconds=20,
            ).fetch_page(filters={"basDt": BASE_DATE}, num_of_rows=NUM_ROWS, page_no=PAGE_NO)
            landing_path = run_root / "response.json"
            write_landing_pages_atomic((result.payload,), landing_path)
            if result.total_count != EXPECTED_TOTAL:
                raise PilotError("totalCount differs from retained guide expectation")
            assessment = _validate_items(tuple(dict(item) for item in result.items))
            assessment.update({"landing_file": landing_path.name, "landing_sha256": _sha(landing_path)})
            status = "PILOT_PASSED_KNOWN_POSITIVE_SCHEMA"
        except Exception as error:
            error_type = type(error).__name__
            assessment = {"error_type": error_type}
        ledger = {
            "version": 1, "run_id": run_id, "status": status,
            "raw_requests": capture.calls, "retry_count": 0,
            "production_checkpoint_writes": False, "normalized_writes": False,
            "capture": capture.receipt, "assessment": assessment,
        }
        _atomic_json(run_root / "call_ledger.json", ledger)
        manifest = {
            **ledger, "official_guide_scope": {
                "document": "docs/api_guides/오픈API 활용자가이드_금융위원회_주식발행정보.docx",
                "operation": "getStocIssuInfo_V3", "basDt": BASE_DATE,
                "pageNo": PAGE_NO, "numOfRows": NUM_ROWS, "expected_total": EXPECTED_TOTAL,
            },
            "call_ledger_sha256": _sha(run_root / "call_ledger.json"),
            "completed_at_utc": _now(),
        }
        _atomic_json(run_root / "manifest.json", manifest)
        retained = b"".join(path.read_bytes() for path in run_root.iterdir() if path.is_file())
        if any(secret in retained for secret in _secret_variants(service_key)):
            raise PilotError("configured credential reached retained pilot evidence")
    return {
        "status": status, "run_id": run_id, "raw_requests": capture.calls,
        "retry_count": 0, "manifest_sha256": _sha(run_root / "manifest.json"),
        "run_root": str(run_root), "error_type": error_type,
    }


def run_current_scope_pilot(project_root: Path, *, delegate=None) -> dict[str, object]:
    """Make one unfiltered page-size-one call to discover bounded snapshot size."""
    project_root = project_root.resolve()
    service_key = service_key_from_environment(project_root)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_") + uuid4().hex
    run_root = project_root / CURRENT_SCOPE_LANDING_RELATIVE / run_id
    public_parameters = {"numOfRows": "1", "pageNo": "1", "resultType": "json"}
    capture = CaptureSession(
        delegate or __import__("requests"), run_root, service_key, public_parameters,
    )
    status = "CURRENT_SCOPE_STOPPED"
    assessment: dict[str, object] = {}
    error_type: str | None = None
    _assert_topology(project_root, project_root / CURRENT_SCOPE_LANDING_RELATIVE)
    _assert_topology(project_root, project_root / LOCK_RELATIVE)
    with _provider_lock(project_root, run_id):
        run_root.mkdir(parents=True, exist_ok=False)
        _assert_topology(project_root, run_root)
        try:
            result = DataGoKrClient(
                endpoint=ENDPOINT, service_key=service_key, session=capture,
                max_attempts=1, timeout_seconds=20,
            ).fetch_page(num_of_rows=1, page_no=1)
            landing_path = run_root / "response.json"
            write_landing_pages_atomic((result.payload,), landing_path)
            if result.total_count < 1 or len(result.items) != 1:
                raise PilotError("unfiltered current scope is empty or not page-size-one")
            row_assessment = _validate_items(
                tuple(dict(item) for item in result.items),
                expected_count=1, expected_snapshot_date=None,
            )
            snapshot_date = str(result.items[0]["basDt"])
            assessment = {
                "declared_total": result.total_count,
                "sampled_rows": 1,
                "source_snapshot_date": snapshot_date,
                "pages_at_9999": (result.total_count + 9998) // 9999,
                "sample_assessment": row_assessment,
                "landing_file": landing_path.name,
                "landing_sha256": _sha(landing_path),
                "backfill_authorized": False,
            }
            status = "CURRENT_SCOPE_COUNT_PASSED"
        except Exception as error:
            error_type = type(error).__name__
            assessment = {"error_type": error_type}
        ledger = {
            "version": 1, "run_id": run_id, "status": status,
            "raw_requests": capture.calls, "retry_count": 0,
            "production_checkpoint_writes": False, "normalized_writes": False,
            "capture": capture.receipt, "assessment": assessment,
        }
        _atomic_json(run_root / "call_ledger.json", ledger)
        manifest = {
            **ledger,
            "scope": {
                "operation": "getStocIssuInfo_V3", "filters": {},
                "pageNo": 1, "numOfRows": 1,
            },
            "call_ledger_sha256": _sha(run_root / "call_ledger.json"),
            "completed_at_utc": _now(),
        }
        _atomic_json(run_root / "manifest.json", manifest)
        retained = b"".join(path.read_bytes() for path in run_root.iterdir() if path.is_file())
        if any(secret in retained for secret in _secret_variants(service_key)):
            raise PilotError("configured credential reached retained current-scope evidence")
    return {
        "status": status, "run_id": run_id, "raw_requests": capture.calls,
        "retry_count": 0, "manifest_sha256": _sha(run_root / "manifest.json"),
        "run_root": str(run_root), "error_type": error_type,
        **({"declared_total": assessment["declared_total"],
            "pages_at_9999": assessment["pages_at_9999"]} if status == "CURRENT_SCOPE_COUNT_PASSED" else {}),
    }


def verify_current_scope_run(project_root: Path, run_root: Path) -> dict[str, object]:
    """Verify the current-scope count evidence with zero network calls."""
    project_root = project_root.resolve()
    expected_parent = project_root / CURRENT_SCOPE_LANDING_RELATIVE
    run_root = Path(os.path.abspath(run_root))
    _assert_topology(project_root, expected_parent)
    _assert_topology(project_root, run_root)
    if (
        run_root.parent != expected_parent
        or re.fullmatch(r"\d{8}T\d{6}Z_[0-9a-f]{32}", run_root.name) is None
    ):
        raise PilotError("run is not an exact immediate current-scope child")
    expected_files = {
        "raw_response.body", "raw_call.json", "response.json",
        "call_ledger.json", "manifest.json",
    }
    files = {path.name: _assert_plain(path) for path in run_root.iterdir()}
    if set(files) != expected_files or not all(path.is_file() for path in files.values()):
        raise PilotError("current-scope evidence topology differs")
    before = {name: _sha(path) for name, path in files.items()}
    manifest = json.loads(files["manifest.json"].read_text(encoding="utf-8"))
    ledger = json.loads(files["call_ledger.json"].read_text(encoding="utf-8"))
    if (
        manifest.get("status") != "CURRENT_SCOPE_COUNT_PASSED"
        or manifest.get("raw_requests") != 1 or manifest.get("retry_count") != 0
        or manifest.get("production_checkpoint_writes") is not False
        or manifest.get("normalized_writes") is not False
        or manifest.get("scope") != {
            "operation": "getStocIssuInfo_V3", "filters": {},
            "pageNo": 1, "numOfRows": 1,
        }
        or manifest.get("call_ledger_sha256") != before["call_ledger.json"]
        or any(manifest.get(key) != value for key, value in ledger.items())
    ):
        raise PilotError("current-scope manifest/ledger differs")
    raw_call = json.loads(files["raw_call.json"].read_text(encoding="utf-8"))
    capture = manifest.get("capture")
    expected_public = {"numOfRows": "1", "pageNo": "1", "resultType": "json"}
    if (
        not isinstance(capture, dict)
        or capture.get("raw_body_file") != "raw_response.body"
        or capture.get("raw_call_file") != "raw_call.json"
        or capture.get("raw_call_sha256") != before["raw_call.json"]
        or raw_call.get("endpoint") != ENDPOINT
        or raw_call.get("public_parameters") != expected_public
        or raw_call.get("http_status") != 200 or raw_call.get("retry_count") != 0
        or raw_call.get("response_sha256") != before["raw_response.body"]
        or raw_call.get("response_bytes") != files["raw_response.body"].stat().st_size
    ):
        raise PilotError("current-scope raw call/body differs")
    payload = json.loads(files["raw_response.body"].read_bytes())
    landing = json.loads(files["response.json"].read_text(encoding="utf-8"))
    if landing != [payload] or before["response.json"] != manifest["assessment"].get("landing_sha256"):
        raise PilotError("current-scope Landing differs")
    header, body = payload["response"]["header"], payload["response"]["body"]
    raw_items = body["items"]["item"]
    raw_items = raw_items if isinstance(raw_items, list) else [raw_items]
    total = int(body["totalCount"])
    if (
        header.get("resultCode") != "00" or body.get("pageNo") != 1
        or body.get("numOfRows") != 1 or total < 1 or len(raw_items) != 1
    ):
        raise PilotError("current-scope source envelope differs")
    rebuilt = _validate_items(
        tuple(dict(item) for item in raw_items), expected_count=1,
        expected_snapshot_date=None,
    )
    recorded_sample = manifest.get("assessment", {}).get("sample_assessment")
    if recorded_sample not in (rebuilt, _legacy_assessment(rebuilt)):
        raise PilotError("current-scope sample assessment differs from rebuild")
    expected_assessment = {
        "declared_total": total, "sampled_rows": 1,
        "source_snapshot_date": str(raw_items[0]["basDt"]),
        "pages_at_9999": (total + 9998) // 9999,
        "sample_assessment": recorded_sample,
        "landing_file": "response.json", "landing_sha256": before["response.json"],
        "backfill_authorized": False,
    }
    if manifest.get("assessment") != expected_assessment:
        raise PilotError("current-scope assessment differs from rebuild")
    key = service_key_from_environment(project_root)
    if any(secret in path.read_bytes() for path in files.values() for secret in _secret_variants(key)):
        raise PilotError("configured credential found in current-scope evidence")
    if {name: _sha(path) for name, path in files.items()} != before:
        raise PilotError("current-scope evidence changed during verification")
    return {
        "status": "OFFLINE_AUDIT_PASS", "network_requests": 0,
        "run_id": run_root.name, "declared_total": total,
        "pages_at_9999": expected_assessment["pages_at_9999"],
        "source_snapshot_date": expected_assessment["source_snapshot_date"],
        "manifest_sha256": before["manifest.json"],
    }


def verify_pilot_run(project_root: Path, run_root: Path) -> dict[str, object]:
    """Rebuild the success classification from immutable evidence, with no network."""
    project_root = project_root.resolve()
    expected_parent = project_root / LANDING_RELATIVE
    run_root = Path(os.path.abspath(run_root))
    _assert_topology(project_root, expected_parent)
    _assert_topology(project_root, run_root)
    if (
        run_root.parent != expected_parent
        or re.fullmatch(r"\d{8}T\d{6}Z_[0-9a-f]{32}", run_root.name) is None
    ):
        raise PilotError("run is not an exact immediate pilot child")
    required_files = {
        "raw_response.body", "raw_call.json", "response.json",
        "call_ledger.json", "manifest.json",
    }
    files = {path.name: _assert_plain(path) for path in run_root.iterdir()}
    if set(files) not in (required_files, required_files | {"offline_audit.json"}) or not all(
        path.is_file() for path in files.values()
    ):
        raise PilotError("successful pilot evidence topology differs")
    before = {name: _sha(path) for name, path in files.items()}
    manifest = json.loads(files["manifest.json"].read_text(encoding="utf-8"))
    ledger = json.loads(files["call_ledger.json"].read_text(encoding="utf-8"))
    if (
        manifest.get("status") not in {
            "PILOT_PASSED_KNOWN_POSITIVE_SCHEMA", "PILOT_STOPPED",
        }
        or manifest.get("raw_requests") != 1
        or manifest.get("retry_count") != 0
        or manifest.get("production_checkpoint_writes") is not False
        or manifest.get("normalized_writes") is not False
        or manifest.get("call_ledger_sha256") != before["call_ledger.json"]
        or any(manifest.get(key) != value for key, value in ledger.items())
    ):
        raise PilotError("manifest/ledger success claim differs")
    expected_scope = {
        "document": "docs/api_guides/오픈API 활용자가이드_금융위원회_주식발행정보.docx",
        "operation": "getStocIssuInfo_V3", "basDt": BASE_DATE,
        "pageNo": PAGE_NO, "numOfRows": NUM_ROWS, "expected_total": EXPECTED_TOTAL,
    }
    if manifest.get("official_guide_scope") != expected_scope:
        raise PilotError("official guide scope differs")
    capture = manifest.get("capture")
    raw_call = json.loads(files["raw_call.json"].read_text(encoding="utf-8"))
    expected_public = {
        "basDt": BASE_DATE, "numOfRows": str(NUM_ROWS),
        "pageNo": str(PAGE_NO), "resultType": "json",
    }
    if (
        not isinstance(capture, dict)
        or capture.get("raw_body_persisted") is not True
        or capture.get("raw_body_file") != "raw_response.body"
        or capture.get("raw_call_file") != "raw_call.json"
        or capture.get("raw_call_sha256") != before["raw_call.json"]
        or raw_call.get("sequence") != 1
        or raw_call.get("operation") != "getStocIssuInfo_V3"
        or raw_call.get("endpoint") != ENDPOINT
        or raw_call.get("public_parameters") != expected_public
        or raw_call.get("http_status") != 200
        or raw_call.get("retry_count") != 0
        or raw_call.get("response_sha256") != before["raw_response.body"]
        or raw_call.get("response_bytes") != files["raw_response.body"].stat().st_size
    ):
        raise PilotError("raw call/body evidence differs")
    try:
        raw_payload = json.loads(files["raw_response.body"].read_bytes())
        landing = json.loads(files["response.json"].read_text(encoding="utf-8"))
        body = raw_payload["response"]["body"]
        header = raw_payload["response"]["header"]
        raw_items = body["items"]["item"]
    except (UnicodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise PilotError("retained response JSON shape differs") from error
    if landing != [raw_payload]:
        raise PilotError("parsed Landing differs from exact response")
    if (
        manifest["status"] == "PILOT_PASSED_KNOWN_POSITIVE_SCHEMA"
        and before["response.json"] != manifest["assessment"].get("landing_sha256")
    ):
        raise PilotError("parsed Landing hash differs from the success manifest")
    if (
        header.get("resultCode") != "00"
        or body.get("pageNo") != PAGE_NO
        or body.get("numOfRows") != NUM_ROWS
        or body.get("totalCount") != EXPECTED_TOTAL
        or not isinstance(raw_items, list)
    ):
        raise PilotError("source envelope/count differs")
    rebuilt = _validate_items(tuple(dict(item) for item in raw_items))
    original_status = str(manifest["status"])
    recorded = dict(manifest["assessment"])
    if original_status == "PILOT_PASSED_KNOWN_POSITIVE_SCHEMA":
        recorded.pop("landing_file", None)
        recorded.pop("landing_sha256", None)
        if recorded != rebuilt:
            raise PilotError("recorded assessment differs from offline rebuild")
        audit_status = "OFFLINE_AUDIT_PASS"
    else:
        if recorded != {"error_type": "PilotError"}:
            raise PilotError("stopped pilot is not the exact future-effective-date case")
        expected_audit = {
            "version": 1,
            "status": "OFFLINE_AUDIT_PASS_FUTURE_EFFECTIVE_EVENT",
            "network_requests": 0,
            "original_status": "PILOT_STOPPED",
            "original_error_type": "PilotError",
            "original_file_sha256": {
                name: digest for name, digest in before.items() if name != "offline_audit.json"
            },
            "rebuilt_assessment": rebuilt,
            "interpretation": (
                "basDt is the source reference snapshot; stckIssuDt is an event effective date "
                "and may be later. Historical predictive use remains blocked."
            ),
        }
        if "offline_audit.json" not in files:
            audit_status = "OFFLINE_RECLASSIFICATION_READY"
        else:
            audit = json.loads(files["offline_audit.json"].read_text(encoding="utf-8"))
            legacy_expected_audit = dict(expected_audit)
            legacy_expected_audit["rebuilt_assessment"] = _legacy_assessment(rebuilt)
            if audit not in (expected_audit, legacy_expected_audit):
                raise PilotError("offline reclassification audit differs")
            audit_status = "OFFLINE_AUDIT_PASS_FUTURE_EFFECTIVE_EVENT"
    service_key = service_key_from_environment(project_root)
    if any(
        secret in path.read_bytes()
        for path in files.values()
        for secret in _secret_variants(service_key)
    ):
        raise PilotError("configured credential found in retained evidence")
    after = {name: _sha(path) for name, path in files.items()}
    if after != before:
        raise PilotError("retained evidence changed during offline verification")
    return {
        "status": audit_status, "network_requests": 0,
        "run_id": run_root.name, "rows": rebuilt["rows"],
        "manifest_sha256": before["manifest.json"],
    }


def finalize_stopped_pilot(project_root: Path, run_root: Path) -> dict[str, object]:
    """Append a deterministic zero-network audit; never alter original evidence."""
    project_root = project_root.resolve()
    run_root = Path(os.path.abspath(run_root))
    initial = verify_pilot_run(project_root, run_root)
    if initial["status"] == "OFFLINE_AUDIT_PASS_FUTURE_EFFECTIVE_EVENT":
        return initial
    if initial["status"] != "OFFLINE_RECLASSIFICATION_READY":
        raise PilotError("run does not require stopped-pilot finalization")
    originals = {
        path.name: _sha(path) for path in run_root.iterdir()
        if path.is_file() and path.name != "offline_audit.json"
    }
    payload = json.loads((run_root / "raw_response.body").read_bytes())
    rows = payload["response"]["body"]["items"]["item"]
    rebuilt = _validate_items(tuple(dict(item) for item in rows))
    audit = {
        "version": 1,
        "status": "OFFLINE_AUDIT_PASS_FUTURE_EFFECTIVE_EVENT",
        "network_requests": 0,
        "original_status": "PILOT_STOPPED",
        "original_error_type": "PilotError",
        "original_file_sha256": originals,
        "rebuilt_assessment": rebuilt,
        "interpretation": (
            "basDt is the source reference snapshot; stckIssuDt is an event effective date "
            "and may be later. Historical predictive use remains blocked."
        ),
    }
    _atomic_json(run_root / "offline_audit.json", audit)
    return verify_pilot_run(project_root, run_root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--confirm-live-one-call-pilot", action="store_true")
    parser.add_argument("--verify-run", type=Path)
    parser.add_argument("--finalize-stopped-run", type=Path)
    parser.add_argument("--confirm-live-current-scope-count", action="store_true")
    parser.add_argument("--verify-current-scope-run", type=Path)
    args = parser.parse_args(argv)
    if args.verify_current_scope_run is not None:
        if args.confirm_live_one_call_pilot or args.confirm_live_current_scope_count or args.verify_run or args.finalize_stopped_run:
            raise SystemExit("pilot modes are mutually exclusive")
        result = verify_current_scope_run(args.project_root, args.verify_current_scope_run)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    if args.confirm_live_current_scope_count:
        if args.confirm_live_one_call_pilot or args.verify_run or args.finalize_stopped_run:
            raise SystemExit("pilot modes are mutually exclusive")
        result = run_current_scope_pilot(args.project_root)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    if args.finalize_stopped_run is not None:
        if args.confirm_live_one_call_pilot or args.verify_run is not None:
            raise SystemExit("live, verify, and finalize modes are mutually exclusive")
        result = finalize_stopped_pilot(args.project_root, args.finalize_stopped_run)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    if args.verify_run is not None:
        if args.confirm_live_one_call_pilot:
            raise SystemExit("live and offline verification modes are mutually exclusive")
        result = verify_pilot_run(args.project_root, args.verify_run)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    if not args.confirm_live_one_call_pilot:
        raise SystemExit("live pilot requires --confirm-live-one-call-pilot")
    print(json.dumps(run_pilot(args.project_root), ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
