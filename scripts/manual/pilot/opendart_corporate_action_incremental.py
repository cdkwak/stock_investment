"""UR-110 single-use OpenDART corporate-action intake pilot.

The retained 2022-04-01..15 ECOPRO BM run is the API-zero baseline.  This
runner accepts only the separately approved 2022-06-14 incremental scope and
never writes a production dataset.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from dotenv import load_dotenv
import requests


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from stock_data.providers.opendart_corporate_action_intake import (  # noqa: E402
    FilingCursor, merge_pages_after_cursor, parse_list_page,
)
from stock_data.providers.opendart_free_issue import parse_observations  # noqa: E402


CORP_CODE = "01160363"
BEGIN_DATE = END_DATE = "20220614"
BASELINE_CURSOR = FilingCursor("20220406", "20220406002324")
MAX_LIST_PAGES = 2
MAX_HTTP_REQUESTS = 4
TIMEOUT_SECONDS = 10
RETRY_COUNT = 0
STATE_RELATIVE = Path("data/state/opendart_corporate_action_incremental/ur110_pilot.json")
LANDING_RELATIVE = Path("data/landing/diagnostics/opendart_corporate_action_incremental")
LIST_URL = "https://opendart.fss.or.kr/api/list.json"
EVENTS = (
    ("fricDecsn", "https://opendart.fss.or.kr/api/fricDecsn.json"),
    ("pifricDecsn", "https://opendart.fss.or.kr/api/pifricDecsn.json"),
)


class PilotStopped(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_url(url: str) -> str:
    parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _atomic_body(path: Path, body: bytes) -> None:
    if path.exists():
        raise PilotStopped("Landing response path already exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.stage")
    try:
        with temporary.open("xb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _fingerprint_run(run_dir: Path) -> dict[str, object]:
    responses = []
    for path in sorted(run_dir.glob("response_*.json")):
        body = path.read_bytes()
        responses.append({
            "file": path.name,
            "bytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
        })
    return {"responses": responses, "response_count": len(responses)}


def _api_zero_replay(project_root: Path) -> dict[str, object] | None:
    state_path = project_root / STATE_RELATIVE
    if not state_path.is_file():
        return None
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if state.get("status") != "COMPLETE" or state.get("scope") != {
        "corp_code": CORP_CODE, "begin_date": BEGIN_DATE, "end_date": END_DATE,
    }:
        raise PilotStopped("existing pilot state is not replayable")
    run_dir = project_root / str(state.get("run_dir", ""))
    if not run_dir.is_dir() or _fingerprint_run(run_dir) != state.get("landing_fingerprint"):
        raise PilotStopped("retained Landing fingerprint differs")
    return {
        "status": "NOOP_API_ZERO_REPLAY",
        "http_requests": 0,
        "run_dir": str(run_dir.relative_to(project_root)),
        "cursor": state["cursor"],
    }


def _event_family_matrix() -> list[dict[str, object]]:
    return [
        {"event_family": "bonus_free_issue", "decision": "observation_only",
         "missing": ["explicit revision parent", "exact class/ISIN", "ex/effective date", "finality", "fraction policy"]},
        {"event_family": "capital_reduction", "decision": "observation_only",
         "missing": ["positive retained exact terms", "exact class/ISIN", "consideration/fractions", "final effective/listing evidence"]},
        {"event_family": "merger", "decision": "observation_only",
         "missing": ["predecessor/successor stable IDs", "explicit revision parent", "final consideration and listing relation"]},
        {"event_family": "company_division", "decision": "observation_only",
         "missing": ["surviving/new stable IDs/classes", "final listing relation", "explicit revision parent"]},
        {"event_family": "rights_issue", "decision": "observation_only",
         "missing": ["final class entitlement/price", "rights instrument and fractions", "verified ex-date/finality"]},
        {"event_family": "cash_dividend", "decision": "observation_only",
         "missing": ["event ex-date", "intraday availability", "currency/tax basis", "explicit revision chain"]},
        {"event_family": "share_split_consolidation", "decision": "unsupported",
         "missing": ["accepted machine-readable exact event endpoint", "ratio/class/ISIN/effective date/revision/finality bundle"]},
    ]


def run(project_root: Path, *, session=None) -> dict[str, object]:
    project_root = project_root.resolve()
    replay = _api_zero_replay(project_root)
    if replay is not None:
        return replay
    # Application runtime use is authorized. This function never reads or logs
    # the file or any credential value.
    load_dotenv(dotenv_path=project_root / ".env", override=False)
    api_key = os.getenv("OPENDART_API_KEY", "")
    if len(api_key) != 40:
        raise PilotStopped("OpenDART runtime credential is unavailable")
    backend = session or requests.Session()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex
    run_dir = project_root / LANDING_RELATIVE / run_id
    stage = run_dir.with_name(f".{run_id}.stage")
    state_path = project_root / STATE_RELATIVE
    if run_dir.exists() or stage.exists():
        raise PilotStopped("pilot run identity collision")
    stage.mkdir(parents=True)
    calls: list[dict[str, object]] = []
    pages = []
    event_results = []
    request_count = 0

    def request(operation: str, url: str, public_parameters: dict[str, str]) -> tuple[bytes, str]:
        nonlocal request_count
        if request_count >= MAX_HTTP_REQUESTS:
            raise PilotStopped("global HTTP request budget exhausted")
        request_count += 1
        started = time.monotonic()
        try:
            response = backend.get(
                url, params={"crtfc_key": api_key, **public_parameters},
                timeout=TIMEOUT_SECONDS, allow_redirects=False,
            )
        except requests.RequestException as error:
            raise PilotStopped("OpenDART transport failed") from error
        body = response.content
        if api_key.encode() in body:
            raise PilotStopped("credential echo detected; response not retained")
        name = f"response_{request_count:02d}_{operation}.json"
        _atomic_body(stage / name, body)
        digest = hashlib.sha256(body).hexdigest()
        calls.append({
            "sequence": request_count, "operation": operation, "method": "GET",
            "url": _safe_url(url), "public_parameters": public_parameters,
            "timeout_seconds": TIMEOUT_SECONDS, "retry_count": RETRY_COUNT,
            "http_status": int(response.status_code),
            "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
            "response_bytes": len(body), "response_sha256": digest,
            "body_file": name,
        })
        if int(response.status_code) != 200:
            raise PilotStopped(f"OpenDART HTTP {response.status_code}")
        return body, _now()

    try:
        list_common = {
            "corp_code": CORP_CODE, "bgn_de": BEGIN_DATE, "end_de": END_DATE,
            "last_reprt_at": "N", "pblntf_ty": "B", "sort": "date",
            "sort_mth": "asc", "page_count": "100",
        }
        body, captured = request("list_page_1", LIST_URL, {**list_common, "page_no": "1"})
        first = parse_list_page(body, captured_at_utc=captured)
        pages.append(first)
        if first.total_page > MAX_LIST_PAGES:
            raise PilotStopped("list pagination exceeds the two-page pilot cap")
        if first.total_page == 2:
            body, captured = request("list_page_2", LIST_URL, {**list_common, "page_no": "2"})
            pages.append(parse_list_page(body, captured_at_utc=captured))
        selected, cursor = merge_pages_after_cursor(pages, BASELINE_CURSOR)
        for operation, url in EVENTS:
            body, captured = request(operation, url, {
                "corp_code": CORP_CODE, "bgn_de": BEGIN_DATE, "end_de": END_DATE,
            })
            classification, rows = parse_observations(
                operation, body, captured_at_utc=captured,
            )
            event_results.append({
                "operation": operation, "classification": classification,
                "rows": len(rows),
                "receipt_numbers": sorted({str(row["rcept_no"]) for row in rows}),
            })
        summary = {
            "version": 1, "status": "COMPLETE", "run_id": run_id,
            "scope": {"corp_code": CORP_CODE, "begin_date": BEGIN_DATE, "end_date": END_DATE},
            "baseline_cursor": {"receipt_date": BASELINE_CURSOR.receipt_date,
                                "receipt_no": BASELINE_CURSOR.receipt_no},
            "cursor": ({"receipt_date": cursor.receipt_date, "receipt_no": cursor.receipt_no}
                       if cursor else None),
            "list": {"pages": len(pages), "total_count": first.total_count,
                     "new_after_cursor": len(selected),
                     "receipt_numbers": [str(row["rcept_no"]) for row in selected]},
            "events": event_results,
            "event_family_matrix": _event_family_matrix(),
            "http_requests": request_count, "http_cap": MAX_HTTP_REQUESTS,
            "retry_count": RETRY_COUNT, "calls": calls,
            "production_writes": 0, "factor_candidates": 0,
            "identity_status": "CURRENT_AT_CAPTURE_EFFECTIVE_DATES_UNVERIFIED",
            "revision_parent_status": "UNVERIFIED_NO_EXPLICIT_PARENT",
            "backtest_eligible_events": 0,
            "completed_at_utc": _now(),
        }
        _atomic_json(stage / "summary.json", summary)
        if any(api_key.encode() in path.read_bytes() for path in stage.iterdir() if path.is_file()):
            raise PilotStopped("credential found in staged artifact")
        stage.replace(run_dir)
        state = {
            "version": 1, "status": "COMPLETE",
            "scope": summary["scope"], "cursor": summary["cursor"],
            "run_dir": str(run_dir.relative_to(project_root)),
            "landing_fingerprint": _fingerprint_run(run_dir),
            "http_requests": request_count, "retry_count": RETRY_COUNT,
            "production_writes": 0, "completed_at_utc": summary["completed_at_utc"],
        }
        _atomic_json(state_path, state)
        return {
            "status": "COMPLETE", "http_requests": request_count,
            "run_dir": str(run_dir.relative_to(project_root)),
            "cursor": summary["cursor"],
        }
    except BaseException:
        if stage.exists():
            stopped = {
                "version": 1, "status": "STOPPED", "run_id": run_id,
                "scope": {"corp_code": CORP_CODE, "begin_date": BEGIN_DATE, "end_date": END_DATE},
                "http_requests": request_count, "retry_count": RETRY_COUNT,
                "calls": calls, "stopped_at_utc": _now(),
            }
            _atomic_json(stage / "stopped.json", stopped)
            stage.replace(run_dir)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="UR-110 bounded OpenDART action intake")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--confirm-ur110-live-pilot", action="store_true")
    args = parser.parse_args(argv)
    if not args.confirm_ur110_live_pilot:
        print("Refusing to run without --confirm-ur110-live-pilot", file=sys.stderr)
        return 2
    result = run(args.project_root)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
