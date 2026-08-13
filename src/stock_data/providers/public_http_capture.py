"""Immutable, credential-safe Landing capture for public HTTP providers.

Each request is committed as one directory containing the exact response body
and a self-describing call record.  The directory rename is the commit point,
so readers never observe a partial body/record pair.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
from typing import Callable, Mapping
from uuid import uuid4


_SAFE_COMPONENT = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
_SENSITIVE_PARAMETER = re.compile(
    r"(?i)(?:api[_-]?key|authorization|cookie|credential|password|secret|service[_-]?key|token)"
)


class PublicHttpCaptureError(RuntimeError):
    pass


@dataclass(frozen=True)
class PublicHttpCaptureReceipt:
    call_root: Path
    response_body_sha256: str
    response_bytes: int
    captured_at_utc: str


def _safe_parameters(parameters: Mapping[str, object]) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_name, raw_value in parameters.items():
        name = str(raw_name)
        if _SENSITIVE_PARAMETER.search(name):
            raise PublicHttpCaptureError(f"refusing to retain sensitive request parameter: {name}")
        result[name] = str(raw_value)
    return dict(sorted(result.items()))


def capture_public_response(
    *,
    root: Path,
    provider: str,
    operation: str,
    request_url: str,
    request_parameters: Mapping[str, object],
    response,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> PublicHttpCaptureReceipt:
    """Atomically retain one exact public response and its call record.

    The URL must not contain a query string. Request parameters are retained
    separately and rejected when their names indicate credentials.
    """
    if not _SAFE_COMPONENT.fullmatch(provider) or not _SAFE_COMPONENT.fullmatch(operation):
        raise ValueError("provider and operation must be safe lowercase path components")
    if "?" in request_url or "#" in request_url:
        raise PublicHttpCaptureError("request_url must not contain query or fragment data")
    parameters = _safe_parameters(request_parameters)
    body = response.content
    if not isinstance(body, bytes):
        raise PublicHttpCaptureError("response.content must be exact bytes")
    observed = now()
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise PublicHttpCaptureError("capture timestamp must be timezone-aware")
    captured_at = observed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    digest = hashlib.sha256(body).hexdigest()
    call_id = uuid4().hex
    stamp = observed.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    parent = root / provider / operation
    target = parent / f"{stamp}_{call_id}"
    stage = parent / f".{target.name}.stage"
    if target.exists() or stage.exists():
        raise PublicHttpCaptureError("capture identity collision")
    parent.mkdir(parents=True, exist_ok=True)
    content_type = ""
    headers = getattr(response, "headers", {})
    if isinstance(headers, Mapping):
        content_type = str(headers.get("Content-Type", ""))
    record = {
        "capture_version": 1,
        "provider": provider,
        "operation": operation,
        "captured_at_utc": captured_at,
        "request_url": request_url,
        "request_parameters": parameters,
        "http_status": int(response.status_code),
        "response_content_type": content_type,
        "response_body_sha256": digest,
        "response_bytes": len(body),
        "landing_body_file": "response.body",
    }
    try:
        stage.mkdir()
        body_path = stage / "response.body"
        with body_path.open("xb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        record_path = stage / "call.json"
        with record_path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(record, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if hashlib.sha256(body_path.read_bytes()).hexdigest() != digest:
            raise PublicHttpCaptureError("Landing response read-back differs")
        stage.replace(target)
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    return PublicHttpCaptureReceipt(target, digest, len(body), captured_at)
