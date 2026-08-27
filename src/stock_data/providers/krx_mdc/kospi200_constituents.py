from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from datetime import date, datetime
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import re
from typing import Callable

import pandas as pd

from stock_data.contracts.kospi200_constituent_breadth import KR_INDEX_CONSTITUENT_DAILY
from stock_data.validation.kospi200_constituent_breadth import validate_index_constituent_daily
from stock_data.orchestration.exchange_calendar import ExchangeTradingCalendar


class KOSPI200ConstituentSourceError(ValueError):
    pass


@dataclass(frozen=True)
class KOSPI200ConstituentRequestPlan:
    market_date: date
    previous_session_date: date
    bld: str
    parameters: tuple[tuple[str, str], ...]
    business_call_limit: int
    retry_count: int
    availability_status: str


@dataclass(frozen=True)
class KOSPI200ConstituentCapture:
    run_id: str
    market_date: date
    captured_at: str
    path: Path
    sha256: str
    rows: int
    business_calls: int = 1
    retry_count: int = 0


BodyFetcher = Callable[[date], bytes]


def _atomic_create(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise KOSPI200ConstituentSourceError("immutable Landing already exists")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            raise KOSPI200ConstituentSourceError("immutable Landing appeared concurrently")
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
    for key in ("KRX_ID", "KRX_PW"):
        if not os.getenv(key) and values.get(key):
            os.environ[key] = values[key]
    if not os.getenv("KRX_ID") or not os.getenv("KRX_PW"):
        raise KOSPI200ConstituentSourceError("KRX credentials are unavailable")


def _authenticated_fetcher(env_file: Path) -> BodyFetcher:
    _load_credentials(env_file)
    try:
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            from pykrx.website.comm import get_session
            from pykrx.website.krx.market import core
        session = get_session()
    except Exception as error:
        raise KOSPI200ConstituentSourceError(
            f"KRX authentication initialization failed: {type(error).__name__}"
        ) from None
    if session is None or not getattr(session, "is_authenticated", False) or not session.is_valid():
        raise KOSPI200ConstituentSourceError("KRX authentication failed")
    operations = []
    for value in vars(core).values():
        if isinstance(value, type):
            try:
                operation = value()
            except Exception:
                continue
            if getattr(operation, "bld", None) == "dbms/MDC/STAT/standard/MDCSTAT00601":
                operations.append(operation)
    if len(operations) != 1:
        raise KOSPI200ConstituentSourceError("MDCSTAT00601 operation is unavailable")
    operation = operations[0]

    def fetch(market_date: date) -> bytes:
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
            if path != "/comm/bldAttendant/getJsonData.cmd" or captured:
                raise KOSPI200ConstituentSourceError("unexpected or repeated KRX business call")
            kwargs.setdefault("timeout", 20)
            response = original(bound_session, method, url, **kwargs)
            captured.append(bytes(response.content))
            if response.status_code != 200:
                raise KOSPI200ConstituentSourceError(
                    f"KRX business HTTP status {response.status_code}"
                )
            return response

        requests.Session.request = request
        try:
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                operation.fetch(market_date.strftime("%Y%m%d"), "028", "1")
        except KOSPI200ConstituentSourceError:
            raise
        except Exception as error:
            raise KOSPI200ConstituentSourceError(
                f"KRX retry-zero constituent request failed: {type(error).__name__}"
            ) from None
        finally:
            requests.Session.request = original
        if len(captured) != 1:
            raise KOSPI200ConstituentSourceError("KRX constituent response was not captured")
        return captured[0]

    return fetch


def capture_kospi200_constituents(
    market_date: date,
    *,
    run_id: str,
    landing_root: Path,
    env_file: Path,
    captured_at: str,
    body_fetcher: BodyFetcher | None = None,
) -> tuple[KOSPI200ConstituentCapture, pd.DataFrame]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", run_id):
        raise KOSPI200ConstituentSourceError("run_id is invalid")
    path = landing_root.resolve() / run_id / "response.json"
    if path.parent.exists():
        raise KOSPI200ConstituentSourceError("run_id already has immutable Landing")
    body = (body_fetcher or _authenticated_fetcher(env_file))(market_date)
    _atomic_create(path, body)
    frame = normalize_kospi200_constituent_landing(
        body, observation_date=market_date, captured_at=captured_at,
    )
    return KOSPI200ConstituentCapture(
        run_id=run_id, market_date=market_date, captured_at=captured_at,
        path=path, sha256=hashlib.sha256(body).hexdigest(), rows=len(frame),
    ), frame


def plan_latest_completed_kospi200_request(as_of: datetime) -> KOSPI200ConstituentRequestPlan:
    """Plan one exact official request without asserting provider availability."""
    calendar = ExchangeTradingCalendar("KR")
    market_date = calendar.latest_completed_session(as_of)
    return KOSPI200ConstituentRequestPlan(
        market_date=market_date,
        previous_session_date=calendar.previous_trading_day(market_date),
        bld="dbms/MDC/STAT/standard/MDCSTAT00601",
        parameters=(("date", market_date.strftime("%Y%m%d")), ("ticker", "1028")),
        business_call_limit=1,
        retry_count=0,
        availability_status="UNVERIFIED_UNTIL_NONEMPTY_EXACT_DATE_RESPONSE",
    )


def normalize_kospi200_constituent_landing(
    body: bytes,
    *,
    observation_date: date,
    captured_at: str,
) -> pd.DataFrame:
    """Normalize one immutable MDCSTAT00601 exact-date response without I/O."""
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise KOSPI200ConstituentSourceError("constituent Landing is not valid JSON") from error
    rows = payload.get("output") if isinstance(payload, dict) else None
    if not isinstance(rows, list) or not rows:
        raise KOSPI200ConstituentSourceError("constituent output is missing or empty")
    if not isinstance(captured_at, str) or not captured_at.strip():
        raise KOSPI200ConstituentSourceError("captured_at is required")
    digest = hashlib.sha256(body).hexdigest()
    day = observation_date.isoformat()
    records = []
    for row in rows:
        if not isinstance(row, dict) or not str(row.get("ISU_SRT_CD", "")).strip():
            raise KOSPI200ConstituentSourceError("constituent row identity is missing")
        records.append({
            "date": day,
            "observation_date": day,
            "index_symbol": "KOSPI200",
            "index_ticker": "1028",
            "market": "KOSPI",
            "symbol": str(row["ISU_SRT_CD"]).strip().upper(),
            "name": str(row.get("ISU_ABBRV", "")).strip() or None,
            "source": "KRX",
            "source_operation": "MDCSTAT00601",
            "source_captured_at": captured_at,
            "source_sha256": digest,
            "pit_status": "EXACT_DATE_ONLY_NO_INTERVAL_INFERENCE",
        })
    frame = pd.DataFrame(records, columns=KR_INDEX_CONSTITUENT_DAILY.column_names)
    frame = frame.sort_values(list(KR_INDEX_CONSTITUENT_DAILY.sort_key), kind="stable").reset_index(drop=True)
    validate_index_constituent_daily(frame)
    return frame


__all__ = [
    "BodyFetcher",
    "KOSPI200ConstituentCapture",
    "KOSPI200ConstituentRequestPlan",
    "KOSPI200ConstituentSourceError",
    "capture_kospi200_constituents",
    "normalize_kospi200_constituent_landing",
    "plan_latest_completed_kospi200_request",
]
