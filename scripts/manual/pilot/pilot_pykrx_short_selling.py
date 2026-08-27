from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import sys
import time
from typing import Callable
from uuid import uuid4

import requests

# Direct script execution places only scripts/manual on sys.path.
ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.manual.pilot.pykrx_short_selling_pilot_support import (
    AUTH_ENDPOINT_PATHS,
    BALANCE_MARKET_FIELDS,
    BUSINESS_ENDPOINT_PATH,
    BusinessThrottle,
    BudgetExceeded,
    CredentialLeakDetected,
    HTTP_TIMEOUT_SECONDS,
    INVESTOR_FIELDS,
    MARKET_IDS,
    MARKET_TYPE_CODES,
    MAX_RAW_HTTP_REQUESTS,
    MAX_RECOVERED_RESUME_RAW_HTTP_REQUESTS,
    MAX_SECOND_RECOVERED_RESUME_RAW_HTTP_REQUESTS,
    PROBE_MATRIX,
    PROVISIONAL_FIELD_INVENTORY,
    PYKRX_VERSION,
    AppendOnlyLedger,
    PilotStopped,
    ProbeSpec,
    ResumeSafetyError,
    STOCK_SECURITY_GROUP,
    assert_no_credentials,
    classify_business_body,
    d_owned_run_lock,
    initial_checkpoint,
    landing_body_name,
    load_checkpoint,
    matrix_sha256,
    reconstruct_raw_request_count,
    recover_verified_content_type_orphan,
    redact,
    safe_url,
    utc_now,
    validate_no_orphan_artifact,
    verify_completed_artifacts,
    write_bytes_atomic_new,
    write_json_atomic,
)


D_OWNED_LOCK_PATH = ROOT / "data/state/d_owned_pykrx_short_selling_pilot.lock"
LANDING_ROOT = ROOT / "data/landing/diagnostics/pykrx_short_selling_pilot"


def _load_credentials(path: Path) -> tuple[str, str]:
    values: dict[str, str] = {}
    if path.is_file():
        for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            if key in {"KRX_ID", "KRX_PW"}:
                values[key] = value.strip().strip('"').strip("'")
    for key in ("KRX_ID", "KRX_PW"):
        if not os.getenv(key) and values.get(key):
            os.environ[key] = values[key]
    return os.getenv("KRX_ID", ""), os.getenv("KRX_PW", "")


class HttpCapture:
    """Count every raw request while retaining only exact non-auth bodies."""

    def __init__(
        self,
        *,
        ledger: AppendOnlyLedger,
        initial_count: int,
        credential_values: tuple[str, ...],
        landing_run_dir: Path | None = None,
        maximum_raw_http_requests: int = MAX_RAW_HTTP_REQUESTS,
    ) -> None:
        self.ledger = ledger
        self.count = initial_count
        self.credential_values = credential_values
        self.landing_run_dir = landing_run_dir
        self.maximum_raw_http_requests = maximum_raw_http_requests
        self.current_probe = "authentication"
        self._original: Callable | None = None
        self._patched: Callable | None = None
        self._business_responses: list[requests.Response] = []
        self._business_artifacts: list[Path | None] = []

    def __enter__(self):
        self._original = requests.Session.request

        def patched(session, method, url, **kwargs):
            return self._request(session, method, url, **kwargs)

        self._patched = patched
        requests.Session.request = patched
        return self

    def __exit__(self, exc_type, exc, traceback):
        if self._original is not None:
            requests.Session.request = self._original

    def _request(self, session, method, url, **kwargs):
        path = requests.utils.urlparse(str(url)).path
        is_auth = path in AUTH_ENDPOINT_PATHS
        if not is_auth and path != BUSINESS_ENDPOINT_PATH:
            raise PilotStopped(f"UNAPPROVED_ENDPOINT:{path}")
        if self.count >= self.maximum_raw_http_requests:
            self.ledger.append(
                "HTTP_BUDGET_EXHAUSTED", probe=self.current_probe,
                maximum_raw_http_requests=self.maximum_raw_http_requests,
            )
            raise BudgetExceeded("raw HTTP request budget exhausted")
        self.count += 1
        raw_sequence = self.count
        kwargs.setdefault("timeout", HTTP_TIMEOUT_SECONDS)
        started = time.monotonic()
        assert self._original is not None
        try:
            response = self._original(session, method, url, **kwargs)
        except Exception as error:
            self.ledger.append(
                "HTTP_ERROR", raw_sequence=raw_sequence, probe=self.current_probe,
                method=str(method).upper(), url=safe_url(str(url)),
                elapsed_ms=round((time.monotonic() - started) * 1000, 3),
                error_type=type(error).__name__, error=str(redact(str(error), self.credential_values)),
            )
            raise
        entry = {
            "raw_sequence": raw_sequence,
            "probe": self.current_probe,
            "method": str(method).upper(),
            "url": safe_url(str(url)),
            "status_code": response.status_code,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
            "response_bytes": len(response.content),
            "authentication": is_auth,
        }
        if not is_auth:
            assert_no_credentials(response.content, self.credential_values)
            entry["response_sha256"] = hashlib.sha256(response.content).hexdigest()
            artifact_path = None
            if self.landing_run_dir is not None:
                matching = [probe for probe in PROBE_MATRIX if probe.name == self.current_probe]
                if len(matching) != 1:
                    raise PilotStopped(f"UNKNOWN_BUSINESS_PROBE:{self.current_probe}")
                artifact_path = self.landing_run_dir / landing_body_name(matching[0])
                write_bytes_atomic_new(artifact_path, response.content)
                entry["body_file"] = artifact_path.name
            self._business_responses.append(response)
            self._business_artifacts.append(artifact_path)
        self.ledger.append("HTTP_RESPONSE", **entry)
        if response.status_code in {403, 429}:
            raise PilotStopped(f"HTTP_RESTRICTION:{response.status_code}:{self.current_probe}")
        if response.status_code != 200:
            raise PilotStopped(f"HTTP_ERROR_STATUS:{response.status_code}:{self.current_probe}")
        return response

    def take_single_business_response(self, probe: ProbeSpec) -> tuple[requests.Response, Path | None]:
        if len(self._business_responses) != 1 or len(self._business_artifacts) != 1:
            raise PilotStopped(
                f"BUSINESS_REQUEST_COUNT_MISMATCH:{probe.name}:{len(self._business_responses)}"
            )
        return self._business_responses.pop(), self._business_artifacts.pop()


def _execute_core_probe(core, probe: ProbeSpec):
    scope = probe.scope
    if probe.operation == "trading_market":
        return core.개별종목_공매도_거래_전종목().fetch(
            scope["date"], MARKET_IDS[str(scope["market"])], list(STOCK_SECURITY_GROUP)
        )
    if probe.operation == "balance_market":
        return core.전종목_공매도_잔고().fetch(
            scope["date"], MARKET_TYPE_CODES[str(scope["market"])]
        )
    if probe.operation == "investor":
        metric_code = {"volume": 1, "trading_value": 2}[str(scope["metric"])]
        return core.투자자별_공매도_거래().fetch(
            scope["fromdate"], scope["todate"], metric_code,
            MARKET_TYPE_CODES[str(scope["market"])],
        )
    if probe.operation == "trading_symbol":
        return core.개별종목_공매도_거래_개별추이().fetch(
            scope["fromdate"], scope["todate"], scope["isin"]
        )
    if probe.operation == "balance_symbol":
        return core.개별종목_공매도_잔고().fetch(
            scope["fromdate"], scope["todate"], scope["isin"]
        )
    if probe.operation == "status_symbol":
        return core.개별종목_공매도_종합정보().fetch(
            scope["fromdate"], scope["todate"], scope["isin"]
        )
    raise AssertionError(f"unsupported probe operation: {probe.operation}")


def _new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex


def _prepare_run_dir(run_root: Path, resume_run_dir: Path | None) -> tuple[str, Path, bool]:
    if resume_run_dir is not None:
        run_dir = resume_run_dir.resolve()
        if run_dir.parent.resolve() != run_root.resolve():
            raise ResumeSafetyError("resume directory must be an immediate child of the Landing pilot root")
        if not run_dir.is_dir():
            raise ResumeSafetyError("resume directory does not exist")
        return run_dir.name, run_dir, True
    run_id = _new_run_id()
    run_dir = run_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_id, run_dir, False


def run_pilot(
    *,
    env_file: Path,
    landing_root: Path = LANDING_ROOT,
    lock_path: Path = D_OWNED_LOCK_PATH,
    resume_run_dir: Path | None = None,
    throttle: BusinessThrottle | None = None,
) -> dict[str, object]:
    if importlib.metadata.version("pykrx") != PYKRX_VERSION:
        raise PilotStopped(f"pykrx must equal {PYKRX_VERSION}")
    krx_id, krx_pw = _load_credentials(env_file)
    if not krx_id or not krx_pw:
        raise PilotStopped("KRX credentials are not configured")
    credentials = (krx_id, krx_pw)
    run_id, run_dir, resumed = _prepare_run_dir(landing_root, resume_run_dir)
    checkpoint_path = run_dir / "checkpoint.json"
    ledger = AppendOnlyLedger(run_dir / "call_ledger.jsonl", credential_values=credentials)
    checkpoint = load_checkpoint(checkpoint_path, run_id=run_id)
    ledger_records = ledger.records()
    recovery = recover_verified_content_type_orphan(
        run_dir, checkpoint, ledger_records
    )
    recovered = None
    recovery_kind = None
    if recovery is not None:
        recovered, recovery_kind = recovery
        write_json_atomic(checkpoint_path, checkpoint)
        ledger.append(
            "PROBE_RECOVERED_FROM_VERIFIED_FALSE_POSITIVE",
            probe=PROBE_MATRIX[len(checkpoint["completed"]) - 1].name,
            recovery_kind=recovery_kind,
            **recovered,
        )
        amendment_path = run_dir / f"manifest_{recovery_kind}_recovery.json"
        if not amendment_path.exists():
            recovered_limit = (
                MAX_SECOND_RECOVERED_RESUME_RAW_HTTP_REQUESTS
                if recovery_kind == "investor_weekend_zero_placeholder"
                else MAX_RECOVERED_RESUME_RAW_HTTP_REQUESTS
            )
            write_bytes_atomic_new(
                amendment_path,
                json.dumps(
                    {
                        "run_id": run_id,
                        "recorded_at_utc": utc_now(),
                        "reason": recovery_kind,
                        "business_request_limit": len(PROBE_MATRIX),
                        "original_raw_http_request_limit": MAX_RAW_HTTP_REQUESTS,
                        "recovered_resume_raw_http_request_limit": (
                            recovered_limit
                        ),
                        "additional_business_requests_authorized": 0,
                        "additional_raw_requests": "authentication overhead only",
                    },
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ).encode("utf-8"),
            )
    verify_completed_artifacts(run_dir, checkpoint)
    raw_count = max(
        int(checkpoint.get("raw_http_requests", 0)),
        reconstruct_raw_request_count(ledger_records),
    )
    if recovery_kind == "investor_weekend_zero_placeholder":
        raw_limit = MAX_SECOND_RECOVERED_RESUME_RAW_HTTP_REQUESTS
    elif recovered is not None:
        raw_limit = MAX_RECOVERED_RESUME_RAW_HTTP_REQUESTS
    else:
        raw_limit = MAX_RAW_HTTP_REQUESTS
    if not checkpoint_path.exists():
        write_json_atomic(checkpoint_path, checkpoint)
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        write_bytes_atomic_new(
            manifest_path,
            json.dumps(
                {
                    "run_id": run_id, "created_at_utc": utc_now(),
                    "pykrx_version": PYKRX_VERSION,
                    "matrix_sha256": matrix_sha256(),
                    "business_request_limit": len(PROBE_MATRIX),
                    "raw_http_request_limit": MAX_RAW_HTTP_REQUESTS,
                    "retry_count": 0, "parallelism": 1,
                    "normalized_writes": False,
                    "provisional_field_inventory": PROVISIONAL_FIELD_INVENTORY,
                    "probes": [
                        {
                            "sequence": p.sequence, "name": p.name, "operation": p.operation,
                            "bld": p.bld, "scope": dict(p.scope), "expectation": p.expectation,
                            "required_fields": list(p.required_fields),
                        }
                        for p in PROBE_MATRIX
                    ],
                }, ensure_ascii=False, indent=2, sort_keys=True,
            ).encode("utf-8"),
        )
    else:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise ResumeSafetyError("manifest is not valid JSON") from error
        if manifest.get("run_id") != run_id or manifest.get("matrix_sha256") != matrix_sha256():
            raise ResumeSafetyError("manifest identity mismatch")
    for artifact in run_dir.rglob("*"):
        if artifact.is_file():
            assert_no_credentials(artifact.read_bytes(), credentials)
    ledger.append("RUN_RESUMED" if resumed else "RUN_CREATED", run_id=run_id, raw_http_requests=raw_count)
    throttle = throttle or BusinessThrottle()

    with d_owned_run_lock(lock_path, run_id=run_id):
        with HttpCapture(
            ledger=ledger, initial_count=raw_count, credential_values=credentials,
            landing_run_dir=run_dir,
            maximum_raw_http_requests=raw_limit,
        ) as capture:
            captured_output = io.StringIO()
            with redirect_stdout(captured_output), redirect_stderr(captured_output):
                from pykrx.website.comm import get_session
                from pykrx.website.krx.market import core
            session = get_session()
            if session is None or not getattr(session, "is_authenticated", False) or not session.is_valid():
                raise PilotStopped("AUTHENTICATION_FAILED")
            checkpoint["raw_http_requests"] = capture.count
            checkpoint["status"] = "AUTHENTICATED"
            checkpoint["updated_at_utc"] = utc_now()
            write_json_atomic(checkpoint_path, checkpoint)
            ledger.append("AUTHENTICATED", raw_http_requests=capture.count)

            completed = checkpoint["completed"]
            assert isinstance(completed, dict)
            try:
                for probe in PROBE_MATRIX:
                    if probe.name in completed:
                        ledger.append("PROBE_SKIPPED_COMPLETED", probe=probe.name)
                        continue
                    validate_no_orphan_artifact(run_dir, probe, checkpoint)
                    slept = throttle.before_business_request()
                    capture.current_probe = probe.name
                    ledger.append(
                        "PROBE_STARTED", probe=probe.name, business_sequence=probe.sequence,
                        operation=probe.operation, bld=probe.bld, scope=dict(probe.scope),
                        throttle_sleep_seconds=round(slept, 6),
                    )
                    _execute_core_probe(core, probe)
                    response, body_path = capture.take_single_business_response(probe)
                    body = response.content
                    if body_path is None:
                        raise PilotStopped(f"LANDING_BODY_NOT_PRESERVED:{probe.name}")
                    classification, rows = classify_business_body(
                        probe, body, content_type=response.headers.get("Content-Type", "")
                    )
                    body_hash = hashlib.sha256(body).hexdigest()
                    completed[probe.name] = {
                        "business_sequence": probe.sequence,
                        "classification": classification,
                        "rows": rows,
                        "body_file": body_path.name,
                        "body_sha256": body_hash,
                        "response_bytes": len(body),
                    }
                    checkpoint["raw_http_requests"] = capture.count
                    checkpoint["status"] = "IN_PROGRESS"
                    checkpoint["updated_at_utc"] = utc_now()
                    write_json_atomic(checkpoint_path, checkpoint)
                    ledger.append(
                        "PROBE_COMPLETED", probe=probe.name, classification=classification,
                        rows=rows, body_file=body_path.name, body_sha256=body_hash,
                        raw_http_requests=capture.count,
                    )
            except Exception as error:
                checkpoint["raw_http_requests"] = capture.count
                checkpoint["status"] = "STOPPED"
                checkpoint["stop_type"] = type(error).__name__
                checkpoint["stop_reason"] = redact(str(error), credentials)
                checkpoint["updated_at_utc"] = utc_now()
                write_json_atomic(checkpoint_path, checkpoint)
                ledger.append(
                    "RUN_STOPPED", error_type=type(error).__name__, error=str(error),
                    raw_http_requests=capture.count,
                )
                raise
            checkpoint["raw_http_requests"] = capture.count
            checkpoint["status"] = "COMPLETE"
            checkpoint["updated_at_utc"] = utc_now()
            write_json_atomic(checkpoint_path, checkpoint)
            ledger.append("RUN_COMPLETED", raw_http_requests=capture.count, probes=len(completed))

    for artifact in run_dir.rglob("*"):
        if artifact.is_file():
            assert_no_credentials(artifact.read_bytes(), credentials)
    return {
        "run_dir": str(run_dir), "status": checkpoint["status"],
        "raw_http_requests": checkpoint["raw_http_requests"],
        "raw_http_request_limit": raw_limit,
        "completed_probes": len(checkpoint["completed"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Landing-only bounded authenticated pykrx short-selling micro-pilot"
    )
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--resume-run-dir", type=Path)
    parser.add_argument(
        "--confirm-live-manual-pilot", action="store_true",
        help="Required explicit authorization; never set this in automated jobs.",
    )
    args = parser.parse_args()
    if not args.confirm_live_manual_pilot:
        print("Refusing to run: --confirm-live-manual-pilot is required", file=sys.stderr)
        return 2
    result = run_pilot(env_file=args.env_file, resume_run_dir=args.resume_run_dir)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
