from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from datetime import date
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import re
from typing import Callable, Mapping


class IndexFundamentalProviderError(RuntimeError):
    pass


IDENTITIES: Mapping[str, str] = {"1001": "KOSPI", "2001": "KOSDAQ"}
_RUN_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_REQUIRED_FIELDS = {
    "TRD_DD", "CLSPRC_IDX", "WT_PER", "WT_STKPRC_NETASST_RTO", "DIV_YD",
}


@dataclass(frozen=True)
class LandingResponse:
    index_code: str
    market: str
    path: Path
    sha256: str
    rows: int


@dataclass(frozen=True)
class CaptureResult:
    run_id: str
    start_date: str
    end_date: str
    business_calls: int
    retry_count: int
    responses: tuple[LandingResponse, ...]


BodyFetcher = Callable[[str, date, date], bytes]


def _atomic_create(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise IndexFundamentalProviderError(f"immutable Landing exists: {path}")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            raise IndexFundamentalProviderError(f"immutable Landing appeared: {path}")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _load_credentials(env_file: Path) -> tuple[str, str]:
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
        raise IndexFundamentalProviderError(
            "KRX credentials must be present before any provider call"
        )
    os.environ.setdefault("KRX_ID", identity)
    os.environ.setdefault("KRX_PW", password)
    return identity, password


def _authenticated_fetcher(env_file: Path) -> BodyFetcher:
    """Build the reviewed pykrx range route after the credential preflight."""
    _load_credentials(env_file)
    try:
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            from pykrx.website.comm import get_session
            from pykrx.website.krx.market import core
        session = get_session()
    except Exception as error:
        raise IndexFundamentalProviderError(
            f"KRX authentication initialization failed: {type(error).__name__}"
        ) from None
    if session is None or not getattr(session, "is_authenticated", False) or not session.is_valid():
        raise IndexFundamentalProviderError("KRX authentication failed")
    candidates = []
    for value in vars(core).values():
        if isinstance(value, type):
            try:
                instance = value()
            except Exception:
                continue
            if getattr(instance, "bld", None) == "dbms/MDC/STAT/standard/MDCSTAT00702":
                candidates.append(instance)
    if len(candidates) != 1:
        raise IndexFundamentalProviderError("MDCSTAT00702 operation is unavailable")
    operation = candidates[0]

    def fetch(index_code: str, start: date, end: date) -> bytes:
        # KrxWebIo returns decoded rows, so capture the exact one business body
        # at the session boundary while rejecting retries and unrelated routes.
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
                raise IndexFundamentalProviderError(f"unexpected post-auth endpoint: {path}")
            if captured:
                raise IndexFundamentalProviderError("retry or extra business call rejected")
            kwargs.setdefault("timeout", 20)
            response = original(bound_session, method, url, **kwargs)
            captured.append(bytes(response.content))
            if response.status_code != 200:
                raise IndexFundamentalProviderError(
                    f"KRX business HTTP status {response.status_code}"
                )
            return response

        requests.Session.request = request
        try:
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                operation.fetch(
                    start.strftime("%Y%m%d"), end.strftime("%Y%m%d"),
                    index_code[0], index_code[1:],
                )
        except IndexFundamentalProviderError:
            raise
        except Exception as error:
            raise IndexFundamentalProviderError(
                f"KRX retry-zero range failed: {type(error).__name__}"
            ) from None
        finally:
            requests.Session.request = original
        if len(captured) != 1:
            raise IndexFundamentalProviderError("KRX business response was not captured")
        return captured[0]

    return fetch


def capture_index_fundamental_range(
    start: date,
    end: date,
    *,
    run_id: str,
    landing_root: Path,
    env_file: Path,
    body_fetcher: BodyFetcher | None = None,
) -> CaptureResult:
    if start > end:
        raise IndexFundamentalProviderError("start must not be after end")
    if (end - start).days > 31:
        raise IndexFundamentalProviderError("range capture is limited to 32 calendar days")
    if not _RUN_ID.fullmatch(run_id):
        raise IndexFundamentalProviderError("run_id is invalid")
    run_root = landing_root.resolve() / run_id
    if run_root.exists():
        raise IndexFundamentalProviderError("run_id already has immutable Landing")
    fetch = body_fetcher or _authenticated_fetcher(env_file)
    responses: list[LandingResponse] = []
    for index_code, market in IDENTITIES.items():
        body = fetch(index_code, start, end)
        path = run_root / f"{market.lower()}.json"
        _atomic_create(path, body)
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise IndexFundamentalProviderError(f"{market} returned non-JSON") from error
        output = payload.get("output") if isinstance(payload, dict) else None
        if not isinstance(output, list):
            raise IndexFundamentalProviderError(f"{market} output is missing")
        if not output:
            raise IndexFundamentalProviderError(f"{market} returned valid empty output")
        if any(not isinstance(row, dict) or not _REQUIRED_FIELDS.issubset(row) for row in output):
            raise IndexFundamentalProviderError(f"{market} schema differs")
        responses.append(LandingResponse(
            index_code=index_code,
            market=market,
            path=path,
            sha256=hashlib.sha256(body).hexdigest(),
            rows=len(output),
        ))
    return CaptureResult(
        run_id=run_id,
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        business_calls=2,
        retry_count=0,
        responses=tuple(responses),
    )


__all__ = [
    "CaptureResult", "IndexFundamentalProviderError", "LandingResponse",
    "capture_index_fundamental_range",
]
