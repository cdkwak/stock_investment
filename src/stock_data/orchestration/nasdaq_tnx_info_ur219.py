"""UR-219's isolated one-shot Nasdaq-hosted TNX information capture."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol


URL = "https://api.nasdaq.com/api/quote/TNX/info?assetclass=index"
PUBLIC_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "User-Agent": "Mozilla/5.0",
}
STATE_PATH = Path("data/state/nasdaq_tnx_info_ur219.json")
LANDING_ROOT = Path("data/landing/nasdaq/tnx_info_ur219")
ROUTE_KEY = "NASDAQ_TNX_INFO"


class HttpResponse(Protocol):
    status_code: int
    content: bytes


@dataclass(frozen=True)
class TnxInfoResult:
    status: str
    raw_gets: int
    landing_sha256: str | None


def _write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _state(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"schema_version": 1, "operation_id": "UR-219", "routes": {}}
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "operation_id", "routes"}
        or payload["schema_version"] != 1
        or payload["operation_id"] != "UR-219"
        or not isinstance(payload["routes"], dict)
    ):
        raise RuntimeError("UR-219 durable ledger schema mismatch")
    return payload


def capture(root: Path, *, now: datetime, response_factory: Callable[[], HttpResponse] | None) -> TnxInfoResult:
    """Durably claim the sole route before the only allowed transport callback."""
    root = Path(root)
    path = root / STATE_PATH
    state = _state(path)
    routes = dict(state["routes"])
    if ROUTE_KEY in routes:
        return TnxInfoResult("NO_REPEAT", 0, None)
    if response_factory is None:
        raise RuntimeError("UR-219 response factory required")

    claim: dict[str, object] = {
        "status": "ATTEMPTING",
        "attempted_at_utc": now.astimezone(timezone.utc).isoformat(),
        "url": URL,
        "raw_gets_reserved": 1,
        "raw_gets_invoked": 0,
        "raw_gets_completed": 0,
        "retry_count": 0,
        "redirect_count": 0,
        "fallback_count": 0,
        "auth_cookie_env_used": False,
    }
    routes[ROUTE_KEY] = claim
    state["routes"] = routes
    _write(path, state)
    try:
        claim["raw_gets_invoked"] = 1
        routes[ROUTE_KEY] = claim
        state["routes"] = routes
        _write(path, state)
        response = response_factory()
        claim["raw_gets_completed"] = 1
        if response.status_code != 200:
            claim.update({
                "status": "COMPLETE_FAILURE",
                "failure_type": "HTTP_STATUS",
                "http_status": int(response.status_code),
                "raw_gets": 1,
            })
            digest = None
        else:
            body = bytes(response.content)
            digest = hashlib.sha256(body).hexdigest()
            landing = root / LANDING_ROOT / digest / "body.json"
            landing.parent.mkdir(parents=True, exist_ok=True)
            with landing.open("xb") as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            if hashlib.sha256(landing.read_bytes()).hexdigest() != digest:
                raise RuntimeError("UR-219 Landing hash readback mismatch")
            claim.update({
                "status": "COMPLETE_CAPTURED",
                "raw_gets": 1,
                "body_bytes": len(body),
                "landing_file": landing.relative_to(root).as_posix(),
                "landing_sha256": digest,
            })
    except Exception as error:
        digest = None
        claim.update({
            "status": "COMPLETE_FAILURE",
            "failure_type": type(error).__name__,
            "raw_gets": int(claim["raw_gets_invoked"]),
        })
    routes[ROUTE_KEY] = claim
    state["routes"] = routes
    _write(path, state)
    return TnxInfoResult(str(claim["status"]), int(claim["raw_gets"]), digest)


def finalize_numeric_free(root: Path, *, failure_type: str) -> TnxInfoResult:
    """Terminalize retained Landing after API-zero semantic review."""
    if not failure_type:
        raise ValueError("UR-219 failure type is required")
    path = Path(root) / STATE_PATH
    state = _state(path)
    routes = dict(state["routes"])
    claim = routes.get(ROUTE_KEY)
    if not isinstance(claim, dict) or claim.get("status") != "COMPLETE_CAPTURED":
        return TnxInfoResult("NO_REPEAT", 0, None)
    claim.update({
        "status": "COMPLETE_FAILURE",
        "failure_type": failure_type,
        "retained_schema_review_api_calls": 0,
        "raw_gets": 1,
    })
    routes[ROUTE_KEY] = claim
    state["routes"] = routes
    _write(path, state)
    if _state(path)["routes"].get(ROUTE_KEY) != claim:
        raise RuntimeError("UR-219 terminal readback mismatch")
    digest = claim.get("landing_sha256")
    return TnxInfoResult("COMPLETE_FAILURE", 0, digest if isinstance(digest, str) else None)
