"""Official CFTC annual COT Futures Only Raw backfill; Landing-only."""
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

from stock_data.providers.cftc import (
    DISAGGREGATED_FUTURES_ONLY_URL, TFF_FUTURES_ONLY_URL, CftcCotSchemaError,
    describe_historical_zip, parse_historical_zip, summarize_target_coverage,
)
from stock_data.providers.public_http_capture import capture_public_response


LANDING_RELATIVE = Path("data/landing/cftc/cot_historical_raw")
STATE_RELATIVE = Path("data/state/us_cftc_cot_historical_raw")
FIRST_YEAR = 2006
FAMILIES = (("tff", TFF_FUTURES_ONLY_URL), ("disaggregated", DISAGGREGATED_FUTURES_ONLY_URL))
HISTORICAL_COMBINED = (
    ("tff", "2006_2016", "https://www.cftc.gov/files/dea/history/fin_fut_txt_2006_2016.zip"),
    ("disaggregated", "2006_2016", "https://www.cftc.gov/files/dea/history/fut_disagg_txt_hist_2006_2016.zip"),
)
FIRST_ANNUAL_YEAR = 2017


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    stage = path.with_name(f".{path.name}.{uuid4().hex}.stage")
    with stage.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.write("\n")
        stream.flush(); os.fsync(stream.fileno())
    stage.replace(path)


def _verified_existing(project_root: Path, url: str) -> dict[str, object] | None:
    """Find one immutable matching Landing response and validate its receipt."""
    matches = []
    for record_path in (project_root / "data/landing/cftc").rglob("call.json"):
        record = json.loads(record_path.read_text(encoding="utf-8"))
        if record.get("provider") != "cftc" or record.get("request_url") != url:
            continue
        body = record_path.with_name(str(record.get("landing_body_file", "")))
        if not body.is_file() or _sha(body) != record.get("response_body_sha256") or body.stat().st_size != record.get("response_bytes"):
            raise CftcCotSchemaError(f"existing CFTC Landing provenance does not reconcile: {record_path}")
        matches.append((record, body))
    if not matches:
        return None
    bodies = {_sha(body) for _, body in matches}
    if len(bodies) != 1:
        raise CftcCotSchemaError(f"multiple CFTC Landing bodies disagree for {url}")
    record, body = sorted(matches, key=lambda pair: str(pair[0].get("captured_at_utc", "")))[0]
    return {
        "capture_status": "ADOPTED_EXISTING_VERIFIED", "landing": str(body.parent.relative_to(project_root)),
        "captured_at_utc": record["captured_at_utc"], "response_sha256": record["response_body_sha256"],
        "response_bytes": record["response_bytes"], "body": body.read_bytes(),
    }


def _capture(session, *, run_root: Path, family: str, url: str) -> dict[str, object]:
    response = session.get(url, headers={"User-Agent": "stock-investment-rev1/0.1"}, timeout=90)
    receipt = capture_public_response(
        root=run_root, provider="cftc", operation=f"cot_{family}_futures_only_annual_zip",
        request_url=url, request_parameters={}, response=response,
    )
    if int(response.status_code) != 200:
        raise CftcCotSchemaError(f"CFTC {family} HTTP {int(response.status_code)}")
    return {
        "capture_status": "CAPTURED", "landing": str(receipt.call_root),
        "captured_at_utc": receipt.captured_at_utc, "response_sha256": receipt.response_body_sha256,
        "response_bytes": receipt.response_bytes, "body": bytes(response.content),
    }


def _entry(project_root: Path, *, family: str, source_key: str, url: str, source: dict[str, object]) -> dict[str, object]:
    body = source.pop("body")
    schema = describe_historical_zip(body, family=family)
    rows = parse_historical_zip(body, family=family)
    coverage = summarize_target_coverage(rows, family=family, require_all=False)
    raw_rows_by_position_year: dict[str, int] = {}
    for row in rows:
        year = datetime.strptime(row["As_of_Date_In_Form_YYMMDD"].strip(), "%y%m%d").strftime("%Y")
        raw_rows_by_position_year[year] = raw_rows_by_position_year.get(year, 0) + 1
    location = source["landing"]
    if source["capture_status"] == "CAPTURED":
        location = str(Path(location).resolve().relative_to(project_root.resolve()))
    return {
        "family": family, "source_key": source_key, "source_url": url, **source,
        "landing": location, "schema": schema, "target_coverage": coverage,
        "raw_rows_by_position_year": raw_rows_by_position_year,
        "release_date_policy": "NULL_NOT_INFERRED_HISTORICAL_RELEASE_DATE_UNAVAILABLE",
    }


def run(project_root: Path, *, session=None, current_year: int | None = None) -> dict[str, object]:
    """Backfill every official annual file from the CFTC-provided 2006 start."""
    end_year = current_year or datetime.now(timezone.utc).year
    if end_year < FIRST_YEAR:
        raise ValueError("current year precedes CFTC TFF/Disaggregated availability")
    state_root = project_root / STATE_RELATIVE
    landing_parent = project_root / LANDING_RELATIVE
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex
    run_root = landing_parent / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    state_root.mkdir(parents=True, exist_ok=True)
    lock = state_root / ".backfill.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        raise CftcCotSchemaError("CFTC historical Raw backfill lock is already held") from error
    entries: list[dict[str, object]] = []
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(run_id)
        session = session or requests.Session()
        plan = list(HISTORICAL_COMBINED) + [
            (family, str(year), template.format(year=year))
            for year in range(FIRST_ANNUAL_YEAR, end_year + 1)
            for family, template in FAMILIES
        ]
        for family, source_key, url in plan:
                existing = _verified_existing(project_root, url)
                source = existing if existing is not None else _capture(session, run_root=run_root, family=family, url=url)
                entries.append(_entry(project_root, family=family, source_key=source_key, url=url, source=source))
                _atomic_json(state_root / "checkpoint.json", {
                    "version": 1, "status": "IN_PROGRESS", "run_id": run_id,
                    "last_completed": {"family": family, "source_key": source_key}, "entries": entries,
                    "normalized_mutation": False, "updated_at_utc": datetime.now(timezone.utc).isoformat(),
                })
        target_totals: dict[str, dict[str, object]] = {}
        for entry in entries:
            for target, item in entry["target_coverage"].items():
                total = target_totals.setdefault(target, {"rows": 0, "position_date_min": None, "position_date_max": None, "years_matched": [], "years_missing_identity": []})
                if item["rows"]:
                    total["rows"] += item["rows"]
                    total["position_date_min"] = min(filter(None, (total["position_date_min"], item["position_date_min"])))
                    total["position_date_max"] = max(filter(None, (total["position_date_max"], item["position_date_max"])))
                    total["years_matched"].extend(item["position_years"])
                else:
                    total["years_missing_identity"].append(entry["source_key"])
        for total in target_totals.values():
            total["years_matched"] = sorted(set(total["years_matched"]))
        yearly_source_status = {
            family: {
                str(year): next(({
                    "source_key": entry["source_key"], "raw_rows": entry["raw_rows_by_position_year"].get(str(year), 0),
                    "capture_status": entry["capture_status"],
                } for entry in entries if entry["family"] == family and str(year) in entry["raw_rows_by_position_year"]), {
                    "source_key": None, "raw_rows": 0, "capture_status": "MISSING_IN_OFFICIAL_RETAINED_SCOPE",
                })
                for year in range(FIRST_YEAR, end_year + 1)
            }
            for family, _ in FAMILIES
        }
        manifest = {
            "version": 1, "status": "RAW_BACKFILL_COMPLETE_PIT_BLOCKED", "provider": "cftc",
            "dataset_scope": "cot_tff_and_disaggregated_futures_only_annual_raw",
            "run_id": run_id, "years": [FIRST_YEAR, end_year], "entries": entries,
            "total_raw_files": len(entries), "total_raw_rows": sum(item["schema"]["raw_rows"] for item in entries),
            "target_coverage": target_totals,
            "yearly_source_status": yearly_source_status,
            "schema_fingerprints_by_family": {family: sorted({item["schema"]["header_sha256"] for item in entries if item["family"] == family}) for family, _ in FAMILIES},
            "release_date_policy": "NULL_NOT_INFERRED_HISTORICAL_RELEASE_DATE_UNAVAILABLE",
            "pit_predictive_use": "BLOCKED", "normalized_mutation": False, "canonical_mutation": False,
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        _atomic_json(run_root / "manifest.json", manifest)
        _atomic_json(state_root / "latest.json", {
            "version": 1, "status": manifest["status"], "run_id": run_id,
            "manifest": str((run_root / "manifest.json").relative_to(project_root)),
            "manifest_sha256": _sha(run_root / "manifest.json"), "total_raw_files": manifest["total_raw_files"],
            "total_raw_rows": manifest["total_raw_rows"], "normalized_mutation": False,
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        })
        return manifest
    except Exception as error:
        _atomic_json(run_root / "manifest.json", {
            "version": 1, "status": "RAW_BACKFILL_STOPPED_SCHEMA_OR_SOURCE_ERROR", "provider": "cftc",
            "run_id": run_id, "entries": entries, "failure_class": type(error).__name__, "failure_detail": str(error),
            "normalized_mutation": False, "canonical_mutation": False, "stopped_at_utc": datetime.now(timezone.utc).isoformat(),
        })
        raise
    finally:
        if lock.exists() and lock.read_text(encoding="utf-8") == run_id:
            lock.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--confirm-live-raw-backfill", action="store_true")
    args = parser.parse_args(argv)
    if not args.confirm_live_raw_backfill:
        raise SystemExit("explicit live Raw-backfill confirmation is required")
    try:
        result = run(args.project_root.resolve())
    except (CftcCotSchemaError, requests.RequestException) as error:
        raise SystemExit(str(error)) from error
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
