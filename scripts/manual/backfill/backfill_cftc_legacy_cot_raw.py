"""Official CFTC Legacy COT Raw backfill in a report-family-isolated namespace."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

import requests


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stock_data.providers.cftc import CftcCotSchemaError
from stock_data.providers.cftc_legacy import (
    LEGACY_FUTURES_ONLY, LEGACY_FUTURES_OPTIONS_COMBINED,
    parse_historical_zip, parse_position_date,
)
from stock_data.providers.public_http_capture import capture_public_response


LANDING_RELATIVE = Path("data/landing/cftc/legacy_cot_historical_raw")
STATE_RELATIVE = Path("data/state/us_cftc_legacy_cot_historical_raw")
BASE = "https://www.cftc.gov/files/dea/history/"
SPECS = (
    (LEGACY_FUTURES_ONLY, 1986, "1986_2016", BASE + "deacot1986_2016.zip", "deacot{year}.zip", 2017),
    (LEGACY_FUTURES_OPTIONS_COMBINED, 1995, "1995_2016", BASE + "deahistfo_1995_2016.zip", "deahistfo{year}.zip", 2017),
)
SPARSE_LIMITATION = {
    "before_position_date": "1992-09-30",
    "source_behavior": "MID_MONTH_AND_MONTH_END_ONLY",
    "source_warning": "mid-month data was not published before that time and may contain identifiable data errors",
    "applies_to": LEGACY_FUTURES_ONLY,
}


def _sha(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    stage = path.with_name(f".{path.name}.{uuid4().hex}.stage")
    with stage.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2); stream.write("\n")
        stream.flush(); os.fsync(stream.fileno())
    stage.replace(path)


def _existing(project_root: Path, url: str) -> dict[str, object] | None:
    matches = []
    for record_path in (project_root / "data/landing/cftc/legacy_cot_historical_raw").rglob("call.json"):
        record = json.loads(record_path.read_text(encoding="utf-8"))
        if record.get("provider") != "cftc" or record.get("request_url") != url: continue
        body = record_path.with_name(str(record.get("landing_body_file", "")))
        if not body.is_file() or _sha(body) != record.get("response_body_sha256") or body.stat().st_size != record.get("response_bytes"):
            raise CftcCotSchemaError(f"Legacy CFTC Landing provenance does not reconcile: {record_path}")
        matches.append((record, body))
    if not matches: return None
    if len({_sha(body) for _, body in matches}) != 1:
        raise CftcCotSchemaError(f"Legacy CFTC Landing bodies disagree for {url}")
    record, body = sorted(matches, key=lambda item: str(item[0].get("captured_at_utc", "")))[0]
    return {"capture_status": "ADOPTED_EXISTING_VERIFIED", "landing": str(body.parent.relative_to(project_root)), "captured_at_utc": record["captured_at_utc"], "response_sha256": record["response_body_sha256"], "response_bytes": record["response_bytes"], "body": body.read_bytes()}


def _capture(session, *, run_root: Path, report_type: str, url: str) -> dict[str, object]:
    response = session.get(url, headers={"User-Agent": "stock-investment-rev1/0.1"}, timeout=120)
    operation = "legacy_futures_only_zip" if report_type == LEGACY_FUTURES_ONLY else "legacy_futures_options_combined_zip"
    receipt = capture_public_response(root=run_root, provider="cftc", operation=operation, request_url=url, request_parameters={}, response=response)
    if int(response.status_code) != 200: raise CftcCotSchemaError(f"CFTC {report_type} HTTP {int(response.status_code)}")
    return {"capture_status": "CAPTURED", "landing": str(receipt.call_root), "captured_at_utc": receipt.captured_at_utc, "response_sha256": receipt.response_body_sha256, "response_bytes": receipt.response_bytes, "body": bytes(response.content)}


def _entry(project_root: Path, *, report_type: str, source_key: str, url: str, source: dict[str, object]) -> dict[str, object]:
    body = source.pop("body")
    rows, schema = parse_historical_zip(body, report_type=report_type)
    by_year: dict[str, int] = {}
    for row in rows:
        year = parse_position_date(row[str(schema["source_fields"]["position_date"])])[:4]
        by_year[year] = by_year.get(year, 0) + 1
    landing = source["landing"]
    if source["capture_status"] == "CAPTURED": landing = str(Path(landing).resolve().relative_to(project_root.resolve()))
    return {"report_type": report_type, "source_key": source_key, "source_url": url, **source, "landing": landing, "schema": schema, "raw_rows_by_position_year": by_year, "release_date": None, "release_date_policy": "NULL_NOT_INFERRED_HISTORICAL_RELEASE_DATE_UNAVAILABLE", "participant_category_policy": "SOURCE_COLUMNS_UNCHANGED_NO_CROSS_FAMILY_MAPPING"}


def run(project_root: Path, *, session=None, current_year: int | None = None) -> dict[str, object]:
    end_year = current_year or datetime.now(timezone.utc).year
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex
    run_root = project_root / LANDING_RELATIVE / run_id; run_root.mkdir(parents=True, exist_ok=False)
    state_root = project_root / STATE_RELATIVE; state_root.mkdir(parents=True, exist_ok=True)
    lock = state_root / ".backfill.lock"
    try: descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error: raise CftcCotSchemaError("Legacy CFTC Raw backfill lock is already held") from error
    entries: list[dict[str, object]] = []
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream: stream.write(run_id)
        session = session or requests.Session()
        plan = []
        for report_type, first_year, combined_key, combined_url, annual_format, first_annual in SPECS:
            plan.append((report_type, combined_key, combined_url))
            plan.extend((report_type, str(year), BASE + annual_format.format(year=year)) for year in range(first_annual, end_year + 1))
        for report_type, source_key, url in plan:
            source = _existing(project_root, url) or _capture(session, run_root=run_root, report_type=report_type, url=url)
            entries.append(_entry(project_root, report_type=report_type, source_key=source_key, url=url, source=source))
            _atomic_json(state_root / "checkpoint.json", {"version": 1, "status": "IN_PROGRESS", "run_id": run_id, "last_completed": {"report_type": report_type, "source_key": source_key}, "entries": entries, "normalized_mutation": False})
        totals = {kind: sum(item["schema"]["raw_rows"] for item in entries if item["report_type"] == kind) for kind, *_ in SPECS}
        manifest = {"version": 1, "status": "RAW_BACKFILL_COMPLETE_PIT_BLOCKED", "provider": "cftc", "run_id": run_id, "report_types": [LEGACY_FUTURES_ONLY, LEGACY_FUTURES_OPTIONS_COMBINED, "TFF_FUTURES_ONLY", "DISAGGREGATED_FUTURES_ONLY"], "entries": entries, "raw_rows_by_report_type": totals, "total_raw_files": len(entries), "total_raw_rows": sum(totals.values()), "schema_fingerprints_by_report_type": {kind: sorted({item["schema"]["header_sha256"] for item in entries if item["report_type"] == kind}) for kind, *_ in SPECS}, "sparse_reporting_limitation": SPARSE_LIMITATION, "release_date_policy": "NULL_NOT_INFERRED_HISTORICAL_RELEASE_DATE_UNAVAILABLE", "pit_predictive_use": "BLOCKED", "normalized_mutation": False, "canonical_mutation": False, "completed_at_utc": datetime.now(timezone.utc).isoformat()}
        _atomic_json(run_root / "manifest.json", manifest)
        _atomic_json(state_root / "latest.json", {"version": 1, "status": manifest["status"], "run_id": run_id, "manifest": str((run_root / "manifest.json").relative_to(project_root)), "manifest_sha256": _sha(run_root / "manifest.json"), "total_raw_files": manifest["total_raw_files"], "total_raw_rows": manifest["total_raw_rows"], "normalized_mutation": False})
        return manifest
    except Exception as error:
        _atomic_json(run_root / "manifest.json", {"version": 1, "status": "RAW_BACKFILL_STOPPED_SCHEMA_OR_SOURCE_ERROR", "provider": "cftc", "run_id": run_id, "entries": entries, "failure_class": type(error).__name__, "failure_detail": str(error), "normalized_mutation": False, "canonical_mutation": False})
        raise
    finally:
        if lock.exists() and lock.read_text(encoding="utf-8") == run_id: lock.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--project-root", type=Path, required=True); parser.add_argument("--confirm-live-legacy-raw-backfill", action="store_true"); args = parser.parse_args(argv)
    if not args.confirm_live_legacy_raw_backfill: raise SystemExit("explicit Legacy Raw-backfill confirmation is required")
    try: result = run(args.project_root.resolve())
    except (CftcCotSchemaError, requests.RequestException) as error: raise SystemExit(str(error)) from error
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2)); return 0


if __name__ == "__main__": raise SystemExit(main())
