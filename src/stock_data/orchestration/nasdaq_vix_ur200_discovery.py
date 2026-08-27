"""Durable, two-step official Nasdaq VIX route discovery for UR-200."""
from __future__ import annotations
import hashlib, json, os, uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

PAGE_URL = "https://www.nasdaq.com/market-activity/index/vix"
PUBLIC_HEADERS = {"Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "User-Agent": "Mozilla/5.0"}
STATE_PATH = Path("data/state/nasdaq_vix_ur200_discovery.json")
LANDING_ROOT = Path("data/landing/nasdaq/vix_ur200_discovery")

class HttpResponse(Protocol):
    status_code: int
    content: bytes

@dataclass(frozen=True)
class DiscoveryResult:
    status: str; operation: str; raw_gets: int; body_sha256: str | None

def _write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try: temporary.unlink()
        except FileNotFoundError: pass

def _state(path: Path) -> dict[str, object]:
    try: payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError: return {"schema_version": 1, "operation_id": "UR-200", "operations": {}}
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "operation_id", "operations"} or payload["schema_version"] != 1 or payload["operation_id"] != "UR-200" or not isinstance(payload["operations"], dict): raise RuntimeError("UR-200 discovery ledger schema mismatch")
    return payload

def capture_page(root: Path, *, now: datetime, response_factory: Callable[[], HttpResponse] | None) -> DiscoveryResult:
    root = Path(root); path = root / STATE_PATH; state = _state(path); ops = dict(state["operations"]); operation = "OFFICIAL_VIX_HTML"
    if operation in ops: return DiscoveryResult("NO_REPEAT", operation, 0, None)
    if response_factory is None: raise RuntimeError("UR-200 official-page response factory required")
    claim: dict[str, object] = {"status": "ATTEMPTING", "attempted_at_utc": now.astimezone(timezone.utc).isoformat(), "url": PAGE_URL, "raw_gets_reserved": 1, "raw_gets_invoked": 0, "raw_gets_completed": 0, "retry_count": 0, "redirect_count": 0, "auth_cookie_env_used": False}
    ops[operation] = claim; state["operations"] = ops; _write(path, state)
    try:
        claim["raw_gets_invoked"] = 1; ops[operation] = claim; state["operations"] = ops; _write(path, state)
        response = response_factory(); claim["raw_gets_completed"] = 1
        if response.status_code != 200: claim.update({"status": "COMPLETE_FAILURE", "failure_type": "HTTP_STATUS", "http_status": int(response.status_code), "raw_gets": 1}); digest = None
        else:
            body = bytes(response.content); digest = hashlib.sha256(body).hexdigest(); landing = root / LANDING_ROOT / digest / "page.html"; landing.parent.mkdir(parents=True, exist_ok=True)
            with landing.open("xb") as stream: stream.write(body); stream.flush(); os.fsync(stream.fileno())
            if hashlib.sha256(landing.read_bytes()).hexdigest() != digest: raise RuntimeError("UR-200 Landing hash readback mismatch")
            claim.update({"status": "COMPLETE_CAPTURED", "raw_gets": 1, "body_bytes": len(body), "landing_file": landing.relative_to(root).as_posix(), "landing_sha256": digest})
    except Exception as error:
        digest = None; claim.update({"status": "COMPLETE_FAILURE", "failure_type": type(error).__name__, "raw_gets": int(claim["raw_gets_invoked"])})
    ops[operation] = claim; state["operations"] = ops; _write(path, state)
    return DiscoveryResult(str(claim["status"]), operation, int(claim["raw_gets"]), digest)
