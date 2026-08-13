"""Bounded, resumable Landing-first stock-issuance snapshot collection."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Any, Callable
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from scripts.manual.data_go_kr_stock_issuance_pilot import (  # noqa: E402
    CaptureSession, CURRENT_SCOPE_LANDING_RELATIVE, ENDPOINT, LOCK_RELATIVE,
    PilotError, _assert_plain, _assert_topology, _provider_lock, _secret_variants,
    _sha, _validate_items, verify_current_scope_run,
)
from stock_data.providers.data_go_kr.client import (  # noqa: E402
    DataGoKrClient, service_key_from_environment, write_landing_pages_atomic,
)


DATASET = "kr_equity_stock_issuance_source_observation"
LANDING_RELATIVE = Path(f"data/landing/data_go_kr/{DATASET}")
COUNT_RUN_ID = "20260813T172157Z_3d52035e3c1643fc8336fce42227323b"
COUNT_MANIFEST_SHA256 = "d44592d87c4d2fdd4799af6916610e74dbe2bcb7003313ac6ca7470429eb9129"
SNAPSHOT_DATE = "20260812"
EXPECTED_TOTAL = 152_676
PAGE_SIZE = 9_999
EXPECTED_PAGES = 16
VERSION = 1


class CollectionStopped(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_sha(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def frozen_plan() -> dict[str, object]:
    return {
        "version": VERSION, "dataset": DATASET, "endpoint": ENDPOINT,
        "operation": "getStocIssuInfo_V3", "filters": {},
        "snapshot_date": SNAPSHOT_DATE, "expected_total": EXPECTED_TOTAL,
        "page_size": PAGE_SIZE, "expected_pages": EXPECTED_PAGES,
        "retry_count": 0, "parallelism": 1,
        "count_run_id": COUNT_RUN_ID,
        "count_manifest_sha256": COUNT_MANIFEST_SHA256,
        "normalized_writes": False,
    }


def plan_sha256() -> str:
    return _canonical_sha(frozen_plan())


def _replace_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _append_jsonl(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def _count_run(project_root: Path) -> Path:
    return project_root / CURRENT_SCOPE_LANDING_RELATIVE / COUNT_RUN_ID


def _verify_count_gate(project_root: Path) -> None:
    result = verify_current_scope_run(project_root, _count_run(project_root))
    if (
        result.get("status") != "OFFLINE_AUDIT_PASS"
        or result.get("manifest_sha256") != COUNT_MANIFEST_SHA256
        or result.get("declared_total") != EXPECTED_TOTAL
        or result.get("pages_at_9999") != EXPECTED_PAGES
        or result.get("source_snapshot_date") != SNAPSHOT_DATE
    ):
        raise CollectionStopped("frozen current-scope evidence differs")


def _expected_page_rows(page_no: int) -> int:
    return PAGE_SIZE if page_no < EXPECTED_PAGES else EXPECTED_TOTAL - PAGE_SIZE * (EXPECTED_PAGES - 1)


def _page_dir(run_root: Path, page_no: int) -> Path:
    return run_root / f"page={page_no:05d}"


def _read_page(project_root: Path, run_root: Path, page_no: int, key: str) -> dict[str, object]:
    page_root = _page_dir(run_root, page_no)
    _assert_topology(project_root, page_root)
    files = {path.name: _assert_plain(path) for path in page_root.iterdir()}
    expected_files = {"raw_response.body", "raw_call.json", "response.json"}
    if set(files) != expected_files or not all(path.is_file() for path in files.values()):
        raise CollectionStopped(f"page {page_no} evidence topology differs")
    call = json.loads(files["raw_call.json"].read_text(encoding="utf-8"))
    expected_public = {
        "numOfRows": str(PAGE_SIZE), "pageNo": str(page_no), "resultType": "json",
    }
    if (
        call.get("sequence") != 1 or call.get("operation") != "getStocIssuInfo_V3"
        or call.get("endpoint") != ENDPOINT or call.get("public_parameters") != expected_public
        or call.get("http_status") != 200 or call.get("retry_count") != 0
        or call.get("response_sha256") != _sha(files["raw_response.body"])
        or call.get("response_bytes") != files["raw_response.body"].stat().st_size
    ):
        raise CollectionStopped(f"page {page_no} raw call evidence differs")
    raw = json.loads(files["raw_response.body"].read_bytes())
    landing = json.loads(files["response.json"].read_text(encoding="utf-8"))
    if landing != [raw]:
        raise CollectionStopped(f"page {page_no} parsed Landing differs")
    try:
        header, body = raw["response"]["header"], raw["response"]["body"]
        source_items = body["items"]["item"]
    except (KeyError, TypeError) as error:
        raise CollectionStopped(f"page {page_no} response shape differs") from error
    source_items = source_items if isinstance(source_items, list) else [source_items]
    expected_rows = _expected_page_rows(page_no)
    if (
        str(header.get("resultCode", "")).zfill(2) != "00"
        or int(body.get("pageNo", 0)) != page_no
        or int(body.get("numOfRows", 0)) != PAGE_SIZE
        or int(body.get("totalCount", -1)) != EXPECTED_TOTAL
        or len(source_items) != expected_rows
    ):
        raise CollectionStopped(f"page {page_no} count/envelope differs")
    assessment = _validate_items(
        tuple(dict(item) for item in source_items), expected_count=expected_rows,
        expected_snapshot_date=SNAPSHOT_DATE,
    )
    if any(secret in path.read_bytes() for path in files.values() for secret in _secret_variants(key)):
        raise CollectionStopped(f"configured credential found in page {page_no}")
    return {
        "page_no": page_no, "rows": expected_rows,
        "page_root": page_root.relative_to(project_root).as_posix(),
        "raw_sha256": _sha(files["raw_response.body"]),
        "raw_bytes": files["raw_response.body"].stat().st_size,
        "call_sha256": _sha(files["raw_call.json"]),
        "landing_sha256": _sha(files["response.json"]),
        "captured_at_utc": call["captured_at_utc"],
        "future_effective_rows": assessment["future_effective_rows"],
    }


def _new_checkpoint(run_id: str) -> dict[str, object]:
    return {
        "schema": "stock_data.stock_issuance_snapshot_collection",
        "version": VERSION, "status": "RUNNING", "run_id": run_id,
        "plan": frozen_plan(), "plan_sha256": plan_sha256(),
        "completed_pages": [], "page_evidence": [],
        "network_calls": 0, "retry_count": 0,
        "started_at_utc": _now(), "updated_at_utc": _now(),
    }


def _load_checkpoint(run_root: Path) -> dict[str, object]:
    path = run_root / "checkpoint.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise CollectionStopped("checkpoint is missing or invalid") from error
    if (
        state.get("schema") != "stock_data.stock_issuance_snapshot_collection"
        or state.get("version") != VERSION or state.get("run_id") != run_root.name
        or state.get("plan") != frozen_plan() or state.get("plan_sha256") != plan_sha256()
        or state.get("retry_count") != 0
        or not isinstance(state.get("completed_pages"), list)
        or not isinstance(state.get("page_evidence"), list)
    ):
        raise CollectionStopped("checkpoint contract differs")
    return state


def collect_snapshot(
    *, project_root: Path, approval_sha256: str, max_calls: int,
    run_root: Path | None = None, delegate: Any = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    project_root = project_root.resolve()
    if approval_sha256 != plan_sha256():
        raise CollectionStopped("frozen plan approval digest differs")
    if max_calls < 1 or max_calls > EXPECTED_PAGES:
        raise ValueError("max_calls is outside the frozen page budget")
    _verify_count_gate(project_root)
    key = service_key_from_environment(project_root)
    _assert_topology(project_root, project_root / LANDING_RELATIVE)
    _assert_topology(project_root, project_root / LOCK_RELATIVE)
    if run_root is None:
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ_") + uuid4().hex
        run_root = project_root / LANDING_RELATIVE / run_id
        state = _new_checkpoint(run_id)
        is_new = True
    else:
        run_root = Path(os.path.abspath(run_root))
        if run_root.parent != project_root / LANDING_RELATIVE:
            raise CollectionStopped("resume run is not an immediate Landing child")
        _assert_topology(project_root, run_root)
        state = _load_checkpoint(run_root)
        if state["status"] == "COMPLETE_REVIEW_REQUIRED":
            return {"status": "ALREADY_COMPLETE", "calls_this_run": 0, **state}
        if state["status"] != "RUNNING":
            raise CollectionStopped(f"checkpoint is terminal: {state['status']}")
        is_new = False
    completed = list(map(int, state["completed_pages"]))
    if completed != list(range(1, len(completed) + 1)):
        raise CollectionStopped("completed pages are not contiguous")
    if len(state["page_evidence"]) != len(completed):
        raise CollectionStopped("checkpoint evidence count differs")
    for page_no, expected in zip(completed, state["page_evidence"], strict=True):
        rebuilt = _read_page(project_root, run_root, page_no, key)
        if rebuilt != expected:
            raise CollectionStopped("retained page differs from checkpoint")
    lock_id = "issuance_" + run_root.name
    calls_this_run = 0
    with _provider_lock(project_root, lock_id):
        if is_new:
            run_root.mkdir(parents=True, exist_ok=False)
            _replace_json(run_root / "checkpoint.json", state)
        while len(completed) < EXPECTED_PAGES and calls_this_run < max_calls:
            page_no = len(completed) + 1
            page_root = _page_dir(run_root, page_no)
            if page_root.exists():
                try:
                    evidence = _read_page(project_root, run_root, page_no, key)
                except Exception as error:
                    state.update({"status": "STOPPED_ORPHAN_PAGE", "updated_at_utc": _now(),
                                  "error_type": type(error).__name__, "failed_page": page_no})
                    _replace_json(run_root / "checkpoint.json", state)
                    raise
                event = "PAGE_RECOVERED_WITHOUT_REQUEST"
            else:
                public = {
                    "numOfRows": str(PAGE_SIZE), "pageNo": str(page_no),
                    "resultType": "json",
                }
                capture = CaptureSession(
                    delegate or __import__("requests"), page_root, key, public,
                )
                page_root.mkdir(parents=True, exist_ok=False)
                try:
                    page = DataGoKrClient(
                        endpoint=ENDPOINT, service_key=key, session=capture,
                        max_attempts=1, timeout_seconds=30,
                    ).fetch_page(num_of_rows=PAGE_SIZE, page_no=page_no)
                    write_landing_pages_atomic((page.payload,), page_root / "response.json")
                    evidence = _read_page(project_root, run_root, page_no, key)
                except Exception as error:
                    state.update({"status": "STOPPED", "updated_at_utc": _now(),
                                  "error_type": type(error).__name__, "failed_page": page_no,
                                  "network_calls": int(state["network_calls"]) + capture.calls})
                    _replace_json(run_root / "checkpoint.json", state)
                    _append_jsonl(run_root / "call_ledger.jsonl", {
                        "event": "PAGE_STOPPED", "page_no": page_no,
                        "network_calls": capture.calls, "retry_count": 0,
                        "error_type": type(error).__name__, "recorded_at_utc": _now(),
                    })
                    return {"status": "STOPPED", "calls_this_run": calls_this_run + capture.calls,
                            "completed_pages": completed, "failed_page": page_no}
                calls_this_run += capture.calls
                state["network_calls"] = int(state["network_calls"]) + capture.calls
                event = "PAGE_CAPTURED"
            completed.append(page_no)
            state["completed_pages"] = completed
            state["page_evidence"] = [*state["page_evidence"], evidence]
            state["updated_at_utc"] = _now()
            _append_jsonl(run_root / "call_ledger.jsonl", {
                "event": event, "page_no": page_no, "network_calls": 0 if event.endswith("WITHOUT_REQUEST") else 1,
                "retry_count": 0, "evidence": evidence, "recorded_at_utc": _now(),
            })
            _replace_json(run_root / "checkpoint.json", state)
            if calls_this_run < max_calls and len(completed) < EXPECTED_PAGES:
                sleep_fn(0.5)
        if len(completed) == EXPECTED_PAGES:
            manifest = {
                "version": VERSION, "status": "COMPLETE_REVIEW_REQUIRED",
                "run_id": run_root.name, "plan": frozen_plan(),
                "plan_sha256": plan_sha256(), "rows": EXPECTED_TOTAL,
                "pages": EXPECTED_PAGES, "network_calls": state["network_calls"],
                "retry_count": 0, "page_evidence": state["page_evidence"],
                "ledger_sha256": _sha(run_root / "call_ledger.jsonl"),
                "normalized_writes": False,
            }
            _replace_json(run_root / "full_snapshot_manifest.json", manifest)
            state.update({
                "status": "COMPLETE_REVIEW_REQUIRED", "updated_at_utc": _now(),
                "full_snapshot_manifest_sha256": _sha(run_root / "full_snapshot_manifest.json"),
                "ledger_sha256": manifest["ledger_sha256"],
            })
            _replace_json(run_root / "checkpoint.json", state)
    return {
        "status": state["status"], "run_root": str(run_root),
        "calls_this_run": calls_this_run, "network_calls_total": state["network_calls"],
        "completed_pages": completed, "expected_pages": EXPECTED_PAGES,
    }


def verify_complete_snapshot(project_root: Path, run_root: Path) -> dict[str, object]:
    project_root = project_root.resolve()
    run_root = Path(os.path.abspath(run_root))
    _verify_count_gate(project_root)
    key = service_key_from_environment(project_root)
    state = _load_checkpoint(run_root)
    if state.get("status") != "COMPLETE_REVIEW_REQUIRED":
        raise CollectionStopped("snapshot checkpoint is not complete")
    if state.get("completed_pages") != list(range(1, EXPECTED_PAGES + 1)):
        raise CollectionStopped("snapshot page set is incomplete")
    evidence = []
    total_future = 0
    exact_records: set[str] = set()
    reason_counts: dict[str, int] = {}
    for page_no in range(1, EXPECTED_PAGES + 1):
        rebuilt = _read_page(project_root, run_root, page_no, key)
        evidence.append(rebuilt)
        total_future += int(rebuilt["future_effective_rows"])
        payload = json.loads((_page_dir(run_root, page_no) / "raw_response.body").read_bytes())
        rows = payload["response"]["body"]["items"]["item"]
        rows = rows if isinstance(rows, list) else [rows]
        for item in rows:
            canonical = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            if canonical in exact_records:
                raise CollectionStopped("duplicate exact source record across pages")
            exact_records.add(canonical)
            reason = str(item.get("stckIssuRcdNm") or "<missing>")
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    if evidence != state["page_evidence"] or len(exact_records) != EXPECTED_TOTAL:
        raise CollectionStopped("complete snapshot evidence differs")
    manifest_path = run_root / "full_snapshot_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        manifest.get("status") != "COMPLETE_REVIEW_REQUIRED"
        or manifest.get("rows") != EXPECTED_TOTAL or manifest.get("pages") != EXPECTED_PAGES
        or manifest.get("page_evidence") != evidence
        or manifest.get("ledger_sha256") != _sha(run_root / "call_ledger.jsonl")
        or state.get("full_snapshot_manifest_sha256") != _sha(manifest_path)
        or state.get("ledger_sha256") != manifest.get("ledger_sha256")
    ):
        raise CollectionStopped("complete snapshot manifest differs")
    return {
        "status": "OFFLINE_AUDIT_PASS", "network_requests": 0,
        "run_id": run_root.name, "rows": EXPECTED_TOTAL, "pages": EXPECTED_PAGES,
        "source_snapshot_date": SNAPSHOT_DATE,
        "future_effective_rows": total_future,
        "issuance_reason_counts": dict(sorted(reason_counts.items())),
        "manifest_sha256": _sha(manifest_path),
        "ledger_sha256": _sha(run_root / "call_ledger.jsonl"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--approve-plan-sha256")
    parser.add_argument("--max-calls", type=int, default=2)
    parser.add_argument("--start", action="store_true")
    parser.add_argument("--resume-run", type=Path)
    parser.add_argument("--verify-run", type=Path)
    parser.add_argument("--print-plan", action="store_true")
    args = parser.parse_args(argv)
    if args.print_plan:
        print(json.dumps({"plan": frozen_plan(), "plan_sha256": plan_sha256()}, indent=2))
        return 0
    if args.verify_run is not None:
        print(json.dumps(verify_complete_snapshot(args.project_root, args.verify_run),
                         ensure_ascii=False, sort_keys=True, indent=2))
        return 0
    if args.start == (args.resume_run is not None):
        raise SystemExit("choose exactly one of --start or --resume-run")
    result = collect_snapshot(
        project_root=args.project_root, approval_sha256=str(args.approve_plan_sha256),
        max_calls=args.max_calls, run_root=args.resume_run,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
