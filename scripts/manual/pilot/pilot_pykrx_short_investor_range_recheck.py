"""Bounded Landing-only recheck of pykrx Short Investor range behavior."""

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

import pandas as pd
import requests


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.manual.diagnostic.diagnose_a007_investor_range import (
    _authenticated_transport, _install_verified_zero_retry,
)
from scripts.manual.pilot.pykrx_short_investor_range_recheck_support import (
    BOUNDARY_60, BUSINESS_ENDPOINT_PATH, EXPECTED_COLUMNS, HISTORICAL_WINDOWS, KNOWN_DATES,
    MAX_BUSINESS_REQUESTS, MAX_RAW_HTTP_REQUESTS, MIN_BUSINESS_INTERVAL_SECONDS,
    PYKRX_VERSION, RECENT_1, RECENT_5, RECENT_20, RECENT_60, Probe,
    RecheckStopped, classify, make_probe, parse_raw, plan_sha256,
)
from scripts.manual.pilot.pykrx_short_selling_pilot_support import (
    AUTH_ENDPOINT_PATHS, AppendOnlyLedger, PilotStopped, assert_no_credentials,
    d_owned_run_lock, redact, safe_url, utc_now, write_bytes_atomic_new,
    write_json_atomic,
)


LANDING_ROOT = ROOT / "data/landing/diagnostics/pykrx_short_investor_range_recheck"
LOCK_PATH = ROOT / "data/state/d_owned_krx_short_selling.lock"
METRIC_FUNCTIONS = {
    "volume": "get_shorting_investor_volume_by_date",
    "trading_value": "get_shorting_investor_value_by_date",
}


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


class Capture:
    def __init__(
        self, run_dir: Path, ledger: AppendOnlyLedger, secrets: tuple[str, ...], *,
        max_business_requests: int = MAX_BUSINESS_REQUESTS,
        max_raw_requests: int = MAX_RAW_HTTP_REQUESTS,
    ):
        self.run_dir = run_dir
        self.ledger = ledger
        self.secrets = secrets
        self.raw_count = 0
        self.business_count = 0
        self.current: Probe | None = None
        self.business_session: requests.Session | None = None
        self.latest_body: bytes | None = None
        self._original: Callable | None = None
        self.max_business_requests = max_business_requests
        self.max_raw_requests = max_raw_requests

    def __enter__(self):
        self._original = requests.Session.request
        requests.Session.request = lambda session, method, url, **kwargs: self._request(session, method, url, **kwargs)
        return self

    def __exit__(self, *unused):
        if self._original is not None:
            requests.Session.request = self._original

    def _request(self, session, method, url, **kwargs):
        path = requests.utils.urlparse(str(url)).path
        auth = path in AUTH_ENDPOINT_PATHS
        if not auth:
            if path != BUSINESS_ENDPOINT_PATH or self.current is None:
                raise RecheckStopped(f"UNAPPROVED_ENDPOINT:{path}")
            if session is not self.business_session:
                raise RecheckStopped("BUSINESS_SESSION_MISMATCH")
            supplied = kwargs.get("data")
            actual = {str(k): str(v) for k, v in supplied.items()} if isinstance(supplied, dict) else None
            if (str(method).upper() != "POST" or kwargs.get("params") not in (None, {})
                    or kwargs.get("json") is not None or actual != self.current.expected_business_data):
                raise RecheckStopped("BUSINESS_REQUEST_BOUNDARY_MISMATCH")
        if self.raw_count >= self.max_raw_requests or (not auth and self.business_count >= self.max_business_requests):
            raise RecheckStopped("REQUEST_BUDGET_EXHAUSTED")
        self.raw_count += 1
        if not auth:
            self.business_count += 1
            kwargs["allow_redirects"] = False
        kwargs.setdefault("timeout", 20)
        assert self._original is not None
        started = time.monotonic()
        response = self._original(session, method, url, **kwargs)
        entry = {
            "raw_sequence": self.raw_count,
            "authentication": auth, "method": str(method).upper(),
            "url": safe_url(str(url)), "status_code": response.status_code,
            "response_bytes": len(response.content),
            "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
        }
        if not auth:
            assert_no_credentials(response.content, self.secrets)
            probe = self.current
            assert probe is not None
            body_name = f"response_{self.business_count:02d}_{probe.probe_id}.json"
            body_path = self.run_dir / body_name
            write_bytes_atomic_new(body_path, response.content)
            body_hash = hashlib.sha256(response.content).hexdigest()
            provenance = {
                "body_file": body_name, "body_sha256": body_hash,
                "captured_at_utc": utc_now(), "content_type": response.headers.get("Content-Type", ""),
                "expected_dates": list(probe.expected_dates), "http_status_code": response.status_code,
                "market": probe.market, "metric": probe.metric, "probe_id": probe.probe_id,
                "requested_end": probe.end, "requested_start": probe.start,
                "response_bytes": len(response.content), "source": "authenticated_pykrx",
                "source_operation": "MDCSTAT30301", "version": 1,
            }
            write_json_atomic(self.run_dir / f"{body_name}.provenance.json", provenance)
            entry.update({"probe_id": probe.probe_id, "body_file": body_name, "response_sha256": body_hash})
            self.latest_body = response.content
        self.ledger.append("HTTP_RESPONSE", **entry)
        if response.status_code in {403, 429}:
            raise RecheckStopped(f"HTTP_RESTRICTION:{response.status_code}")
        if response.status_code != 200:
            raise RecheckStopped(f"HTTP_STATUS:{response.status_code}")
        return response


def _compare_dataframe(probe: Probe, body: bytes, frame: pd.DataFrame) -> dict[str, object]:
    rows, source_order = parse_raw(body)
    columns = tuple(str(x) for x in frame.columns)
    frame_dates = tuple(index.strftime("%Y%m%d") for index in frame.index)
    raw_values = {
        datetime.strptime(str(row["TRD_DD"]), "%Y/%m/%d").strftime("%Y%m%d"):
        tuple(int(str(row[f"STR_CONST_VAL{i}"]).replace(",", "")) for i in range(1, 6))
        for row in rows if str(row["TRD_DD"]).strip()
    }
    expected_frame_rows = len(source_order)
    dataframe_matches_raw = columns == EXPECTED_COLUMNS and len(frame) == expected_frame_rows
    if dataframe_matches_raw:
        for date, values in raw_values.items():
            if date not in frame_dates or tuple(int(x) for x in frame.loc[pd.Timestamp(date)].tolist()) != values:
                dataframe_matches_raw = False
                break
    return {
        "dataframe_rows": len(frame), "dataframe_columns": list(columns),
        "dataframe_dates": list(frame_dates), "dataframe_order": "ascending" if list(frame_dates) == sorted(frame_dates) else "other",
        "dataframe_matches_raw": dataframe_matches_raw,
        "wrapper_regression": len(source_order) > 1 and len(frame) == 1,
    }


def _all_possible_probes() -> list[Probe]:
    probes = []
    for metric in METRIC_FUNCTIONS:
        probes.append(make_probe(f"known_20200106_10_{metric}", "KOSPI", metric, KNOWN_DATES, "known_positive"))
    for dates, label in ((RECENT_1, "recent_1"), (RECENT_5, "recent_5"), (RECENT_20, "recent_20"), (RECENT_60, "recent_60")):
        for metric in METRIC_FUNCTIONS:
            probes.append(make_probe(f"{label}_{metric}", "KOSPI", metric, dates, label))
    for metric in METRIC_FUNCTIONS:
        probes.append(make_probe(f"kosdaq_20_{metric}", "KOSDAQ", metric, RECENT_20, "kosdaq"))
    for year, dates in HISTORICAL_WINDOWS.items():
        for metric in METRIC_FUNCTIONS:
            probes.append(make_probe(f"historical_{year}_{metric}", "KOSPI", metric, dates, "historical"))
    assert len(probes) == MAX_BUSINESS_REQUESTS
    return probes


def _boundary_probes() -> list[Probe]:
    """Four exact scope probes, capped before any KRX business request."""
    return [
        make_probe(f"boundary_60_{market.lower()}_{metric}", market, metric, BOUNDARY_60, "boundary")
        for market in ("KOSPI", "KOSDAQ")
        for metric in METRIC_FUNCTIONS
    ]


def run(*, env_file: Path = ROOT / ".env", boundary_only: bool = False) -> dict[str, object]:
    if importlib.metadata.version("pykrx") != PYKRX_VERSION:
        raise RecheckStopped(f"pykrx must equal {PYKRX_VERSION}")
    krx_id, krx_pw = _load_credentials(env_file)
    if not krx_id or not krx_pw:
        raise RecheckStopped("KRX credentials not configured")
    secrets = (krx_id, krx_pw)
    run_id = _new_run_id()
    run_dir = LANDING_ROOT / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    ledger = AppendOnlyLedger(run_dir / "call_ledger.jsonl", credential_values=secrets)
    possible = _boundary_probes() if boundary_only else _all_possible_probes()
    business_limit = len(possible)
    manifest = {
        "business_request_limit": business_limit,
        "diagnostic_checkpoint_writes": True, "production_checkpoint_writes": False,
        "created_at_utc": utc_now(), "normalized_writes": False, "parallelism": 1,
        "plan_sha256": plan_sha256(possible), "probes": [p.__dict__ for p in possible],
        "purpose": "verify_boundary_60_pykrx_short_investor_multirow_window" if boundary_only else "find_safe_pykrx_short_investor_multirow_window",
        "pykrx_version": PYKRX_VERSION, "raw_http_request_limit": business_limit,
        "retry_count": 0, "run_id": run_id,
    }
    write_json_atomic(run_dir / "manifest.json", manifest)
    checkpoint: dict[str, object] = {"run_id": run_id, "status": "RUNNING", "completed": [], "skipped": []}
    write_json_atomic(run_dir / "checkpoint.json", checkpoint)

    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        from pykrx import stock
        from pykrx.website.comm import get_session

    completed: dict[str, dict[str, object]] = {}
    last_business = 0.0

    def execute(probe: Probe, capture: Capture) -> dict[str, object]:
        nonlocal last_business
        wait = max(0.0, MIN_BUSINESS_INTERVAL_SECONDS - (time.monotonic() - last_business))
        if wait:
            time.sleep(wait)
        capture.current = probe
        function = getattr(stock, METRIC_FUNCTIONS[probe.metric])
        frame = function(probe.start, probe.end, probe.market)
        last_business = time.monotonic()
        if capture.latest_body is None:
            raise RecheckStopped("BUSINESS_BODY_NOT_CAPTURED")
        result = classify(probe, capture.latest_body)
        result.update(_compare_dataframe(probe, capture.latest_body, frame))
        result.update({"probe_id": probe.probe_id, "market": probe.market, "metric": probe.metric,
                       "requested_start": probe.start, "requested_end": probe.end,
                       "expected_rows": len(probe.expected_dates)})
        completed[probe.probe_id] = result
        checkpoint["completed"] = list(completed.values())
        write_json_atomic(run_dir / "checkpoint.json", checkpoint)
        ledger.append("PROBE_CLASSIFIED", **result)
        return result

    def passes(result: dict[str, object]) -> bool:
        return result["classification"] in {"REGRESSION_PASS_MULTIROW", "RANGE_PASS"} and result["dataframe_matches_raw"] is True

    try:
        with d_owned_run_lock(LOCK_PATH, run_id=run_id):
            with Capture(
                run_dir, ledger, secrets, max_business_requests=business_limit,
                max_raw_requests=business_limit,
            ) as capture:
                session = get_session()
                if session is None or not getattr(session, "is_authenticated", False) or not session.is_valid():
                    raise RecheckStopped("AUTHENTICATION_FAILED")
                transport = _authenticated_transport(session)
                _install_verified_zero_retry(transport)
                capture.business_session = transport

                if boundary_only:
                    for probe in possible:
                        result = execute(probe, capture)
                        if not passes(result):
                            raise RecheckStopped(f"BOUNDARY_RANGE_NOT_SAFE:{probe.probe_id}")
                    safe_recent: dict[str, int] = {metric: len(BOUNDARY_60) for metric in METRIC_FUNCTIONS}
                else:
                    known = {metric: execute(make_probe(f"known_20200106_10_{metric}", "KOSPI", metric, KNOWN_DATES, "known_positive"), capture) for metric in METRIC_FUNCTIONS}
                    safe_recent = {metric: 0 for metric in METRIC_FUNCTIONS}
                    stages = ((RECENT_1, "recent_1", 0), (RECENT_5, "recent_5", 1),
                              (RECENT_20, "recent_20", 5), (RECENT_60, "recent_60", 20))
                    for dates, label, required_prior in stages:
                        for metric in METRIC_FUNCTIONS:
                            prior_ok = passes(known[metric]) and safe_recent[metric] >= required_prior
                            if prior_ok:
                                result = execute(make_probe(f"{label}_{metric}", "KOSPI", metric, dates, label), capture)
                                if passes(result):
                                    safe_recent[metric] = len(dates)
                            else:
                                checkpoint["skipped"].append(f"{label}_{metric}:prior_gate")

                    for metric in METRIC_FUNCTIONS:
                        if safe_recent[metric] >= 20:
                            execute(make_probe(f"kosdaq_20_{metric}", "KOSDAQ", metric, RECENT_20, "kosdaq"), capture)
                        else:
                            checkpoint["skipped"].append(f"kosdaq_20_{metric}:kospi_20_gate")

                    for year, dates in HISTORICAL_WINDOWS.items():
                        for metric in METRIC_FUNCTIONS:
                            if passes(known[metric]):
                                execute(make_probe(f"historical_{year}_{metric}", "KOSPI", metric, dates, "historical"), capture)
                            else:
                                checkpoint["skipped"].append(f"historical_{year}_{metric}:known_positive_gate")

                checkpoint.update({"status": "COMPLETE", "raw_http_requests": capture.raw_count,
                                   "business_requests": capture.business_count, "safe_recent_windows": safe_recent,
                                   "updated_at_utc": utc_now()})
                write_json_atomic(run_dir / "checkpoint.json", checkpoint)
    except Exception as error:
        checkpoint.update({"status": "STOPPED", "error_type": type(error).__name__,
                           "error": redact(str(error), secrets), "updated_at_utc": utc_now()})
        write_json_atomic(run_dir / "checkpoint.json", checkpoint)
        ledger.append("DIAGNOSTIC_STOPPED", error_type=type(error).__name__, error=redact(str(error), secrets))
        raise
    for path in run_dir.rglob("*"):
        if path.is_file():
            assert_no_credentials(path.read_bytes(), secrets)
    return {"run_dir": str(run_dir), "status": checkpoint["status"],
            "business_requests": checkpoint["business_requests"],
            "raw_http_requests": checkpoint["raw_http_requests"]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-bounded-live-recheck", action="store_true")
    parser.add_argument("--boundary-60", action="store_true")
    args = parser.parse_args()
    if not args.confirm_bounded_live_recheck:
        print("Refusing live recheck without explicit confirmation", file=sys.stderr)
        return 2
    print(json.dumps(run(boundary_only=args.boundary_60), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
