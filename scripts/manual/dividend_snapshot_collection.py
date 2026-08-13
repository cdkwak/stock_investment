"""Bounded, resumable Landing-first collection of one FSC dividend snapshot."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Callable
from urllib.parse import unquote
from uuid import uuid4

import requests

from stock_data.providers.data_go_kr.client import DataGoKrClient
from stock_data.providers.data_go_kr.data_v1 import ENDPOINTS, normalize_dividend
from stock_data.providers.data_go_kr.dividend_observation import load_dividend_observation


OPERATION = "GetStocDiviInfoService_V2/getDiviInfo_V2"
PAGE_SIZE = 9999
MAX_PAGES = 10
STATE_VERSION = 1


class DividendCollectionStopped(RuntimeError):
    pass


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8"))
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_list(path: Path, values: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            stream.flush(); os.fsync(stream.fileno())
        if json.loads(temporary.read_text(encoding="utf-8")) != values:
            raise DividendCollectionStopped("consolidated Landing read-back differs")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def provider_lock(path: Path, *, run_id: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise DividendCollectionStopped("data.go.kr provider lock already exists") from error
    try:
        os.write(descriptor, json.dumps({"run_id": run_id, "pid": os.getpid()}).encode("utf-8"))
        os.close(descriptor); descriptor = -1
        yield
    finally:
        if descriptor >= 0: os.close(descriptor)
        try: owner = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise DividendCollectionStopped("provider lock ownership cannot be verified") from error
        if owner.get("run_id") != run_id:
            raise DividendCollectionStopped("provider lock ownership changed")
        path.unlink()


class RecordingSession:
    def __init__(self, delegate: Any, call_cap: int) -> None:
        self.delegate = delegate
        self.call_cap = call_cap
        self.calls = 0
        self.last_response: Any | None = None

    def get(self, url: str, **kwargs: Any) -> Any:
        if self.calls >= self.call_cap:
            raise DividendCollectionStopped("invocation call cap exceeded")
        self.calls += 1
        response = self.delegate.get(url, **kwargs)
        self.last_response = response
        return response


def _credential_forms(key: str) -> tuple[bytes, ...]:
    return tuple(value.encode() for value in {key, unquote(key)} if value)


def _page_payload(path: Path, *, page_no: int, snapshot_date: str) -> tuple[dict[str, Any], int, int]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        response = payload["response"]; header = response["header"]; body = response["body"]
        items = body["items"]["item"]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise DividendCollectionStopped(f"retained page {page_no} shape differs") from error
    if not isinstance(items, list): items = [items]
    if (
        str(header.get("resultCode", "")).zfill(2) != "00"
        or int(body.get("pageNo", 0)) != page_no
        or int(body.get("numOfRows", 0)) != PAGE_SIZE
        or not items
        or len(items) > PAGE_SIZE
        or any(str(item.get("basDt")) != snapshot_date for item in items if isinstance(item, dict))
    ):
        raise DividendCollectionStopped(f"retained page {page_no} semantics differ")
    normalize_dividend(items)
    return payload, int(body["totalCount"]), len(items)


def _new_state(snapshot_date: str) -> dict[str, Any]:
    return {
        "schema": "stock_data.dividend_snapshot_collection", "version": STATE_VERSION,
        "status": "READY", "snapshot_date": snapshot_date, "page_size": PAGE_SIZE,
        "max_pages": MAX_PAGES, "expected_total": None, "expected_pages": None,
        "completed_pages": [], "page_evidence": [], "business_calls": 0,
        "retries": 0, "landing_path": None, "updated_at_utc": _iso_now(),
        "historical_completeness": False, "predictive_use": False,
    }


def _load_state(path: Path, snapshot_date: str) -> dict[str, Any]:
    if not path.exists(): return _new_state(snapshot_date)
    try: state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise DividendCollectionStopped("checkpoint is invalid") from error
    if (
        state.get("schema") != "stock_data.dividend_snapshot_collection"
        or state.get("version") != STATE_VERSION
        or state.get("snapshot_date") != snapshot_date
        or state.get("page_size") != PAGE_SIZE
        or state.get("max_pages") != MAX_PAGES
        or state.get("retries") != 0
        or state.get("historical_completeness") is not False
        or state.get("predictive_use") is not False
        or not isinstance(state.get("completed_pages"), list)
        or not isinstance(state.get("page_evidence"), list)
    ):
        raise DividendCollectionStopped("checkpoint contract differs")
    return state


def collect_dividend_snapshot(
    *, project_root: Path, snapshot_date: str, service_key: str,
    max_calls: int = 2, delegate: Any = requests,
) -> dict[str, Any]:
    if re.fullmatch(r"20\d{6}", snapshot_date) is None:
        raise ValueError("snapshot_date must be YYYYMMDD")
    if max_calls < 1 or max_calls > 2:
        raise ValueError("max_calls must be 1 or 2")
    project_root = project_root.resolve()
    run_root = project_root / "data/landing/data_go_kr/kr_equity_dividend/snapshots" / snapshot_date
    state_path = project_root / "data/state/dividend_snapshot_collection" / f"{snapshot_date}.json"
    ledger_path = project_root / "data/state/dividend_snapshot_collection" / f"{snapshot_date}.ledger.jsonl"
    lock_path = project_root / "data/state/.data_go_kr_network.lock"
    run_id = "dividend_" + snapshot_date + "_" + uuid4().hex
    state = _load_state(state_path, snapshot_date)
    if state.get("status") == "COMPLETE":
        landing = project_root / str(state["landing_path"])
        load_dividend_observation(landing)
        return {"status": "ALREADY_COMPLETE", "calls_this_run": 0, **state}
    if state.get("status") in {
        "STOPPED", "BOUNDS_STOP", "TOTAL_CHANGED_STOP", "PAGE_COUNT_STOP",
    }:
        raise DividendCollectionStopped(
            f"checkpoint is terminal and requires offline audit: {state['status']}"
        )
    completed = list(map(int, state["completed_pages"]))
    if completed != list(range(1, len(completed) + 1)):
        raise DividendCollectionStopped("checkpoint pages are not contiguous")
    evidence = list(state["page_evidence"])
    for item in evidence:
        page = int(item["page_no"]); path = project_root / str(item["path"])
        _, total, rows = _page_payload(path, page_no=page, snapshot_date=snapshot_date)
        if _sha(path) != item["sha256"] or total != item["total_count"] or rows != item["item_count"]:
            raise DividendCollectionStopped("retained page differs from checkpoint")

    session = RecordingSession(delegate, max_calls)
    client = DataGoKrClient(
        endpoint=ENDPOINTS["dividend_https"], service_key=service_key,
        session=session, max_attempts=1,
    )
    with provider_lock(lock_path, run_id=run_id):
        while session.calls < max_calls:
            page_no = len(completed) + 1
            expected_pages = state.get("expected_pages")
            if expected_pages is not None and page_no > int(expected_pages): break
            started = _iso_now(); error_type = None
            try:
                page = client.fetch_page(
                    filters={"basDt": snapshot_date}, num_of_rows=PAGE_SIZE,
                    page_no=page_no,
                )
            except Exception as error:
                error_type = type(error).__name__
                raw = bytes(session.last_response.content) if session.last_response is not None else b""
                if any(value in raw for value in _credential_forms(service_key)):
                    raw = b"[REDACTED_CREDENTIAL_ECHO]"
                failure = run_root / f"page={page_no:05d}.failure.bin"
                failure.parent.mkdir(parents=True, exist_ok=True); failure.write_bytes(raw)
                state["status"] = "STOPPED"; state["updated_at_utc"] = _iso_now()
                state["terminal_evidence"] = {
                    "path": failure.relative_to(project_root).as_posix(),
                    "sha256": _sha(failure), "page_no": page_no,
                }
                _atomic_json(state_path, state)
                with ledger_path.open("a", encoding="utf-8", newline="\n") as stream:
                    stream.write(json.dumps({"run_id": run_id, "page_no": page_no,
                        "attempt": 1, "retry_count": 0, "started_at_utc": started,
                        "completed_at_utc": _iso_now(), "outcome": "STOPPED",
                        "error_type": error_type, "http_status": getattr(session.last_response, "status_code", None),
                        "failure_sha256": _sha(failure)}, sort_keys=True) + "\n")
                return {"status": "STOPPED", "calls_this_run": session.calls,
                        "error_type": error_type, "completed_pages": completed}
            payload = dict(page.payload)
            page_path = run_root / f"page={page_no:05d}.json"
            _atomic_json(page_path, payload)
            parsed, total, rows = _page_payload(page_path, page_no=page_no, snapshot_date=snapshot_date)
            if total != page.total_count or rows != len(page.items):
                raise DividendCollectionStopped("Landing page differs from parsed response")
            if state["expected_total"] is None:
                pages = math.ceil(total / PAGE_SIZE)
                if total < 1 or pages > MAX_PAGES:
                    state["status"] = "BOUNDS_STOP"
                    state["terminal_evidence"] = {
                        "path": page_path.relative_to(project_root).as_posix(),
                        "sha256": _sha(page_path), "page_no": page_no,
                    }
                    _atomic_json(state_path, state)
                    return {"status": "BOUNDS_STOP", "calls_this_run": session.calls,
                            "expected_total": total, "expected_pages": pages}
                state["expected_total"] = total; state["expected_pages"] = pages
            elif total != state["expected_total"]:
                state["status"] = "TOTAL_CHANGED_STOP"
                state["terminal_evidence"] = {
                    "path": page_path.relative_to(project_root).as_posix(),
                    "sha256": _sha(page_path), "page_no": page_no,
                }
                _atomic_json(state_path, state)
                return {"status": "TOTAL_CHANGED_STOP", "calls_this_run": session.calls}
            expected_rows = total - PAGE_SIZE * (page_no - 1) if page_no == state["expected_pages"] else PAGE_SIZE
            if rows != expected_rows:
                state["status"] = "PAGE_COUNT_STOP"
                state["terminal_evidence"] = {
                    "path": page_path.relative_to(project_root).as_posix(),
                    "sha256": _sha(page_path), "page_no": page_no,
                }
                _atomic_json(state_path, state)
                return {"status": "PAGE_COUNT_STOP", "calls_this_run": session.calls}
            completed.append(page_no)
            evidence.append({"page_no": page_no, "path": page_path.relative_to(project_root).as_posix(),
                             "sha256": _sha(page_path), "item_count": rows, "total_count": total})
            state.update({"status": "RUNNING", "completed_pages": completed,
                          "page_evidence": evidence, "business_calls": int(state["business_calls"]) + 1,
                          "updated_at_utc": _iso_now()})
            _atomic_json(state_path, state)
            ledger_path.parent.mkdir(parents=True, exist_ok=True)
            with ledger_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps({"run_id": run_id, "operation": OPERATION,
                    "snapshot_date": snapshot_date, "page_no": page_no, "num_of_rows": PAGE_SIZE,
                    "attempt": 1, "retry_count": 0, "started_at_utc": started,
                    "completed_at_utc": _iso_now(), "outcome": "SUCCESS",
                    "http_status": int(session.last_response.status_code),
                    "landing_sha256": _sha(page_path), "item_count": rows,
                    "total_count": total, "credential_values_persisted": False}, sort_keys=True) + "\n")
            if page_no == state["expected_pages"]: break

    if len(completed) == state.get("expected_pages"):
        pages = [_page_payload(project_root / item["path"], page_no=item["page_no"],
                               snapshot_date=snapshot_date)[0] for item in evidence]
        landing_path = run_root / "full_history.json"
        _atomic_list(landing_path, pages)
        frame, metadata = load_dividend_observation(landing_path)
        if (
            len(frame) != state["expected_total"]
            or str(metadata["source_snapshot_date"]).replace("-", "") != snapshot_date
        ):
            raise DividendCollectionStopped("complete Landing validation differs")
        state.update({"status": "COMPLETE", "landing_path": landing_path.relative_to(project_root).as_posix(),
                      "landing_sha256": _sha(landing_path), "updated_at_utc": _iso_now()})
        _atomic_json(state_path, state)
    return {"status": state["status"], "calls_this_run": session.calls,
            "completed_pages": completed, "expected_pages": state.get("expected_pages"),
            "expected_total": state.get("expected_total"), "landing_path": state.get("landing_path")}
