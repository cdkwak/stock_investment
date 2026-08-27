"""One-shot durable capture for UR-218's exact Nasdaq VIX info route."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

ENDPOINT = "https://api.nasdaq.com/api/quote/VIX/info?assetclass=index"
STATE_PATH = Path("data/state/nasdaq_vix_info_ur218_pilot.json")
LANDING_ROOT = Path("data/landing/nasdaq/vix_info_ur218")


class HttpResponse(Protocol):
    status_code: int
    content: bytes


@dataclass(frozen=True)
class Result:
    status: str
    raw_gets: int
    body_sha256: str | None


def _write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _read(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"schema_version": 1, "operation_id": "UR-218", "operation": None}
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "operation_id", "operation"}:
        raise RuntimeError("UR-218 ledger schema mismatch")
    if payload["schema_version"] != 1 or payload["operation_id"] != "UR-218":
        raise RuntimeError("UR-218 ledger identity mismatch")
    return payload


def capture(root: Path, *, now: datetime, response_factory: Callable[[], HttpResponse] | None) -> Result:
    """Durably reserve the sole GET; retain only a successful, hash-verified body."""
    root = Path(root)
    path = root / STATE_PATH
    state = _read(path)
    if state["operation"] is not None:
        return Result("NO_REPEAT", 0, None)
    if response_factory is None:
        raise RuntimeError("UR-218 response factory required after durable preflight")
    operation: dict[str, object] = {
        "status": "ATTEMPTING", "endpoint": ENDPOINT,
        "attempted_at_utc": now.astimezone(timezone.utc).isoformat(),
        "raw_gets_reserved": 1, "raw_gets_invoked": 0, "raw_gets_completed": 0,
        "retry_count": 0, "redirect_count": 0, "fallback_count": 0,
        "auth_cookie_env_used": False,
    }
    state["operation"] = operation
    _write(path, state)
    try:
        operation["raw_gets_invoked"] = 1
        _write(path, state)
        response = response_factory()
        operation["raw_gets_completed"] = 1
        if int(response.status_code) != 200:
            operation.update({"status": "COMPLETE_FAILURE", "failure_type": "HTTP_STATUS", "http_status": int(response.status_code)})
            _write(path, state)
            return Result("COMPLETE_FAILURE", 1, None)
        body = bytes(response.content)
        digest = hashlib.sha256(body).hexdigest()
        landing = root / LANDING_ROOT / digest / "body.json"
        landing.parent.mkdir(parents=True, exist_ok=False)
        with landing.open("xb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        if hashlib.sha256(landing.read_bytes()).hexdigest() != digest:
            operation.update({"status": "COMPLETE_FAILURE", "failure_type": "LANDING_READBACK"})
            _write(path, state)
            return Result("COMPLETE_FAILURE", 1, digest)
        operation.update({"status": "LANDING_CAPTURED_PENDING_STRICT_VALIDATION", "body_bytes": len(body), "body_sha256": digest, "landing_file": landing.relative_to(root).as_posix()})
        _write(path, state)
        return Result("LANDING_CAPTURED_PENDING_STRICT_VALIDATION", 1, digest)
    except Exception as error:
        operation.update({"status": "COMPLETE_FAILURE", "failure_type": type(error).__name__})
        _write(path, state)
        return Result("COMPLETE_FAILURE", 1, None)
