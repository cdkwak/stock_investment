from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlsplit

import numpy as np
import pandas as pd
import requests
from requests.adapters import HTTPAdapter


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUTPUT_RELATIVE = Path("artifacts/agent_runs/ur108/pilots")
TIMEOUT_SECONDS = 10
RETRY_COUNT = 0


class PilotStopped(RuntimeError):
    pass


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise PilotStopped(f"refusing to overwrite completed artifact: {path.name}")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            raise PilotStopped(f"artifact appeared before commit: {path.name}")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@dataclass(frozen=True)
class CallEvidence:
    route_id: str
    sequence: int
    method: str
    upstream_host: str
    upstream_path: str
    http_status: int
    response_bytes: int
    response_sha256: str
    timeout_seconds: int
    retry_count: int


class GuardedGetTransport:
    def __init__(self, *, route_id: str, allowed_host: str, allowed_path: str, budget: int):
        self.route_id = route_id
        self.allowed_host = allowed_host
        self.allowed_path = allowed_path
        self.budget = budget
        self.calls: list[CallEvidence] = []
        self._session = requests.Session()
        adapter = HTTPAdapter(max_retries=0)
        self._session.mount("https://", adapter)

    def get(self, url, **kwargs):
        parsed = urlsplit(str(url))
        if parsed.scheme != "https" or parsed.hostname != self.allowed_host or parsed.path != self.allowed_path:
            raise PilotStopped("UPSTREAM_IDENTITY_MISMATCH")
        if len(self.calls) >= self.budget:
            raise PilotStopped("ROUTE_REQUEST_BUDGET_EXCEEDED")
        supplied = kwargs.pop("timeout", TIMEOUT_SECONDS)
        if supplied != TIMEOUT_SECONDS:
            raise PilotStopped("TIMEOUT_POLICY_MISMATCH")
        kwargs["timeout"] = TIMEOUT_SECONDS
        kwargs["allow_redirects"] = False
        response = self._session.get(url, **kwargs)
        body = response.content
        evidence = CallEvidence(
            route_id=self.route_id,
            sequence=len(self.calls) + 1,
            method="GET",
            upstream_host=parsed.hostname,
            upstream_path=parsed.path,
            http_status=int(response.status_code),
            response_bytes=len(body),
            response_sha256=hashlib.sha256(body).hexdigest(),
            timeout_seconds=TIMEOUT_SECONDS,
            retry_count=RETRY_COUNT,
        )
        self.calls.append(evidence)
        if 300 <= response.status_code < 400:
            raise PilotStopped(f"HTTP_REDIRECT_{response.status_code}")
        if response.status_code in {401, 403, 429}:
            raise PilotStopped(f"HTTP_RESTRICTION_{response.status_code}")
        if response.status_code != 200:
            raise PilotStopped(f"HTTP_STATUS_{response.status_code}")
        return response


OFFLINE_ROUTES = (
    {
        "route_id": "kr_listing_kospi",
        "fdr_call": 'StockListing("KOSPI")',
        "upstream": "KRX working-date lookup + FinanceData/fdr_krx_data_cache",
        "expected_schema": "Code,Name,Market plus price/marcap snapshot fields",
        "live_budget": 0,
        "decision": "hold",
        "gate": "CACHE_LINEAGE_AND_REDISTRIBUTION_UNRESOLVED",
    },
    {
        "route_id": "international_listing_tse",
        "fdr_call": 'StockListing("TSE")',
        "upstream": "Naver overseas listing pagination",
        "expected_schema": "Symbol,Name,IndustryCode,Industry",
        "live_budget": 0,
        "decision": "exclude",
        "gate": "NAVER_AUTOMATED_USE_NOT_AUTHORIZED",
    },
    {
        "route_id": "krx_delisted_july",
        "fdr_call": 'StockListing("KRX-DELISTING", "2026-07-01", "2026-07-31")',
        "upstream": "KRX working-date lookup + FinanceData/fdr_krx_data_cache",
        "expected_schema": "Symbol,Name,Market,ListingDate,DelistingDate,Reason",
        "live_budget": 0,
        "decision": "hold",
        "gate": "CACHE_REVISION_AND_RIGHTS_UNRESOLVED",
    },
    {
        "route_id": "krx_administrative_current",
        "fdr_call": 'StockListing("KRX-ADMINISTRATIVE")',
        "upstream": "KRX KIND current HTML",
        "expected_schema": "Symbol,Name,DesignationDate,Reason",
        "live_budget": 0,
        "decision": "cross_check_only",
        "gate": "UNDOCUMENTED_AUTOMATED_ROUTE_AND_NO_DATED_FINALITY",
    },
    {
        "route_id": "kr_etf_listing",
        "fdr_call": 'StockListing("ETF/KR")',
        "upstream": "Naver Finance ETF item list",
        "expected_schema": "Symbol,Name,Price,NAV,Volume and current snapshot fields",
        "live_budget": 0,
        "decision": "exclude",
        "gate": "NAVER_RIGHTS_AND_CURRENT_UNIVERSE_PIT_MISMATCH",
    },
    {
        "route_id": "us_exchange_nasdaq",
        "fdr_call": 'StockListing("NASDAQ")',
        "upstream": "Naver overseas listing pagination",
        "expected_schema": "Symbol,Name,IndustryCode,Industry",
        "live_budget": 0,
        "decision": "exclude",
        "gate": "NOT_NASDAQ_OFFICIAL_DIRECTORY_AND_NAVER_RIGHTS",
    },
    {
        "route_id": "fx_usd_jpy",
        "fdr_call": 'DataReader("USD/JPY", "2026-08-11", "2026-08-12")',
        "upstream": "Yahoo query2 chart",
        "expected_schema": "Open,High,Low,Close,Volume,Adj Close",
        "live_budget": 0,
        "decision": "exclude",
        "gate": "YAHOO_NON_API_AUTOMATED_ACCESS_NOT_ACCEPTED",
    },
    {
        "route_id": "kr_index_ks11",
        "fdr_call": 'DataReader("KS11", "2026-08-11", "2026-08-12")',
        "upstream": "FinanceData/fdr_krx_data_cache",
        "expected_schema": "Open,High,Low,Close,Volume,Change plus index snapshot fields",
        "live_budget": 0,
        "decision": "hold",
        "gate": "CACHE_LINEAGE_FINALITY_AND_RIGHTS_UNRESOLVED",
    },
    {
        "route_id": "global_index_dji",
        "fdr_call": 'DataReader("DJI", "2026-08-11", "2026-08-12")',
        "upstream": "Yahoo query2 chart",
        "expected_schema": "Open,High,Low,Close,Volume,Adj Close",
        "live_budget": 0,
        "decision": "exclude",
        "gate": "SAME_PROVIDER_FAMILY_AND_YAHOO_RIGHTS_GATE",
    },
    {
        "route_id": "kr_daily_000660",
        "fdr_call": 'DataReader("000660", "2026-08-11", "2026-08-12")',
        "upstream": "Naver fchart",
        "expected_schema": "Open,High,Low,Close,Volume,Change",
        "live_budget": 0,
        "decision": "cross_check_only",
        "gate": "NAVER_RIGHTS_AND_SINGLE_SECURITY_UNIVERSE_MISMATCH",
    },
    {
        "route_id": "global_daily_msft",
        "fdr_call": 'DataReader("MSFT", "2026-08-11", "2026-08-12")',
        "upstream": "Yahoo query2 chart",
        "expected_schema": "Open,High,Low,Close,Volume,Adj Close",
        "live_budget": 0,
        "decision": "exclude",
        "gate": "ADJUSTMENT_SESSION_DELIST_PIT_AND_RIGHTS_UNRESOLVED",
    },
)


def run_offline(root: Path) -> dict:
    import FinanceDataReader.data as dispatch

    source_text = Path(dispatch.__file__).read_text(encoding="utf-8")
    required_markers = {
        "NaverStockListing": "NaverStockListing",
        "NaverEtfListing": "NaverEtfListing",
        "WikipediaStockListing": "WikipediaStockListing",
        "YahooDailyReader": "YahooDailyReader",
        "FredReader": "FredReader",
        "KrxMarcapListingCache": "KrxMarcapListingCache",
        "KrxDelistingCache": "KrxDelistingCache",
        "KrxAdministrative": "KrxAdministrative",
    }
    missing = [name for name, marker in required_markers.items() if marker not in source_text]
    if missing:
        raise PilotStopped("FDR_DISPATCH_MARKERS_MISSING")
    result = {
        "version": 1,
        "mode": "offline",
        "status": "OFFLINE_DISPATCH_SCHEMA_AUDIT_COMPLETE",
        "package_version": __import__("FinanceDataReader").__version__,
        "provider_requests": 0,
        "retry_count": 0,
        "raw_responses_persisted": 0,
        "routes": list(OFFLINE_ROUTES),
    }
    _atomic_json(root / OUTPUT_RELATIVE / "offline_dispatch_schema.json", result)
    return result


def _sp500_pilot() -> dict:
    import FinanceDataReader as fdr
    import FinanceDataReader.wikipedia.listing as module

    transport = GuardedGetTransport(
        route_id="sp500_current",
        allowed_host="en.wikipedia.org",
        allowed_path="/wiki/List_of_S%26P_500_companies",
        budget=1,
    )
    original = module.requests
    module.requests = transport
    try:
        frame = fdr.StockListing("S&P500")
    finally:
        module.requests = original
    expected = ["Symbol", "Name", "Sector", "Industry"]
    if list(frame.columns) != expected or frame.empty:
        raise PilotStopped("SP500_SCHEMA_OR_EMPTY")
    if frame["Symbol"].isna().any() or frame["Symbol"].astype(str).str.strip().eq("").any():
        raise PilotStopped("SP500_SYMBOL_MISSING")
    if frame["Symbol"].duplicated().any():
        raise PilotStopped("SP500_SYMBOL_DUPLICATE")
    return {
        "route_id": "sp500_current",
        "status": "BOUNDED_SCHEMA_PASS",
        "recommendation": "cross_check_only",
        "upstream_provider": "Wikipedia",
        "official_sp_membership": False,
        "row_count": len(frame),
        "columns": expected,
        "missing_by_column": {name: int(frame[name].isna().sum()) for name in expected},
        "unique_symbols": int(frame["Symbol"].nunique()),
        "ordering": "provider_table_order",
        "observation_time_semantics": "retrieval_time_only",
        "currency": None,
        "units": "security identity fields",
        "pit_finality": "NOT_ESTABLISHED",
        "calls": [asdict(item) for item in transport.calls],
    }


def _fred_pilot(root: Path) -> dict:
    import FinanceDataReader as fdr
    import FinanceDataReader.fred.data as module

    transport = GuardedGetTransport(
        route_id="fred_vixcls",
        allowed_host="fred.stlouisfed.org",
        allowed_path="/graph/fredgraph.csv",
        budget=2,
    )
    original_requests = module.requests
    original_read_csv = module.pd.read_csv
    raw_summaries: list[dict] = []

    def guarded_read_csv(source, *args, **kwargs):
        if isinstance(source, str) and source.startswith("https://"):
            response = transport.get(source)
            frame = original_read_csv(BytesIO(response.content), *args, **kwargs)
            raw_summaries.append({
                "rows": len(frame),
                "columns": [str(name) for name in frame.columns],
                "missing_by_column": {
                    str(name): int(frame[name].isna().sum()) for name in frame.columns
                },
            })
            return frame
        return original_read_csv(source, *args, **kwargs)

    module.requests = transport
    module.pd.read_csv = guarded_read_csv
    try:
        frame = fdr.DataReader("FRED:VIXCLS", "2026-08-11", "2026-08-12")
    finally:
        module.requests = original_requests
        module.pd.read_csv = original_read_csv
    if frame is None or frame.empty or list(frame.columns) != ["VIXCLS"]:
        raise PilotStopped("FRED_VIXCLS_SCHEMA_OR_EMPTY")
    if frame.index.name != "DATE" or frame.index.duplicated().any() or not frame.index.is_monotonic_increasing:
        raise PilotStopped("FRED_VIXCLS_DATE_KEY_INVALID")
    numeric = pd.to_numeric(frame["VIXCLS"], errors="coerce")
    if numeric.isna().any() or not np.isfinite(numeric.to_numpy(dtype="float64")).all():
        raise PilotStopped("FRED_VIXCLS_NONFINITE_OR_MISSING")
    if not raw_summaries or any(
        count for summary in raw_summaries for count in summary["missing_by_column"].values()
    ):
        raise PilotStopped("FRED_VIXCLS_RAW_MISSING_FORWARD_FILL_FORBIDDEN")

    retained_path = root / "data/normalized/fred_vix_daily/year=2026/data.parquet"
    retained = pd.read_parquet(retained_path, columns=["date", "vixcls"])
    retained["date"] = pd.to_datetime(retained["date"]).dt.strftime("%Y-%m-%d")
    pilot = pd.DataFrame({
        "date": pd.to_datetime(frame.index).strftime("%Y-%m-%d"),
        "pilot": numeric.to_numpy(dtype="float64"),
    })
    overlap = pilot.merge(retained, on="date", how="inner", validate="one_to_one")
    if len(overlap) != len(pilot):
        raise PilotStopped("FRED_VIXCLS_RETAINED_OVERLAP_INCOMPLETE")
    difference = np.abs(overlap["pilot"].to_numpy() - overlap["vixcls"].to_numpy())
    if not np.all(difference == 0):
        raise PilotStopped("FRED_VIXCLS_RETAINED_VALUE_MISMATCH")
    return {
        "route_id": "fred_vixcls",
        "status": "BOUNDED_SCHEMA_AND_RETAINED_OVERLAP_PASS",
        "recommendation": "automatic_fallback_candidate_for_primary_schema_failure_only",
        "upstream_provider": "FRED",
        "series_id": "VIXCLS",
        "period": ["2026-08-11", "2026-08-12"],
        "row_count": len(frame),
        "columns": ["VIXCLS"],
        "date_index_name": "DATE",
        "ordering": "ascending_unique",
        "missing_count": int(numeric.isna().sum()),
        "nonfinite_count": int((~np.isfinite(numeric.to_numpy(dtype="float64"))).sum()),
        "raw_csv_summaries": raw_summaries,
        "retained_overlap_rows": len(overlap),
        "retained_max_abs_difference": float(difference.max(initial=0.0)),
        "currency": None,
        "units": "VIX index points",
        "price_semantics": "published close observation; not OHLC or adjusted price",
        "timezone": "FRED observation date; no intraday timestamp",
        "pit_finality": "DESCRIPTIVE_MATCH_ONLY_PREDICTIVE_VINTAGE_BLOCKED",
        "calls": [asdict(item) for item in transport.calls],
    }


def run_live(root: Path, *, output_name: str = "live_bounded_results.json") -> dict:
    started = datetime.now(timezone.utc).isoformat()
    routes = []
    global_stop = None
    runners = (
        ("sp500_current", lambda: _sp500_pilot()),
        ("fred_vixcls", lambda: _fred_pilot(root)),
    )
    global_stop_codes = {
        "UPSTREAM_IDENTITY_MISMATCH",
        "ROUTE_REQUEST_BUDGET_EXCEEDED",
        "TIMEOUT_POLICY_MISMATCH",
    }
    for route_id, runner in runners:
        try:
            routes.append(runner())
        except Exception as error:
            if isinstance(error, PilotStopped):
                candidate = str(error)
                safe_code = candidate if candidate.replace("_", "").isalnum() else "PILOT_STOPPED"
            elif isinstance(error, requests.Timeout):
                safe_code = "TIMEOUT"
            elif isinstance(error, requests.RequestException):
                safe_code = "TRANSPORT_ERROR"
            else:
                safe_code = "UNEXPECTED_PILOT_ERROR"
            routes.append({
                "route_id": route_id,
                "status": "ROUTE_STOPPED",
                "error_type": type(error).__name__,
                "safe_code": safe_code,
                "calls": [],
            })
            if safe_code in global_stop_codes or safe_code == "UNEXPECTED_PILOT_ERROR":
                global_stop = "PROCESS_SAFETY_DEFECT"
                break
    calls = [call for route in routes for call in route.get("calls", [])]
    if len(calls) > 3 or any(call["retry_count"] != 0 for call in calls):
        raise PilotStopped("GLOBAL_CALL_OR_RETRY_BUDGET_VIOLATION")
    result = {
        "version": 1,
        "mode": "live",
        "status": "LIVE_BOUNDED_PILOTS_COMPLETE" if global_stop is None else "GLOBAL_STOP",
        "started_at_utc": started,
        "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        "global_get_cap": 3,
        "provider_gets": len(calls),
        "provider_posts": 0,
        "retry_count": 0,
        "raw_responses_persisted": 0,
        "response_headers_persisted": 0,
        "global_stop": global_stop,
        "routes": routes,
    }
    _atomic_json(root / OUTPUT_RELATIVE / output_name, result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--mode", choices=("offline", "live"), required=True)
    parser.add_argument("--confirm-live-three-get-cap", action="store_true")
    parser.add_argument("--resume-zero-call-network-block", action="store_true")
    args = parser.parse_args(argv)
    root = args.project_root.resolve()
    if args.mode == "offline":
        if args.confirm_live_three_get_cap or args.resume_zero_call_network_block:
            raise SystemExit("live confirmation is invalid in offline mode")
        result = run_offline(root)
    else:
        if not args.confirm_live_three_get_cap:
            raise SystemExit("live mode requires explicit three-GET confirmation")
        output_name = "live_bounded_results.json"
        if args.resume_zero_call_network_block:
            prior_path = root / OUTPUT_RELATIVE / output_name
            if not prior_path.exists():
                raise SystemExit("zero-call network-block evidence is missing")
            prior = json.loads(prior_path.read_text(encoding="utf-8"))
            if (
                prior.get("provider_gets") != 0
                or not prior.get("routes")
                or any(route.get("safe_code") != "TRANSPORT_ERROR" for route in prior["routes"])
            ):
                raise SystemExit("prior live result is not a zero-call network block")
            output_name = "live_bounded_results_resume1.json"
        result = run_live(root, output_name=output_name)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
