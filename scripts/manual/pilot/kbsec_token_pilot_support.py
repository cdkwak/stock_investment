"""Fail-closed, one-call KB Securities OAuth access pilot support."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any

import requests


SCHEMA = "stock_data.kbsec_token_pilot"
SENSITIVE_KEY = re.compile(
    r"(?i)(?:access[_-]?token|refresh[_-]?token|authorization|cookie|credential|"
    r"password|secret|app[_-]?key)"
)
BEARER = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    temporary.write_bytes(json_bytes(value))
    os.replace(temporary, path)


def _sensitive_values(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            if SENSITIVE_KEY.search(str(key)) and isinstance(item, str) and item:
                found.add(item)
            found.update(_sensitive_values(item))
    elif isinstance(value, list):
        for item in value:
            found.update(_sensitive_values(item))
    return found


def redact(value: Any, *, known_secrets: tuple[str, ...] = ()) -> Any:
    discovered = _sensitive_values(value)
    secrets = tuple(item for item in (*known_secrets, *discovered) if item)

    def visit(item: Any, key: str | None = None) -> Any:
        if key is not None and SENSITIVE_KEY.search(key):
            return "[REDACTED]"
        if isinstance(item, dict):
            return {str(k): visit(v, str(k)) for k, v in item.items()}
        if isinstance(item, list):
            return [visit(v) for v in item]
        if isinstance(item, str):
            text = item
            for secret in secrets:
                text = text.replace(secret, "[REDACTED]")
            return BEARER.sub("Bearer [REDACTED]", text)
        return item

    return visit(value)


class OneCallCaptureSession(requests.Session):
    """Requests session that permits and retains at most one response."""

    def __init__(self) -> None:
        super().__init__()
        self.request_count = 0
        self.captured_response: requests.Response | None = None

    def post(self, url: str, **kwargs: Any) -> requests.Response:
        if self.request_count >= 1:
            raise RuntimeError("KB token pilot one-call cap exceeded")
        self.request_count += 1
        response = super().post(url, **kwargs)
        self.captured_response = response
        return response


def response_evidence(
    response: requests.Response | None,
    *,
    known_secrets: tuple[str, ...],
) -> dict[str, Any]:
    if response is None:
        return {"received": False}
    raw = bytes(response.content)
    content_type = str(response.headers.get("Content-Type", "")).split(";", 1)[0] or None
    try:
        parsed: Any = response.json()
        body_format = "json"
    except (TypeError, ValueError):
        parsed = response.text
        body_format = "text"
    safe_body = redact(parsed, known_secrets=known_secrets)
    return {
        "received": True,
        "http_status": int(response.status_code),
        "content_type": content_type,
        "body_format": body_format,
        "body_redacted": safe_body,
        "raw_response_bytes": len(raw),
        "raw_response_sha256": hashlib.sha256(raw).hexdigest(),
        "redaction": "credential and OAuth-token values are intentionally not persisted",
    }


def secret_scan(paths: list[Path], secrets: tuple[str, ...]) -> bool:
    needles = [item.encode("utf-8") for item in secrets if item]
    return all(needle not in path.read_bytes() for path in paths for needle in needles)
