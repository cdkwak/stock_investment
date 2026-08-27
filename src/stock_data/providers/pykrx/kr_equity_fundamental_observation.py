"""One-call KRX equity-fundamental observation capture.

The capture is immutable Landing evidence for descriptive current research.
It does not promote a canonical date-symbol series or make a PIT/finality claim.
"""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import re
from typing import Callable

from stock_data.orchestration.pykrx_equity_fundamental_daily import _analyze_body


class EquityFundamentalObservationError(RuntimeError):
    pass


SOURCE_OPERATION = "MDCSTAT03501"
SOURCE_BLD = "dbms/MDC/STAT/standard/MDCSTAT03501"
SOURCE_SCOPE = "ALL"
_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")


@dataclass(frozen=True)
class EquityFundamentalObservation:
    run_id: str
    market_date: str
    path: Path
    provenance_path: Path
    sha256: str
    rows: int
    distinct_security_codes: int
    duplicate_groups: int
    missing_value_counts: dict[str, int]
    business_calls: int = 1
    retry_count: int = 0
    predictive_use: bool = False


BodyFetcher = Callable[[date], bytes]


def find_valid_equity_fundamental_observation(
    landing_root: Path, target: date,
) -> EquityFundamentalObservation | None:
    date_root = landing_root / f"date={target.isoformat()}"
    accepted: list[tuple[str, EquityFundamentalObservation]] = []
    for provenance_path in sorted(date_root.glob("*/provenance.json")):
        try:
            provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
            response_path = provenance_path.with_name("response.json")
            body = response_path.read_bytes()
            analysis = _analyze_body(body, target_date=target.isoformat())
            if (
                type(provenance) is not dict
                or provenance.get("schema")
                != "kr_equity_fundamental_current_observation/v1"
                or provenance.get("source") != "KRX_via_pykrx"
                or provenance.get("source_operation") != SOURCE_OPERATION
                or provenance.get("source_bld") != SOURCE_BLD
                or provenance.get("source_scope") != SOURCE_SCOPE
                or provenance.get("market_date") != target.isoformat()
                or provenance.get("response_path") != "response.json"
                or provenance.get("response_sha256") != analysis["body_sha256"]
                or provenance.get("rows") != analysis["rows"]
                or provenance.get("distinct_security_codes")
                != analysis["distinct_security_codes"]
                or provenance.get("provider_duplicate_groups")
                != analysis["provider_duplicate_groups"]
                or provenance.get("missing_value_counts")
                != analysis["missing_value_counts"]
                or provenance.get("finality") != "UNKNOWN"
                or provenance.get("pit_status")
                != "PIT_LIMITED_FIRST_OBSERVED_ONLY"
                or provenance.get("descriptive_current_use") is not True
                or provenance.get("predictive_use") is not False
                or provenance.get("normalized_writes") is not False
                or type(provenance.get("business_calls")) is not int
                or provenance.get("business_calls") != 1
                or type(provenance.get("retry_count")) is not int
                or provenance.get("retry_count") != 0
                or provenance.get("availability_basis")
                != "PROJECT_FIRST_OBSERVED_AS_RETRIEVED"
                or provenance.get("available_at_utc")
                != provenance.get("retrieved_at_utc")
            ):
                continue
            retrieved_at = datetime.fromisoformat(provenance["retrieved_at_utc"])
            if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
                continue
            observation = EquityFundamentalObservation(
                run_id=provenance_path.parent.name,
                market_date=target.isoformat(),
                path=response_path,
                provenance_path=provenance_path,
                sha256=analysis["body_sha256"],
                rows=analysis["rows"],
                distinct_security_codes=analysis["distinct_security_codes"],
                duplicate_groups=len(analysis["provider_duplicate_groups"]),
                missing_value_counts=dict(analysis["missing_value_counts"]),
            )
            accepted.append((provenance["retrieved_at_utc"], observation))
        except (
            KeyError, OSError, TypeError, ValueError, json.JSONDecodeError,
        ):
            continue
    return max(accepted, key=lambda item: item[0])[1] if accepted else None


def _atomic_create(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise EquityFundamentalObservationError(f"immutable Landing exists: {path}")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            raise EquityFundamentalObservationError(
                f"immutable Landing appeared: {path}"
            )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _load_credentials(env_file: Path) -> None:
    values: dict[str, str] = {}
    if env_file.is_file():
        for raw in env_file.read_text(encoding="utf-8-sig").splitlines():
            if "=" in raw and not raw.lstrip().startswith("#"):
                key, value = raw.split("=", 1)
                if key.strip() in {"KRX_ID", "KRX_PW"}:
                    values[key.strip()] = value.strip().strip("\"'")
    identity = os.getenv("KRX_ID") or values.get("KRX_ID", "")
    password = os.getenv("KRX_PW") or values.get("KRX_PW", "")
    if not identity or not password:
        raise EquityFundamentalObservationError(
            "KRX credentials must be present before any provider call"
        )
    os.environ.setdefault("KRX_ID", identity)
    os.environ.setdefault("KRX_PW", password)


def _authenticated_fetcher(env_file: Path) -> BodyFetcher:
    _load_credentials(env_file)
    try:
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            from pykrx.website.comm import get_session
            from pykrx.website.krx.market import core
        session = get_session()
    except Exception as error:
        raise EquityFundamentalObservationError(
            f"KRX authentication initialization failed: {type(error).__name__}"
        ) from None
    if session is None or not getattr(session, "is_authenticated", False) or not session.is_valid():
        raise EquityFundamentalObservationError("KRX authentication failed")
    candidates = []
    for value in vars(core).values():
        if not isinstance(value, type):
            continue
        try:
            instance = value()
        except Exception:
            continue
        if getattr(instance, "bld", None) == SOURCE_BLD:
            candidates.append(instance)
    if len(candidates) != 1:
        raise EquityFundamentalObservationError(
            "MDCSTAT03501 operation is unavailable"
        )
    operation = candidates[0]

    def fetch(target: date) -> bytes:
        import requests

        original = requests.Session.request
        captured: list[bytes] = []

        def request(bound_session, method, url, **kwargs):
            path = requests.utils.urlparse(str(url)).path
            if path in {
                "/contents/MDC/COMS/client/MDCCOMS001.cmd",
                "/contents/MDC/COMS/client/view/login.jsp",
                "/contents/MDC/COMS/client/MDCCOMS001D1.cmd",
            }:
                kwargs.setdefault("timeout", 20)
                return original(bound_session, method, url, **kwargs)
            if path != "/comm/bldAttendant/getJsonData.cmd":
                raise EquityFundamentalObservationError(
                    f"unexpected post-auth endpoint: {path}"
                )
            if captured:
                raise EquityFundamentalObservationError(
                    "retry or extra business call rejected"
                )
            kwargs.setdefault("timeout", 20)
            response = original(bound_session, method, url, **kwargs)
            captured.append(bytes(response.content))
            if response.status_code != 200:
                raise EquityFundamentalObservationError(
                    f"KRX business HTTP status {response.status_code}"
                )
            return response

        requests.Session.request = request
        try:
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                operation.fetch(target.strftime("%Y%m%d"), SOURCE_SCOPE)
        except EquityFundamentalObservationError:
            raise
        except Exception as error:
            raise EquityFundamentalObservationError(
                f"KRX retry-zero observation failed: {type(error).__name__}"
            ) from None
        finally:
            requests.Session.request = original
        if len(captured) != 1:
            raise EquityFundamentalObservationError(
                "KRX business response was not captured"
            )
        return captured[0]

    return fetch


def capture_equity_fundamental_observation(
    target: date,
    *,
    run_id: str,
    landing_root: Path,
    env_file: Path,
    body_fetcher: BodyFetcher | None = None,
) -> EquityFundamentalObservation:
    if not _RUN_ID.fullmatch(run_id):
        raise EquityFundamentalObservationError("run_id is invalid")
    run_root = landing_root.resolve() / f"date={target.isoformat()}" / run_id
    if run_root.exists():
        raise EquityFundamentalObservationError(
            "run_id already has immutable Landing"
        )
    fetch = body_fetcher or _authenticated_fetcher(env_file)
    body = fetch(target)
    response_path = run_root / "response.json"
    _atomic_create(response_path, body)
    try:
        analysis = _analyze_body(body, target_date=target.isoformat())
    except ValueError as error:
        raise EquityFundamentalObservationError(str(error)) from error
    retrieved_at = datetime.now(timezone.utc).isoformat()
    provenance = {
        "schema": "kr_equity_fundamental_current_observation/v1",
        "source": "KRX_via_pykrx",
        "source_operation": SOURCE_OPERATION,
        "source_bld": SOURCE_BLD,
        "source_scope": SOURCE_SCOPE,
        "market_date": target.isoformat(),
        "retrieved_at_utc": retrieved_at,
        "available_at_utc": retrieved_at,
        "availability_basis": "PROJECT_FIRST_OBSERVED_AS_RETRIEVED",
        "response_path": response_path.name,
        "response_sha256": analysis["body_sha256"],
        "rows": analysis["rows"],
        "distinct_security_codes": analysis["distinct_security_codes"],
        "provider_duplicate_groups": analysis["provider_duplicate_groups"],
        "missing_value_counts": analysis["missing_value_counts"],
        "finality": "UNKNOWN",
        "pit_status": "PIT_LIMITED_FIRST_OBSERVED_ONLY",
        "descriptive_current_use": True,
        "predictive_use": False,
        "normalized_writes": False,
        "business_calls": 1,
        "retry_count": 0,
    }
    provenance_path = run_root / "provenance.json"
    _atomic_create(
        provenance_path,
        (json.dumps(provenance, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(),
    )
    return EquityFundamentalObservation(
        run_id=run_id,
        market_date=target.isoformat(),
        path=response_path,
        provenance_path=provenance_path,
        sha256=analysis["body_sha256"],
        rows=analysis["rows"],
        distinct_security_codes=analysis["distinct_security_codes"],
        duplicate_groups=len(analysis["provider_duplicate_groups"]),
        missing_value_counts=dict(analysis["missing_value_counts"]),
    )


__all__ = [
    "EquityFundamentalObservation", "EquityFundamentalObservationError",
    "capture_equity_fundamental_observation",
    "find_valid_equity_fundamental_observation",
]
