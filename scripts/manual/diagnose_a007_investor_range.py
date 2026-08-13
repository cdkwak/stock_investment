"""Exactly-one-call diagnostic for A007 Investor multi-day range semantics.

This utility is Landing-only.  It must not be used while another D-owned KRX
stream is active and it never resumes or mutates the A007 checkpoint.
"""

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
from urllib3.util.retry import Retry


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.manual import a007_investor_range_diagnostic_support as default_support
from scripts.manual.a007_investor_range_diagnostic_support import (
    BUSINESS_BLD,
    BUSINESS_ENDPOINT_PATH,
    EXPECTED_DATES,
    MAX_BUSINESS_REQUESTS,
    MAX_RAW_HTTP_REQUESTS,
    PYKRX_VERSION,
    SCOPE,
    SCOPE_ID,
    classify_response,
    manifest_payload,
    scope_sha256,
)
from scripts.manual.pykrx_short_selling_pilot_support import (
    AUTH_ENDPOINT_PATHS,
    AppendOnlyLedger,
    BudgetExceeded,
    PilotStopped,
    ResumeSafetyError,
    assert_no_credentials,
    d_owned_run_lock,
    redact,
    safe_url,
    utc_now,
    write_bytes_atomic_new,
)


LANDING_ROOT = ROOT / "data/landing/diagnostics/a007_investor_range"
D_OWNED_LOCK_PATH = ROOT / "data/state/d_owned_krx_short_selling.lock"
BODY_NAME = "response.json"
PROVENANCE_NAME = "response.json.provenance.json"
LEDGER_NAME = "call_ledger.jsonl"
MANIFEST_NAME = "manifest.json"


def _load_credentials(path: Path) -> tuple[str, str]:
    values: dict[str, str] = {}
    if path.is_file():
        for raw in path.read_text(encoding="utf-8-sig").splitlines():
            if "=" in raw and not raw.lstrip().startswith("#"):
                key, value = raw.split("=", 1)
                if key.strip() in {"KRX_ID", "KRX_PW"}:
                    values[key.strip()] = value.strip().strip("\"'")
    for key in ("KRX_ID", "KRX_PW"):
        if not os.getenv(key) and values.get(key):
            os.environ[key] = values[key]
    return os.getenv("KRX_ID", ""), os.getenv("KRX_PW", "")


def _new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex


def _atomic_json_new(path: Path, payload: object) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    write_bytes_atomic_new(path, encoded)


def _project_relative(path: Path, project_root: Path) -> str:
    try:
        return path.resolve().relative_to(project_root.resolve()).as_posix()
    except ValueError as error:
        raise ResumeSafetyError("diagnostic artifact path escapes project root") from error


class HttpCapture:
    """Fail-closed raw-request budget and immutable business response capture."""

    def __init__(
        self, *, ledger: AppendOnlyLedger, run_dir: Path, project_root: Path,
        credential_values: tuple[str, ...], run_id: str,
        diagnostic_support=default_support,
        expected_dates: tuple[str, ...] = EXPECTED_DATES,
    ) -> None:
        self.ledger = ledger
        self.run_dir = run_dir
        self.project_root = project_root
        self.credential_values = credential_values
        self.run_id = run_id
        self.support = diagnostic_support
        self.expected_dates = expected_dates
        self.raw_count = 0
        self.business_count = 0
        self._original: Callable | None = None
        self._response: requests.Response | None = None
        self._business_session: requests.Session | None = None

    def __enter__(self):
        self._original = requests.Session.request

        def patched(session, method, url, **kwargs):
            return self._request(session, method, url, **kwargs)

        requests.Session.request = patched
        return self

    def __exit__(self, *unused):
        if self._original is not None:
            requests.Session.request = self._original

    def authorize_business_session(self, session: requests.Session) -> None:
        self._business_session = session

    def _verify_business_request(
        self, session: requests.Session, method: object, kwargs: dict[str, object]
    ) -> None:
        expected = getattr(self.support, "EXPECTED_BUSINESS_DATA", None)
        if expected is None:
            return
        if session is not self._business_session:
            raise PilotStopped("BUSINESS_SESSION_MISMATCH")
        if str(method).upper() != "POST":
            raise PilotStopped("BUSINESS_METHOD_MISMATCH")
        if kwargs.get("params") not in (None, {}):
            raise PilotStopped("BUSINESS_QUERY_PARAMS_FORBIDDEN")
        if kwargs.get("json") is not None:
            raise PilotStopped("BUSINESS_JSON_BODY_FORBIDDEN")
        supplied = kwargs.get("data")
        if not isinstance(supplied, dict):
            raise PilotStopped("BUSINESS_DATA_MISSING")
        normalized = {str(key): str(value) for key, value in supplied.items()}
        if normalized != expected:
            raise PilotStopped("BUSINESS_DATA_MISMATCH")

    def _request(self, session, method, url, **kwargs):
        path = requests.utils.urlparse(str(url)).path
        authentication = path in AUTH_ENDPOINT_PATHS
        if not authentication and path != self.support.BUSINESS_ENDPOINT_PATH:
            raise PilotStopped(f"UNAPPROVED_ENDPOINT:{path}")
        if not authentication:
            # Validate the complete transaction before counters or network I/O.
            self._verify_business_request(session, method, kwargs)
        if self.raw_count >= self.support.MAX_RAW_HTTP_REQUESTS:
            self.ledger.append(
                "HTTP_BUDGET_EXHAUSTED", maximum=self.support.MAX_RAW_HTTP_REQUESTS
            )
            raise BudgetExceeded("raw HTTP budget exhausted")
        if (
            not authentication
            and self.business_count >= self.support.MAX_BUSINESS_REQUESTS
        ):
            self.ledger.append(
                "BUSINESS_BUDGET_EXHAUSTED",
                maximum=self.support.MAX_BUSINESS_REQUESTS,
            )
            raise BudgetExceeded("business request budget exhausted")
        self.raw_count += 1
        if not authentication:
            self.business_count += 1
            # A redirect would be a second business transaction hidden inside
            # requests' redirect handling.  Preserve the exactly-one-call cap.
            kwargs["allow_redirects"] = False
        raw_sequence = self.raw_count
        kwargs.setdefault("timeout", 20)
        started = time.monotonic()
        assert self._original is not None
        try:
            response = self._original(session, method, url, **kwargs)
        except Exception as error:
            self.ledger.append(
                "HTTP_ERROR", raw_sequence=raw_sequence, authentication=authentication,
                method=str(method).upper(), url=safe_url(str(url)),
                error_type=type(error).__name__,
                error=redact(str(error), self.credential_values),
            )
            raise
        entry: dict[str, object] = {
            "authentication": authentication,
            "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
            "method": str(method).upper(),
            "raw_sequence": raw_sequence,
            "response_bytes": len(response.content),
            "status_code": response.status_code,
            "url": safe_url(str(url)),
        }
        if not authentication:
            assert_no_credentials(response.content, self.credential_values)
            body_path = self.run_dir / BODY_NAME
            provenance_path = self.run_dir / PROVENANCE_NAME
            body_hash = hashlib.sha256(response.content).hexdigest()
            write_bytes_atomic_new(body_path, response.content)
            provenance = {
                "body_sha256": body_hash,
                "captured_at_utc": utc_now(),
                "content_type": response.headers.get("Content-Type", ""),
                "dataset": "kr_short_selling_investor_daily",
                "expected_dates": list(self.expected_dates),
                "http_status_code": response.status_code,
                "ledger_relative_path": _project_relative(self.ledger.path, self.project_root),
                "raw_sequence": raw_sequence,
                "response_bytes": len(response.content),
                "run_id": self.run_id,
                "scope_id": self.support.SCOPE_ID,
                "scope_sha256": self.support.scope_sha256(self.expected_dates),
                "version": 1,
            }
            _atomic_json_new(provenance_path, provenance)
            entry.update({
                "body_file": BODY_NAME,
                "provenance_file": PROVENANCE_NAME,
                "response_sha256": body_hash,
                "scope": self.support.SCOPE_ID,
            })
            self._response = response
        self.ledger.append("HTTP_RESPONSE", **entry)
        if response.status_code in {403, 429}:
            raise PilotStopped(f"HTTP_RESTRICTION:{response.status_code}")
        if response.status_code != 200:
            raise PilotStopped(f"HTTP_ERROR_STATUS:{response.status_code}")
        return response

    def take_exact_response(self) -> requests.Response:
        if self.business_count != 1 or self._response is None:
            raise PilotStopped(f"BUSINESS_REQUEST_COUNT_MISMATCH:{self.business_count}")
        return self._response


def _default_session_getter():
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        from pykrx.website.comm import get_session
    return get_session()


def _zero_retry() -> Retry:
    return Retry(
        total=0, connect=0, read=0, redirect=0, status=0, other=0,
        allowed_methods=frozenset(), raise_on_redirect=False, raise_on_status=False,
    )


def _retry_is_enabled(retry: Retry) -> bool:
    if retry.total not in (0, False):
        return True
    dimensions = (
        retry.connect, retry.read, retry.redirect, retry.status,
        getattr(retry, "other", 0),
    )
    # With total=0, urllib3's inherited None dimensions cannot retry. They are
    # replaced by explicit zeroes immediately after this preflight.
    return any(value not in (None, 0, False) for value in dimensions)


def _install_verified_zero_retry(session: requests.Session) -> None:
    if not isinstance(session, requests.Session):
        raise PilotStopped("AUTH_SESSION_TYPE_MISMATCH")
    for prefix, adapter in session.adapters.items():
        retry = getattr(adapter, "max_retries", None)
        if not isinstance(retry, Retry) or _retry_is_enabled(retry):
            raise PilotStopped(f"RETRY_ENABLED_OR_UNKNOWN:{prefix}")
    strict = _zero_retry()
    for adapter in session.adapters.values():
        adapter.max_retries = strict
    for prefix, adapter in session.adapters.items():
        retry = getattr(adapter, "max_retries", None)
        if not isinstance(retry, Retry) or _retry_is_enabled(retry):
            raise PilotStopped(f"ZERO_RETRY_INSTALL_FAILED:{prefix}")


def _authenticated_transport(value: object) -> requests.Session:
    transport = getattr(value, "session", None)
    if not isinstance(transport, requests.Session):
        raise PilotStopped("AUTH_TRANSPORT_SESSION_TYPE_MISMATCH")
    return transport


def _default_execute_probe(diagnostic_support=default_support) -> None:
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        from pykrx.website.krx.market import core
    candidates = []
    for value in vars(core).values():
        if isinstance(value, type):
            try:
                operation = value()
                if getattr(operation, "bld", None) == diagnostic_support.BUSINESS_BLD:
                    candidates.append(operation)
            except Exception:
                pass
    if len(candidates) != 1:
        raise PilotStopped(f"CORE_OPERATION_UNRESOLVED:{len(candidates)}")
    candidates[0].fetch(
        diagnostic_support.SCOPE["strtDd"], diagnostic_support.SCOPE["endDd"],
        diagnostic_support.SCOPE["inqCondTpCd"], diagnostic_support.SCOPE["mktTpCd"]
    )


def run_diagnostic(
    *, env_file: Path, project_root: Path = ROOT, landing_root: Path = LANDING_ROOT,
    lock_path: Path = D_OWNED_LOCK_PATH, session_getter: Callable | None = None,
    execute_probe: Callable[[], None] | None = None,
    diagnostic_support=default_support,
) -> dict[str, object]:
    if importlib.metadata.version("pykrx") != diagnostic_support.PYKRX_VERSION:
        raise PilotStopped(f"pykrx must equal {diagnostic_support.PYKRX_VERSION}")
    project_root = project_root.resolve()
    landing_root = landing_root.resolve()
    lock_path = lock_path.resolve()
    _project_relative(landing_root, project_root)
    _project_relative(lock_path, project_root)
    expected_dates = diagnostic_support.expected_dates(project_root)
    krx_id, krx_pw = _load_credentials(env_file)
    if not krx_id or not krx_pw:
        raise PilotStopped("KRX credentials are not configured")
    credentials = (krx_id, krx_pw)
    run_id = _new_run_id()
    run_dir = landing_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    ledger = AppendOnlyLedger(run_dir / LEDGER_NAME, credential_values=credentials)
    _atomic_json_new(
        run_dir / MANIFEST_NAME,
        diagnostic_support.manifest_payload(
            run_id=run_id, created_at_utc=utc_now(), dates=expected_dates
        ),
    )
    session_getter = session_getter or _default_session_getter
    execute_probe = execute_probe or (
        lambda: _default_execute_probe(diagnostic_support)
    )
    try:
        with d_owned_run_lock(lock_path, run_id=run_id):
            with HttpCapture(
                ledger=ledger, run_dir=run_dir, project_root=project_root,
                credential_values=credentials, run_id=run_id,
                diagnostic_support=diagnostic_support,
                expected_dates=expected_dates,
            ) as capture:
                session = session_getter()
                if session is None or not getattr(session, "is_authenticated", False) or not session.is_valid():
                    raise PilotStopped("AUTHENTICATION_FAILED")
                if getattr(diagnostic_support, "REQUIRE_ZERO_RETRY_AUTH_SESSION", False):
                    transport = _authenticated_transport(session)
                    _install_verified_zero_retry(transport)
                    capture.authorize_business_session(transport)
                ledger.append(
                    "SCOPE_STARTED", bld=diagnostic_support.BUSINESS_BLD,
                    scope=diagnostic_support.SCOPE_ID,
                    params=dict(diagnostic_support.SCOPE),
                    business_request_limit=diagnostic_support.MAX_BUSINESS_REQUESTS,
                )
                execute_probe()
                response = capture.take_exact_response()
                expected_raw = getattr(
                    diagnostic_support, "EXPECTED_RAW_HTTP_REQUESTS", None
                )
                if expected_raw is not None and capture.raw_count != expected_raw:
                    raise PilotStopped(
                        f"RAW_REQUEST_COUNT_MISMATCH:{capture.raw_count}/{expected_raw}"
                    )
                classification = diagnostic_support.classify_response(
                    response.content, expected_dates
                )
                ledger.append(
                    "DIAGNOSTIC_PASSED", scope=diagnostic_support.SCOPE_ID,
                    classification=classification.classification,
                    source_rows=classification.source_rows,
                    observed_dates=list(classification.observed_dates),
                    positive_total_dates=classification.positive_total_dates,
                    raw_http_requests=capture.raw_count,
                    business_requests=capture.business_count,
                )
                result = {
                    "business_requests": capture.business_count,
                    "classification": classification.classification,
                    "observed_dates": list(classification.observed_dates),
                    "raw_http_requests": capture.raw_count,
                    "run_dir": str(run_dir),
                    "source_rows": classification.source_rows,
                    "status": "PASS",
                }
    except Exception as error:
        ledger.append(
            "DIAGNOSTIC_STOPPED", error_type=type(error).__name__,
            error=redact(str(error), credentials),
        )
        raise
    for artifact in run_dir.rglob("*"):
        if artifact.is_file():
            assert_no_credentials(artifact.read_bytes(), credentials)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="One-call Landing-only A007 Investor range diagnostic"
    )
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--acknowledge-cooldown-ended", action="store_true")
    parser.add_argument("--confirm-one-live-request", action="store_true")
    args = parser.parse_args()
    if not (args.acknowledge_cooldown_ended and args.confirm_one_live_request):
        print(
            "Refusing to run: both --acknowledge-cooldown-ended and "
            "--confirm-one-live-request are required",
            file=sys.stderr,
        )
        return 2
    print(json.dumps(run_diagnostic(env_file=args.env_file), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
