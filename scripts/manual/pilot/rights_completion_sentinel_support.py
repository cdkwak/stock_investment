"""Exactly-one-call completion sentinel for the retained FSC Rights snapshot."""
from __future__ import annotations

import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote
from uuid import uuid4

import requests

from stock_data.providers.data_go_kr.client import DataGoKrClient
from stock_data.providers.data_go_kr.data_v1 import (
    ENDPOINTS,
    RIGHTS_SOURCE_FIELDS,
    normalize_rights,
)


TASK_ID = "B002-P3"
OPERATION = "GetStocRighScheService_V2/getRighExerReasSche_V2"
FILTERS = {"basDt": "20191231", "issuCmpyKsdCustNo": "1115"}
PAGE_NO = 1
NUM_ROWS = 12
EXPECTED_TOTAL = 12


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class OneCallSession:
    def __init__(self, delegate: Any = requests) -> None:
        self.delegate = delegate
        self.request_count = 0
        self.response: Any | None = None

    def get(self, url: str, **kwargs: Any) -> Any:
        if self.request_count:
            raise RuntimeError("Rights completion sentinel one-call cap exceeded")
        self.request_count = 1
        self.response = self.delegate.get(url, **kwargs)
        return self.response


@contextmanager
def provider_lock(path: Path, *, run_id: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise RuntimeError("data.go.kr provider lock already exists") from error
    try:
        os.write(descriptor, json.dumps({"run_id": run_id, "pid": os.getpid()}).encode("utf-8"))
        os.close(descriptor)
        descriptor = -1
        yield
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            owner = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise RuntimeError("data.go.kr provider lock ownership cannot be verified") from error
        if owner.get("run_id") != run_id:
            raise RuntimeError("data.go.kr provider lock ownership changed")
        path.unlink()


def _credential_forms(service_key: str) -> tuple[bytes, ...]:
    values = {service_key, unquote(service_key)}
    return tuple(value.encode("utf-8") for value in values if value)


def _classify(raw: bytes, *, service_key: str) -> tuple[str, dict[str, Any]]:
    if any(value in raw for value in _credential_forms(service_key)):
        return "SECRET_ECHO_SAFETY_STOP", {}
    try:
        payload = json.loads(raw)
        response = payload["response"]
        header = response["header"]
        body = response["body"]
        items = body["items"]["item"]
    except (UnicodeError, json.JSONDecodeError, KeyError, TypeError):
        return "RESPONSE_SHAPE_STOP", {}
    if header.get("resultCode") != "00":
        return "SOURCE_ERROR_STOP", payload
    if (
        body.get("pageNo") != PAGE_NO
        or body.get("numOfRows") != NUM_ROWS
        or body.get("totalCount") != EXPECTED_TOTAL
        or not isinstance(items, list)
        or len(items) != EXPECTED_TOTAL
    ):
        return "PARTIAL_OR_AMBIGUOUS_STOP", payload
    expected_fields = set(RIGHTS_SOURCE_FIELDS)
    if any(not isinstance(item, dict) or set(item) != expected_fields for item in items):
        return "SOURCE_SCHEMA_STOP", payload
    if any(
        str(item.get("basDt")) != FILTERS["basDt"]
        or str(item.get("issuCmpyKsdCustNo")) != FILTERS["issuCmpyKsdCustNo"]
        for item in items
    ):
        return "SOURCE_IDENTITY_STOP", payload
    fingerprints = {
        json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for item in items
    }
    if len(fingerprints) != EXPECTED_TOTAL:
        return "DUPLICATE_SOURCE_RECORD_STOP", payload
    try:
        normalize_rights(
            items,
            landing_response_body_sha256=_sha(raw),
            source_page_no=PAGE_NO,
        )
    except Exception:
        return "PARSER_OR_CONTRACT_STOP", payload
    return "SOURCE_SNAPSHOT_COMPLETE", payload


def run_completion_sentinel(
    *,
    project_root: Path,
    service_key: str,
    delegate: Any = requests,
    now_fn: Callable[[], datetime] = _now,
) -> dict[str, Any]:
    project_root = project_root.resolve()
    started = now_fn()
    run_id = "b002_p3_" + started.strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex
    run_dir = project_root / "data/landing/diagnostics/b002_p3_rights" / run_id
    state_path = project_root / "data/state/diagnostics/b002_p3_rights" / f"{run_id}.json"
    lock_path = project_root / "data/state/.data_go_kr_network.lock"
    session = OneCallSession(delegate)
    error_type = None
    with provider_lock(lock_path, run_id=run_id):
        try:
            client = DataGoKrClient(
                endpoint=ENDPOINTS["rights_https"], service_key=service_key,
                session=session, max_attempts=1,
            )
            client.fetch_page(filters=FILTERS, num_of_rows=NUM_ROWS, page_no=PAGE_NO)
        except Exception as error:
            error_type = type(error).__name__
        completed = now_fn()

        response = session.response
        raw = bytes(response.content) if response is not None else b""
        http_status = int(response.status_code) if response is not None else None
        classification, payload = _classify(raw, service_key=service_key) if response is not None else ("NO_RESPONSE_STOP", {})
        if error_type is not None and classification == "SOURCE_SNAPSHOT_COMPLETE":
            classification = "CLIENT_VALIDATION_STOP"
        raw_to_store = raw
        for credential in _credential_forms(service_key):
            raw_to_store = raw_to_store.replace(credential, b"[REDACTED]")
        body_sha = _sha(raw)
        envelope = {
            "task_id": TASK_ID, "run_id": run_id, "http_status": http_status,
            "response_body_encoding": "base64",
            "response_body_base64": base64.b64encode(raw_to_store).decode("ascii"),
            "response_body_bytes": len(raw), "response_body_sha256": body_sha,
            "credential_echo_redacted": raw_to_store != raw,
        }
        envelope_path = run_dir / "response_envelope.json"
        ledger_path = run_dir / "call_ledger.redacted.json"
        _atomic_json(envelope_path, envelope)
        body = payload.get("response", {}).get("body", {}) if isinstance(payload, dict) else {}
        header = payload.get("response", {}).get("header", {}) if isinstance(payload, dict) else {}
        items = body.get("items", {}).get("item", []) if isinstance(body, dict) else []
        ledger = {
            "task_id": TASK_ID, "run_id": run_id, "classification": classification,
            "authorized_operation": OPERATION, "endpoint": ENDPOINTS["rights_https"],
            "request": {**FILTERS, "numOfRows": NUM_ROWS, "pageNo": PAGE_NO, "resultType": "json"},
            "request_count": session.request_count, "retries": 0,
            "started_at": _iso(started), "completed_at": _iso(completed),
            "http_status": http_status, "json_parseable": bool(payload),
            "result_code": header.get("resultCode") if isinstance(header, dict) else None,
            "result_message": header.get("resultMsg") if isinstance(header, dict) else None,
            "total_count": body.get("totalCount") if isinstance(body, dict) else None,
            "returned_item_count": len(items) if isinstance(items, list) else None,
            "response_body_bytes": len(raw), "response_body_sha256": body_sha,
            "transport_error_type": error_type,
            "service_key_or_prepared_query_stored": False,
        }
        _atomic_json(ledger_path, ledger)
        checkpoint = {
            "task_id": TASK_ID, "run_id": run_id, "status": classification,
            "request_count": session.request_count, "retry_count": 0,
            "http_status": http_status, "response_body_sha256": body_sha,
            "completed_at": _iso(completed),
        }
        _atomic_json(state_path, checkpoint)
        handoff = {
            "task_id": TASK_ID, "run_id": run_id, "classification": classification,
            "request_count": session.request_count, "retries": 0,
            "response_body_sha256": body_sha,
            "envelope_path": envelope_path.relative_to(project_root).as_posix(),
            "envelope_sha256": _sha(envelope_path.read_bytes()),
            "ledger_path": ledger_path.relative_to(project_root).as_posix(),
            "ledger_sha256": _sha(ledger_path.read_bytes()),
            "checkpoint_path": state_path.relative_to(project_root).as_posix(),
            "checkpoint_sha256": _sha(state_path.read_bytes()),
            "lock_action": "RELEASED_AFTER_SINGLE_CALL",
        }
        _atomic_json(run_dir / "handoff_manifest.json", handoff)
        persisted = b"".join(path.read_bytes() for path in (*run_dir.iterdir(), state_path))
        if any(credential in persisted for credential in _credential_forms(service_key)):
            raise RuntimeError("credential value reached persisted Rights evidence")
    return {
        "status": classification, "run_id": run_id,
        "request_count": session.request_count, "retry_count": 0,
        "http_status": http_status, "response_body_sha256": body_sha,
        "diagnostic_root": run_dir,
    }
