from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime, timezone
from io import BytesIO, StringIO
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlsplit

import numpy as np
import pandas as pd
import requests

from stock_data.orchestration.automatic_fallback import (
    AttemptFailure,
    FailureKind,
    SourceObservation,
    SourceProvenance,
)
from stock_data.providers.public_http_capture import capture_public_response


FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
FDR_PROVIDER = "financedatareader"
FRED_UPSTREAM = "FRED"
FDR_ROUTE = "FRED:VIXCLS"
TIMEOUT_SECONDS = 10
MAX_REQUESTS = 2


class _GuardedFredTransport:
    def __init__(self, *, session, capture_root: Path, start: date, end: date) -> None:
        self.session = session
        self.capture_root = capture_root
        self.start = start
        self.end = end
        self.calls = 0
        self.raw_missing_counts: list[int] = []

    def get(self, url, **kwargs):
        parsed = urlsplit(str(url))
        query = parse_qs(parsed.query, keep_blank_values=True)
        expected = {
            "id": ["VIXCLS"],
            "cosd": [self.start.isoformat()],
            "coed": [self.end.isoformat()],
        }
        if (
            parsed.scheme != "https"
            or parsed.hostname != "fred.stlouisfed.org"
            or parsed.path != "/graph/fredgraph.csv"
            or query != expected
        ):
            raise AttemptFailure(
                FailureKind.AMBIGUOUS_SEMANTICS,
                safe_code="FDR_FRED_ROUTE_IDENTITY_MISMATCH",
                request_count=self.calls,
            )
        if self.calls >= MAX_REQUESTS:
            raise AttemptFailure(
                FailureKind.RATE_LIMITED,
                safe_code="FDR_FRED_REQUEST_BUDGET_EXCEEDED",
                request_count=self.calls,
            )
        kwargs.pop("timeout", None)
        kwargs["timeout"] = TIMEOUT_SECONDS
        kwargs["allow_redirects"] = False
        try:
            response = self.session.get(url, **kwargs)
        except requests.Timeout as error:
            raise AttemptFailure(
                FailureKind.TIMEOUT,
                safe_code="FDR_FRED_TIMEOUT",
                request_count=self.calls + 1,
            ) from error
        except requests.RequestException as error:
            raise AttemptFailure(
                FailureKind.HTTP_ERROR,
                safe_code="FDR_FRED_TRANSPORT_ERROR",
                request_count=self.calls + 1,
            ) from error
        self.calls += 1
        capture_public_response(
            root=self.capture_root,
            provider="fred_via_financedatareader",
            operation=f"fredgraph_csv_{self.calls}",
            request_url=FRED_URL,
            request_parameters={
                "id": "VIXCLS",
                "cosd": self.start.isoformat(),
                "coed": self.end.isoformat(),
                "fdr_version": "0.9.202",
            },
            response=response,
        )
        status = int(response.status_code)
        if status in {401, 403}:
            raise AttemptFailure(
                FailureKind.AUTHENTICATION_REJECTED,
                safe_code=f"FDR_FRED_HTTP_{status}",
                request_count=self.calls,
            )
        if status == 429:
            raise AttemptFailure(
                FailureKind.RATE_LIMITED,
                safe_code="FDR_FRED_HTTP_429",
                request_count=self.calls,
            )
        if status != 200 or 300 <= status < 400:
            raise AttemptFailure(
                FailureKind.HTTP_ERROR,
                safe_code=f"FDR_FRED_HTTP_{status}",
                request_count=self.calls,
            )
        return response


def _validate_output(
    frame: pd.DataFrame | None, *, start: date, end: date, request_count: int
) -> pd.DataFrame:
    if frame is None or frame.empty:
        raise AttemptFailure(
            FailureKind.EMPTY_RESULT,
            safe_code="FDR_FRED_EMPTY",
            request_count=request_count,
        )
    if list(frame.columns) != ["VIXCLS"] or frame.index.name != "DATE":
        raise AttemptFailure(
            FailureKind.SCHEMA_ERROR,
            safe_code="FDR_FRED_SCHEMA",
            request_count=request_count,
        )
    index = pd.to_datetime(frame.index, errors="raise")
    if index.duplicated().any() or not index.is_monotonic_increasing:
        raise AttemptFailure(
            FailureKind.SCHEMA_ERROR,
            safe_code="FDR_FRED_DATE_KEY",
            request_count=request_count,
        )
    values = pd.to_numeric(frame["VIXCLS"], errors="coerce")
    if values.isna().any() or not np.isfinite(values.to_numpy(dtype="float64")).all():
        raise AttemptFailure(
            FailureKind.SCHEMA_ERROR,
            safe_code="FDR_FRED_NONFINITE_OR_FILLED",
            request_count=request_count,
        )
    dates = index.strftime("%Y-%m-%d")
    if any(value < start.isoformat() or value > end.isoformat() for value in dates):
        raise AttemptFailure(
            FailureKind.SCHEMA_ERROR,
            safe_code="FDR_FRED_DATE_RANGE",
            request_count=request_count,
        )
    return pd.DataFrame({"date": dates, "vixcls": values.to_numpy(dtype="float64")})


def fetch_vixcls(
    *,
    start: date,
    end: date,
    capture_root: Path,
    session=requests,
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    reader: Callable[[_GuardedFredTransport], pd.DataFrame] | None = None,
) -> SourceObservation[pd.DataFrame]:
    """Run the accepted exact FDR/FRED VIXCLS route with two counted GETs.

    The route rejects raw missing values before FinanceDataReader can forward
    fill them. It is descriptive only and does not establish a vintage.
    """
    if end < start:
        raise ValueError("end precedes start")
    transport = _GuardedFredTransport(
        session=session,
        capture_root=capture_root,
        start=start,
        end=end,
    )
    if reader is None:
        import FinanceDataReader as fdr
        import FinanceDataReader.fred.data as module

        original_requests = module.requests
        original_read_csv = module.pd.read_csv

        def guarded_read_csv(source, *args, **kwargs):
            if isinstance(source, str) and source.startswith("https://"):
                response = transport.get(source)
                raw = original_read_csv(BytesIO(response.content), *args, **kwargs)
                missing = int(raw["VIXCLS"].isna().sum()) if "VIXCLS" in raw else len(raw)
                transport.raw_missing_counts.append(missing)
                if missing:
                    raise AttemptFailure(
                        FailureKind.SCHEMA_ERROR,
                        safe_code="FDR_FRED_RAW_MISSING_FORWARD_FILL_FORBIDDEN",
                        request_count=transport.calls,
                    )
                return raw
            return original_read_csv(source, *args, **kwargs)

        module.requests = transport
        module.pd.read_csv = guarded_read_csv
        try:
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                frame = fdr.DataReader(
                    "FRED:VIXCLS",
                    start.isoformat(),
                    end.isoformat(),
                )
        finally:
            module.requests = original_requests
            module.pd.read_csv = original_read_csv
    else:
        frame = reader(transport)
    if transport.calls != MAX_REQUESTS:
        raise AttemptFailure(
            FailureKind.SCHEMA_ERROR,
            safe_code="FDR_FRED_CALL_ACCOUNTING",
            request_count=transport.calls,
        )
    normalized = _validate_output(
        frame, start=start, end=end, request_count=transport.calls
    )
    observed = now()
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return SourceObservation(
        value=normalized,
        provenance=SourceProvenance(
            provider=FDR_PROVIDER,
            upstream_provider=FRED_UPSTREAM,
            source_route=FDR_ROUTE,
            retrieved_at_utc=observed.astimezone(timezone.utc).isoformat(),
            request_count=transport.calls,
            retry_count=0,
        ),
    )
