from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any
from zoneinfo import ZoneInfo

import requests

from stock_data.pipelines.kbsec_snapshot import store_kb_market_summary_response
from stock_data.providers.kbsec.client import KBSecClient, KBSecError


STATE_SCHEMA = "stock_data.kbsec_daily_snapshot_state"
RUN_SCHEMA = "stock_data.kbsec_daily_snapshot_run"
KST = ZoneInfo("Asia/Seoul")
SENSITIVE_KEY = re.compile(r"(?i)(?:access[_-]?token|refresh[_-]?token|authorization|cookie|password|secret|app[_-]?key)")


def _body(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _redact(value: Any, secrets: tuple[str, ...]) -> Any:
    if isinstance(value, dict):
        return {str(key): "[REDACTED]" if SENSITIVE_KEY.search(str(key)) else _redact(item, secrets)
                for key, item in value.items()}
    if isinstance(value, list): return [_redact(item, secrets) for item in value]
    if isinstance(value, str):
        text = value
        for secret in secrets:
            if secret: text = text.replace(secret, "[REDACTED]")
        return re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", text)
    return value


def _response_evidence(response: requests.Response, secrets: tuple[str, ...]) -> dict[str, Any]:
    raw = bytes(response.content)
    try: parsed = response.json(); body_format = "json"
    except (TypeError, ValueError): parsed = response.text; body_format = "text"
    return {
        "received": True, "http_status": int(response.status_code),
        "content_type": str(response.headers.get("Content-Type", "")).split(";", 1)[0] or None,
        "body_format": body_format, "body_redacted": _redact(parsed, secrets),
        "raw_response_bytes": len(raw), "raw_response_sha256": _sha(raw),
        "redaction": "credential and OAuth-token values are intentionally not persisted",
    }


def _secret_scan(paths: list[Path], secrets: tuple[str, ...]) -> bool:
    needles = [secret.encode("utf-8") for secret in secrets if secret]
    return all(needle not in path.read_bytes() for path in paths for needle in needles)


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".kb-daily-", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(_body(value)); handle.flush(); os.fsync(handle.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _state_path(root: Path) -> Path:
    return root / "data/state/kbsec_daily_snapshot.json"


def _read_state(root: Path) -> dict[str, Any]:
    path = _state_path(root)
    if not path.exists():
        return {"schema": STATE_SCHEMA, "version": 1, "runs": [], "access_status": "UNKNOWN"}
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema") != STATE_SCHEMA or not isinstance(value.get("runs"), list):
        raise RuntimeError("invalid KB daily state")
    return value


def _append_state(root: Path, run: dict[str, Any]) -> dict[str, Any]:
    state = _read_state(root)
    if any(item.get("run_id") == run["run_id"] for item in state["runs"]):
        return state
    if any(item.get("capture_date_kst") == run["capture_date_kst"] for item in state["runs"]):
        raise RuntimeError("KB daily attempt already recorded for this KST date")
    state["runs"].append(run)
    state["runs"] = sorted(state["runs"], key=lambda item: (item["capture_date_kst"], item["run_id"]))
    state["access_status"] = "SENTINEL_PATH_FAILED" if run["status"] == "TOKEN_FAILED" else "ACCESS_OK"
    state["latest_run_id"] = run["run_id"]
    state["latest_capture_date_kst"] = run["capture_date_kst"]
    _atomic_json(_state_path(root), state)
    return state


def adopt_token_failure(project_root: Path, run_dir: Path) -> dict[str, Any]:
    root = project_root.resolve(); run_dir = run_dir.resolve()
    expected_parent = root / "data/landing/diagnostics/kbsec_token_pilot"
    if expected_parent not in run_dir.parents or run_dir.parent != expected_parent or run_dir.is_symlink():
        raise ValueError("token failure run is outside the exact retained root")
    checkpoint_path = run_dir / "checkpoint.json"
    ledger_path = run_dir / "call_ledger.jsonl"
    response_path = run_dir / "response.redacted.json"
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    ledger_lines = ledger_path.read_text(encoding="utf-8").splitlines()
    if checkpoint.get("status") != "TOKEN_FAILED" or checkpoint.get("request_count") != 1 or checkpoint.get("retry_count") != 0:
        raise RuntimeError("retained token run is not an exact one-call failure")
    if len(ledger_lines) != 1:
        raise RuntimeError("retained token ledger count differs")
    ledger = json.loads(ledger_lines[0])
    response = json.loads(response_path.read_text(encoding="utf-8"))
    if ledger.get("outcome") != "TOKEN_FAILED" or ledger.get("retry_count") != 0:
        raise RuntimeError("retained token ledger differs")
    if response.get("raw_response_sha256") != ledger.get("response_sha256"):
        raise RuntimeError("retained token response hash differs")
    completed = datetime.fromisoformat(str(checkpoint["completed_at_utc"]).replace("Z", "+00:00"))
    run = {
        "run_id": checkpoint["run_id"],
        "capture_date_kst": completed.astimezone(KST).date().isoformat(),
        "captured_at_utc": checkpoint["completed_at_utc"],
        "status": "TOKEN_FAILED",
        "provider_error": {
            "http_status": response.get("http_status"),
            "result_code": response.get("body_redacted", {}).get("dataHeader", {}).get("resultCode"),
            "process_code": response.get("body_redacted", {}).get("dataHeader", {}).get("processCode"),
        },
        "request_count": 1,
        "retry_count": 0,
        "market_request_count": 0,
        "response_sha256": response.get("raw_response_sha256"),
        "landing_run": run_dir.relative_to(root).as_posix(),
        "normalized_writes": False,
    }
    state = _append_state(root, run)
    return {"status": "ADOPTED_TOKEN_FAILURE", "run": run, "state_runs": len(state["runs"])}


class DailyCaptureSession(requests.Session):
    def __init__(self) -> None:
        super().__init__(); self.calls: list[dict[str, Any]] = []

    def post(self, url: str, **kwargs: Any) -> requests.Response:
        if len(self.calls) >= 2:
            raise RuntimeError("KB daily two-call cap exceeded")
        expected = "/oauth2/token" if not self.calls else "/api/v1/ivsa0070"
        if not url.endswith(expected):
            raise RuntimeError("unexpected KB daily endpoint sequence")
        response = super().post(url, **kwargs)
        self.calls.append({"operation": expected.removeprefix("/"), "response": response})
        return response


def collect_daily_snapshot(
    project_root: Path,
    *,
    known_secrets: tuple[str, ...],
    confirm_access_restored: bool = False,
    now: datetime | None = None,
    session: DailyCaptureSession | None = None,
) -> dict[str, Any]:
    root = project_root.resolve()
    lock = root / "data/state/locks/kbsec_daily_snapshot.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return {"status": "NOT_EXECUTED_LOCKED", "network_calls": 0}
    try:
        os.write(descriptor, _body({"pid": os.getpid(), "acquired_at_utc": datetime.now(timezone.utc).isoformat()}))
        os.close(descriptor)
        return _collect_daily_snapshot(
            root, known_secrets=known_secrets, confirm_access_restored=confirm_access_restored,
            now=now, session=session,
        )
    finally:
        lock.unlink(missing_ok=True)


def _collect_daily_snapshot(
    project_root: Path,
    *,
    known_secrets: tuple[str, ...],
    confirm_access_restored: bool,
    now: datetime | None,
    session: DailyCaptureSession | None,
) -> dict[str, Any]:
    root = project_root.resolve(); observed = now or datetime.now(timezone.utc)
    local = observed.astimezone(KST); state = _read_state(root)
    if state.get("access_status") in {"ACCESS_BLOCKED", "SENTINEL_PATH_FAILED"} and not confirm_access_restored:
        return {"status": "NOT_EXECUTED_AUTH_REVIEW_REQUIRED", "network_calls": 0}
    if any(item.get("capture_date_kst") == local.date().isoformat() for item in state["runs"]):
        return {"status": "NOT_EXECUTED_ALREADY_ATTEMPTED_TODAY", "network_calls": 0}
    if local.weekday() >= 5:
        return {"status": "NOT_EXECUTED_NON_TRADING_WEEKDAY", "network_calls": 0}
    minute = local.hour * 60 + local.minute
    if not 16 * 60 + 30 <= minute <= 18 * 60:
        return {"status": "NOT_EXECUTED_OUTSIDE_1630_1800_KST", "network_calls": 0}

    run_id = observed.strftime("%Y%m%dT%H%M%SZ") + "_daily"
    run_dir = root / "data/landing/kbsec/daily_snapshot" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    session = session or DailyCaptureSession(); status = "STARTED"; error_type = None
    response = None; counts: dict[str, int] = {}; market_date = None
    try:
        response = KBSecClient(session=session).market_summary()
        market_call = session.calls[-1]["response"]
        raw = bytes(market_call.content)
        for secret in known_secrets:
            if secret and secret.encode("utf-8") in raw:
                raise RuntimeError("credential appeared in KB market response")
        (run_dir / "market_response.body").write_bytes(raw)
        _atomic_json(run_dir / "market_response.json", response.raw_payload)
        market_date = str(response.data_body.get("inq_dy_tm", ""))[:8]
        provenance = {
            "schema": RUN_SCHEMA + ".provenance", "run_id": run_id,
            "captured_at_utc": observed.isoformat(), "capture_date_kst": local.date().isoformat(),
            "market_date_source": market_date, "operation": "IVSA0070",
            "raw_response_bytes": len(raw), "raw_response_sha256": _sha(raw),
            "unknown_source_fields_preserved_in_raw": True,
        }
        _atomic_json(run_dir / "provenance.json", provenance)
        status = "RAW_VALIDATED"
        counts = store_kb_market_summary_response(root, response=response, collected_at=observed)
        status = "COMPLETE"
    except KBSecError as error:
        error_type = type(error).__name__; status = "TOKEN_FAILED" if len(session.calls) == 1 else "MARKET_FAILED"
    finally:
        ledger = []
        for sequence, call in enumerate(session.calls, 1):
            evidence = _response_evidence(call["response"], known_secrets)
            _atomic_json(run_dir / f"response_{sequence:02d}.redacted.json", evidence)
            ledger.append({
                "sequence": sequence, "operation": call["operation"], "retry_count": 0,
                "http_status": evidence.get("http_status"), "raw_response_bytes": evidence.get("raw_response_bytes"),
                "raw_response_sha256": evidence.get("raw_response_sha256"),
            })
        (run_dir / "call_ledger.jsonl").write_text(
            "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in ledger), encoding="utf-8"
        )
        checkpoint = {
            "schema": RUN_SCHEMA + ".checkpoint", "run_id": run_id, "status": status,
            "capture_date_kst": local.date().isoformat(), "captured_at_utc": observed.isoformat(),
            "market_date_source": market_date, "request_count": len(session.calls), "retry_count": 0,
            "normalized_counts": counts, "error_type": error_type,
        }
        _atomic_json(run_dir / "checkpoint.json", checkpoint)
        if not _secret_scan(list(run_dir.iterdir()), known_secrets):
            raise RuntimeError("secret detected in KB daily artifacts")
    run = {
        "run_id": run_id, "capture_date_kst": local.date().isoformat(),
        "captured_at_utc": observed.isoformat(), "status": status,
        "request_count": len(session.calls), "retry_count": 0,
        "market_request_count": sum(call["operation"] == "api/v1/ivsa0070" for call in session.calls),
        "market_date_source": market_date, "landing_run": run_dir.relative_to(root).as_posix(),
        "normalized_counts": counts, "normalized_writes": status == "COMPLETE",
    }
    _append_state(root, run)
    return run
