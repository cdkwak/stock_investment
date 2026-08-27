"""API-zero, hash-gated summary for UR-187's retained Cboe VIX body."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

EXPECTED_SHA256 = "c7200a06987693bddb2d2b3829483e6a94ab9e84b56d296016b56d3f66b9c569"

def audit_retained_body(path: Path) -> dict[str, object]:
    body = Path(path).read_bytes()
    if hashlib.sha256(body).hexdigest() != EXPECTED_SHA256: raise ValueError("UR-187 retained body hash differs")
    payload = json.loads(body)
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict): raise ValueError("UR-187 retained schema differs")
    data = payload["data"]
    return {"sha256": EXPECTED_SHA256, "top_level_keys": tuple(sorted(payload)), "record_keys": tuple(sorted(data)), "timestamp_timezone_bound": False, "record_time_timezone_bound": False, "accepted": False, "reason": "PROVIDER_TIMESTAMP_TIMEZONE_UNBOUND"}
