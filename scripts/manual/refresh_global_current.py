"""Capture-first, review-gated refresh for the retained Yahoo/FRED datasets.

The live command can only create immutable Landing evidence and a candidate
tree.  Publication is a separate, zero-network command guarded by a content
manifest compare-and-swap (CAS).
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from uuid import uuid4

import pandas as pd
import requests
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stock_data.contracts.global_market import (  # noqa: E402
    FRED_TREASURY_YIELD_DAILY, FRED_USD_FX_DAILY,
    GLOBAL_INDEX_PRICE_DAILY, US_TREASURY_SPREAD_DAILY,
)
from stock_data.derived.treasury_spread import (  # noqa: E402
    calculate_treasury_spreads, validate_treasury_spreads,
)
from stock_data.providers.fred import fetch_series  # noqa: E402
from stock_data.providers.fred import URL as FRED_URL  # noqa: E402
from stock_data.providers.yahoo import CONFIG, _epoch, fetch_global_index  # noqa: E402
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic  # noqa: E402
from stock_data.validation.global_market import validate_fred, validate_global_index  # noqa: E402


PHASES = {
    "yahoo": (3, GLOBAL_INDEX_PRICE_DAILY, tuple(CONFIG)),
    "fred_yields": (3, FRED_TREASURY_YIELD_DAILY, ("DGS2", "DGS10", "DGS30")),
    "fred_fx": (2, FRED_USD_FX_DAILY, ("DEXKOUS", "DEXJPUS")),
}
LOCK = Path("data/state/global_current_refresh.lock")
REPARSE_POINT = 0x400


class RefreshError(RuntimeError):
    pass


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _assert_plain_path(base: Path, path: Path, *, must_exist: bool = True) -> Path:
    """Reject escape, links, junctions/reparse points, and unexpected topology."""
    base = Path(os.path.abspath(base))
    absolute = Path(os.path.abspath(path))
    try:
        absolute.relative_to(base)
    except ValueError as error:
        raise RefreshError("path escapes its required root") from error
    current = base
    for component in absolute.relative_to(base).parts:
        current /= component
        if os.path.lexists(current):
            info = current.lstat()
            if current.is_symlink() or (getattr(info, "st_file_attributes", 0) & REPARSE_POINT):
                raise RefreshError("links/reparse points are forbidden in refresh paths")
    if must_exist and not absolute.exists():
        raise RefreshError("required refresh path does not exist")
    return absolute


def _files_manifest(root: Path) -> dict[str, object]:
    if not root.is_dir():
        raise RefreshError(f"dataset root is absent: {root}")
    _assert_plain_path(root.parent, root)
    partition_keys = {
        GLOBAL_INDEX_PRICE_DAILY.name: ("symbol", "year"),
        FRED_TREASURY_YIELD_DAILY.name: ("year",),
        FRED_USD_FX_DAILY.name: ("year",),
        US_TREASURY_SPREAD_DAILY.name: ("year",),
    }.get(root.name)
    if partition_keys is None:
        raise RefreshError(f"unknown dataset topology: {root.name}")
    entries_on_disk = sorted(root.rglob("*"))
    for entry in entries_on_disk:
        _assert_plain_path(root, entry)
        relative = entry.relative_to(root)
        if entry.is_dir():
            parts = relative.parts
            if len(parts) > len(partition_keys):
                raise RefreshError(f"unexpected nested dataset directory: {relative}")
            for number, part in enumerate(parts):
                prefix = partition_keys[number] + "="
                if not part.startswith(prefix) or not part[len(prefix):]:
                    raise RefreshError(f"unexpected dataset directory: {relative}")
    all_files = [path for path in entries_on_disk if path.is_file()]
    files = []
    for path in all_files:
        relative = path.relative_to(root).as_posix()
        parts = Path(relative).parts
        if len(parts) != len(partition_keys) + 1 or parts[-1] != "data.parquet":
            raise RefreshError(f"unexpected dataset topology: {relative}")
        values = {}
        for number, key in enumerate(partition_keys):
            prefix = key + "="
            if not parts[number].startswith(prefix):
                raise RefreshError(f"unexpected dataset topology: {relative}")
            values[key] = parts[number][len(prefix):]
        try:
            year = int(values["year"])
        except ValueError as error:
            raise RefreshError(f"invalid year partition: {relative}") from error
        if year < 1800 or year > 2200:
            raise RefreshError(f"invalid year partition: {relative}")
        _assert_plain_path(root, path)
        files.append(path)
    digest = hashlib.sha256()
    rows = 0
    entries = []
    for path in files:
        body_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        relative = path.relative_to(root).as_posix()
        count = len(pd.read_parquet(path, columns=["date"]))
        entries.append({"path": relative, "rows": count, "sha256": body_hash})
        digest.update(relative.encode() + b"\0" + body_hash.encode() + b"\n")
        rows += count
    if not files:
        raise RefreshError("dataset root has no partitions")
    return {"files": len(files), "rows": rows, "manifest_sha256": digest.hexdigest(), "entries": entries}


def _file_fingerprint(path: Path) -> dict[str, object]:
    _assert_plain_path(path.parent, path, must_exist=False)
    if not os.path.lexists(path):
        return {"exists": False}
    if not path.is_file():
        raise RefreshError("state fingerprint target is not a plain file")
    body = path.read_bytes()
    return {"exists": True, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}


@contextmanager
def _lock(project_root: Path, run_id: str):
    path = project_root / LOCK
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        raise RefreshError("global current refresh lock is already held") from error
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(run_id)
            stream.flush()
            os.fsync(stream.fileno())
        yield
    finally:
        if path.exists() and path.read_text(encoding="utf-8") == run_id:
            path.unlink()


class BudgetSession:
    """A retry-free hard call budget. Providers make exactly one get per item."""
    def __init__(self, limit: int, backend=None):
        self.limit = limit
        self.backend = backend or requests.Session()
        self.calls = 0
        self.statuses: list[int] = []

    def get(self, *args, **kwargs):
        if self.calls >= self.limit:
            raise RefreshError("phase HTTP-call cap reached")
        self.calls += 1
        response = self.backend.get(*args, **kwargs)
        self.statuses.append(int(response.status_code))
        return response


def _finite_latest(frame: pd.DataFrame, columns: tuple[str, ...]) -> dict[str, str]:
    result = {}
    for column in columns:
        selected = frame.loc[pd.to_numeric(frame[column], errors="coerce").notna(), "date"]
        if selected.empty:
            raise RefreshError(f"{column} has no finite source values")
        result[column] = str(selected.max())
    return result


def _merge(existing: pd.DataFrame, incoming: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    incoming_keys = pd.MultiIndex.from_frame(incoming[keys])
    existing_keys = pd.MultiIndex.from_frame(existing[keys])
    result = pd.concat([existing.loc[~existing_keys.isin(incoming_keys)], incoming], ignore_index=True)
    return result.sort_values(keys, kind="stable").reset_index(drop=True)


def _series_revision(
    existing: pd.DataFrame, incoming: pd.DataFrame, *, item: str, phase: str,
    planned_start: str | None = None, planned_end: str | None = None,
) -> dict[str, object]:
    if phase == "yahoo":
        old = existing.loc[existing.symbol.eq(item)].set_index("date")
        new = incoming.loc[incoming.symbol.eq(item)].set_index("date")
        columns = ["open", "high", "low", "close", "volume", "source_ticker"]
    else:
        column = item.lower()
        old = existing.set_index("date")[[column]]
        new = incoming.set_index("date")[[column]]
        columns = [column]
    overlap = old.index.intersection(new.index)
    revised_finite = finite_to_null = null_to_finite = 0
    for column in columns:
        left, right = old.loc[overlap, column], new.loc[overlap, column]
        finite_to_null += int((left.notna() & right.isna()).sum())
        null_to_finite += int((left.isna() & right.notna()).sum())
        revised_finite += int((left.notna() & right.notna() & ~left.eq(right)).sum())
    lower = pd.Timestamp(planned_start) if planned_start else pd.to_datetime(incoming["date"]).min()
    upper = pd.Timestamp(planned_end) if planned_end else pd.to_datetime(incoming["date"]).max()
    bounded = old.loc[pd.to_datetime(old.index).to_series(index=old.index).between(lower, upper)]
    return {
        "item": item, "response_start": str(incoming.date.min()), "response_end": str(incoming.date.max()),
        "overlap_rows": len(overlap), "inserted_rows": len(new.index.difference(old.index)),
        "revised_finite_cells": revised_finite, "finite_to_null_cells": finite_to_null,
        "null_to_finite_cells": null_to_finite,
        "source_omitted_existing_dates": len(set(bounded.index) - set(new.index)),
    }


def _verify_captures(landing_root: Path, phase: str, plan: list[dict[str, str]]) -> list[dict[str, object]]:
    expected_provider = "yahoo" if phase == "yahoo" else "fred"
    expected_operation = "chart" if phase == "yahoo" else "fredgraph_csv"
    for entry in landing_root.rglob("*"):
        _assert_plain_path(landing_root, entry)
        parts = entry.relative_to(landing_root).parts
        valid = (
            (len(parts) == 1 and entry.is_dir() and parts[0] == expected_provider)
            or (len(parts) == 2 and entry.is_dir() and parts == (expected_provider, expected_operation))
            or (len(parts) == 3 and entry.is_dir() and parts[:2] == (expected_provider, expected_operation))
            or (len(parts) == 4 and entry.is_file() and parts[:2] == (expected_provider, expected_operation)
                and parts[3] in {"call.json", "response.body"})
        )
        if not valid:
            raise RefreshError("Landing root contains unexpected topology")
    records = []
    for path in sorted(landing_root.rglob("call.json")):
        _assert_plain_path(landing_root, path)
        record = json.loads(path.read_text(encoding="utf-8"))
        required = {
            "capture_version", "provider", "operation", "captured_at_utc",
            "request_url", "request_parameters", "http_status",
            "response_content_type", "response_body_sha256", "response_bytes",
            "landing_body_file",
        }
        if set(record) != required or record["capture_version"] != 1 or record["landing_body_file"] != "response.body":
            raise RefreshError("Landing call schema/value differs")
        if set(child.name for child in path.parent.iterdir()) != {"call.json", "response.body"}:
            raise RefreshError("Landing call directory topology differs")
        if path.parent.parent.parent != landing_root / record["provider"]:
            raise RefreshError("Landing provider/operation topology differs")
        if not re.fullmatch(r"\d{8}T\d{6}\.\d{6}Z_[0-9a-f]{32}", path.parent.name):
            raise RefreshError("Landing call-directory identity differs")
        try:
            stamp = datetime.fromisoformat(str(record["captured_at_utc"]).replace("Z", "+00:00")).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        except (ValueError, TypeError) as error:
            raise RefreshError("Landing capture timestamp differs") from error
        if not path.parent.name.startswith(stamp + "_"):
            raise RefreshError("Landing call-directory timestamp differs")
        body = path.with_name(record["landing_body_file"])
        _assert_plain_path(landing_root, body)
        content = body.read_bytes()
        if (hashlib.sha256(content).hexdigest() != record["response_body_sha256"]
                or len(content) != record["response_bytes"]
                or not isinstance(record["response_content_type"], str)):
            raise RefreshError("Landing body hash differs from call record")
        if int(record.get("http_status", 0)) != 200:
            raise RefreshError("Landing call is not HTTP 200")
        parameters = record.get("request_parameters")
        if not isinstance(parameters, dict):
            raise RefreshError("Landing parameters are absent")
        if phase == "yahoo":
            item = parameters.get("symbol")
            item_plan = next((entry for entry in plan if entry["item"] == item), None)
            expected_provider, expected_operation = "yahoo", "chart"
            expected_url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(CONFIG.get(item, ''), safe='')}"
            expected_parameters = {
                "symbol": item,
                "period1": str(_epoch(date.fromisoformat(item_plan["start"]))) if item_plan else "",
                "period2": str(_epoch(date.fromisoformat(item_plan["end"]) + timedelta(days=1))) if item_plan else "",
                "interval": "1d", "events": "history", "includeAdjustedClose": "false",
            }
        else:
            item = parameters.get("id")
            item_plan = next((entry for entry in plan if entry["item"] == item), None)
            expected_provider, expected_operation = "fred", "fredgraph_csv"
            expected_url = "https://fred.stlouisfed.org/graph/fredgraph.csv"
            expected_parameters = {"id": item, "cosd": item_plan["start"] if item_plan else "", "coed": item_plan["end"] if item_plan else ""}
        if (item_plan is None or record.get("provider") != expected_provider
                or record.get("operation") != expected_operation
                or record.get("request_url") != expected_url or parameters != expected_parameters):
            raise RefreshError("Landing record does not bind exactly to frozen plan")
        records.append({"item": item, "path": path.relative_to(landing_root).as_posix(),
                        "call_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "body_sha256": record["response_body_sha256"]})
    if len(records) != len(plan) or {record["item"] for record in records} != {entry["item"] for entry in plan}:
        raise RefreshError("Landing call-record count differs")
    return records


def _build_spread_candidate(yields: pd.DataFrame, root: Path) -> dict[str, object]:
    source = yields.copy()
    source["date"] = pd.to_datetime(source["date"]).dt.date
    result = calculate_treasury_spreads(source)
    validation = validate_treasury_spreads(source, result)
    def validator(frame: pd.DataFrame) -> None:
        dates = set(pd.to_datetime(frame["date"]).dt.date)
        selected = source.loc[source["date"].isin(dates)].reset_index(drop=True)
        restored = frame.copy()
        restored["date"] = pd.to_datetime(restored["date"]).dt.date
        validate_treasury_spreads(selected, restored.reset_index(drop=True))
    write_dataset_atomic(result, root, US_TREASURY_SPREAD_DAILY, validator)
    return {"rows": validation.rows, "coverage_start": validation.coverage_start, "coverage_end": validation.coverage_end}


def _parse_retained_fred_capture(call_path: Path, item: str) -> pd.DataFrame:
    record = json.loads(call_path.read_text(encoding="utf-8"))
    body = call_path.with_name("response.body")
    if hashlib.sha256(body.read_bytes()).hexdigest() != record.get("response_body_sha256"):
        raise RefreshError("retained FRED Landing body hash differs")
    frame = pd.read_csv(StringIO(body.read_bytes().decode("utf-8")))
    if frame.empty or len(frame.columns) != 2 or frame.columns[0] != "DATE" or frame.columns[1].upper() != item:
        raise RefreshError("retained FRED Landing schema/series identity differs")
    frame.columns = ["date", item.lower()]
    frame["date"] = pd.to_datetime(frame["date"], errors="raise").dt.strftime("%Y-%m-%d")
    frame[item.lower()] = pd.to_numeric(frame[item.lower()], errors="coerce")
    finite = frame[item.lower()].dropna()
    if finite.empty or not pd.Series(finite).map(lambda value: pd.notna(value) and abs(float(value)) != float("inf")).all():
        raise RefreshError("retained FRED Landing has no valid finite values")
    if frame.date.duplicated().any() or not frame.date.is_monotonic_increasing:
        raise RefreshError("retained FRED Landing dates differ")
    return frame


def adopt_stopped_fred_yields(
    project_root: Path, checkpoint_path: Path, *, accepted_observed_end: date,
    confirm_requested_end: date,
) -> dict[str, object]:
    """Offline adoption of one already captured stopped yields run; zero HTTP."""
    project_root = project_root.resolve()
    checkpoint_path = _assert_plain_path(project_root, checkpoint_path)
    stopped = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    run_id = stopped.get("run_id")
    if (stopped.get("phase") != "fred_yields" or stopped.get("status") != "STOPPED"
            or stopped.get("http_calls") != 3 or stopped.get("http_statuses") != [200, 200, 200]
            or stopped.get("retry_count") != 0):
        raise RefreshError("stopped run is not an exact healthy three-call yields capture")
    expected_checkpoint = project_root / "data/state/global_current_refresh" / str(run_id) / "checkpoint.json"
    if checkpoint_path != expected_checkpoint.absolute():
        raise RefreshError("stopped checkpoint topology differs")
    plan = stopped.get("frozen_plan")
    if (not isinstance(plan, list) or [entry.get("item") for entry in plan] != ["DGS2", "DGS10", "DGS30"]
            or {entry.get("end") for entry in plan} != {confirm_requested_end.isoformat()}):
        raise RefreshError("requested-end confirmation or frozen yields plan differs")
    landing = _assert_plain_path(project_root, project_root / "data/landing/global_current_refresh" / run_id)
    captures = _verify_captures(landing, "fred_yields", plan)
    call_by_item = {}
    for capture in captures:
        call_by_item[capture["item"]] = landing / capture["path"]
    frames = {item: _parse_retained_fred_capture(call_by_item[item], item) for item in ("DGS2", "DGS10", "DGS30")}
    endpoints = {item: frame.date.max() for item, frame in frames.items()}
    if set(endpoints.values()) != {accepted_observed_end.isoformat()}:
        raise RefreshError("FRED series endpoints are unequal or differ from reviewed observed end")
    for entry in plan:
        frame = frames[entry["item"]]
        if frame.date.min() < entry["start"] or frame.date.max() > entry["end"]:
            raise RefreshError("retained FRED response lies outside frozen requested window")
    production = project_root / "data/normalized" / FRED_TREASURY_YIELD_DAILY.name
    state = project_root / "data/state" / f"{FRED_TREASURY_YIELD_DAILY.name}.json"
    existing = read_dataset(production, FRED_TREASURY_YIELD_DAILY, validate_fred)
    if _files_manifest(production) != stopped["pre_dataset"]:
        raise RefreshError("yield production changed since stopped capture")
    incoming = frames["DGS2"]
    for item in ("DGS10", "DGS30"):
        incoming = incoming.merge(frames[item], on="date", how="outer", validate="one_to_one")
    incoming = incoming.sort_values("date", kind="stable").reset_index(drop=True)
    validate_fred(incoming)
    plan_by_item = {entry["item"]: entry for entry in plan}
    revisions = {item: _series_revision(
        existing, frames[item], item=item, phase="fred_yields",
        planned_start=plan_by_item[item]["start"], planned_end=accepted_observed_end.isoformat(),
    ) for item in frames}
    if any(report["source_omitted_existing_dates"] or report["finite_to_null_cells"] for report in revisions.values()):
        raise RefreshError("retained FRED response omits or nulls retained observations")
    candidate = _merge(existing, incoming, ["date"])
    validate_fred(candidate)
    candidate_parent = project_root / "data/staging/global_current_refresh" / run_id
    candidate_root = candidate_parent / FRED_TREASURY_YIELD_DAILY.name
    if candidate_parent.exists():
        raise RefreshError("adoption candidate path already exists")
    with _lock(project_root, run_id):
        write_dataset_atomic(candidate, candidate_root, FRED_TREASURY_YIELD_DAILY, validate_fred)
        spread_root = candidate_parent / US_TREASURY_SPREAD_DAILY.name
        spread_validation = _build_spread_candidate(candidate, spread_root)
        spread_state = candidate_parent / f"{US_TREASURY_SPREAD_DAILY.name}.state.json"
        _atomic_json(spread_state, {
            "dataset": US_TREASURY_SPREAD_DAILY.name, "status": "artifact_complete_provenance_limited",
            "source_dataset": FRED_TREASURY_YIELD_DAILY.name, "source_manifest": _files_manifest(candidate_root),
            "output_manifest": _files_manifest(spread_root), "validation": spread_validation, "run_id": run_id,
        })
        candidate_manifest = _files_manifest(candidate_root)
        coverage = {item: {"planned_start": plan_by_item[item]["start"],
                           "requested_end": confirm_requested_end.isoformat(),
                           "accepted_observed_end": accepted_observed_end.isoformat(),
                           "observed_start": frames[item].date.min(), "observed_end": frames[item].date.max()}
                    for item in frames}
        state_path = candidate_parent / f"{FRED_TREASURY_YIELD_DAILY.name}.state.json"
        _atomic_json(state_path, {"dataset": FRED_TREASURY_YIELD_DAILY.name,
            "status": "artifact_complete_provenance_limited", "run_id": run_id,
            "adoption": "reviewed_publication_lag", "requested_end": confirm_requested_end.isoformat(),
            "accepted_observed_end": accepted_observed_end.isoformat(), "frozen_plan": plan,
            "landing_captures": captures, "coverage": coverage, "revision_report": revisions,
            "pre_dataset": stopped["pre_dataset"], "candidate_dataset": candidate_manifest})
        adopted = dict(stopped)
        adopted.update({"version": 3, "status": "CANDIDATE_REVIEW_REQUIRED", "error_type": None,
            "adoption": "reviewed_publication_lag", "requested_end": confirm_requested_end.isoformat(),
            "accepted_observed_end": accepted_observed_end.isoformat(), "landing_captures": captures,
            "coverage": coverage, "revision_report": revisions, "candidate_dataset": candidate_manifest,
            "candidate_root": candidate_root.relative_to(project_root).as_posix(),
            "pre_operational_state": _file_fingerprint(state),
            "candidate_operational_state": state_path.relative_to(project_root).as_posix(),
            "candidate_operational_state_fingerprint": _file_fingerprint(state_path),
            "pre_spread": _files_manifest(project_root / "data/derived" / US_TREASURY_SPREAD_DAILY.name),
            "pre_spread_state": _file_fingerprint(project_root / "data/state" / f"{US_TREASURY_SPREAD_DAILY.name}.json"),
            "candidate_spread": spread_validation, "candidate_spread_manifest": _files_manifest(spread_root),
            "candidate_spread_state": spread_state.relative_to(project_root).as_posix(),
            "candidate_spread_state_fingerprint": _file_fingerprint(spread_state)})
        adopted["approval_digest"] = _approval_digest(adopted)
        _atomic_json(checkpoint_path, adopted)
    return adopted


def prepare_phase(project_root: Path, phase: str, *, end: date, session=None) -> dict[str, object]:
    """Make a reviewable Landing/candidate bundle; never mutate production."""
    project_root = project_root.resolve()
    _assert_plain_path(project_root.parent, project_root)
    if phase not in PHASES:
        raise RefreshError("unknown phase")
    limit, contract, items = PHASES[phase]
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex
    state_root = project_root / "data/state/global_current_refresh" / run_id
    landing_root = project_root / "data/landing/global_current_refresh" / run_id
    candidate_root = project_root / "data/staging/global_current_refresh" / run_id / contract.name
    production_root = project_root / "data/normalized" / contract.name
    production_state = project_root / "data/state" / f"{contract.name}.json"
    _assert_plain_path(project_root, production_root)
    _assert_plain_path(project_root, production_state)
    for prospective in (state_root, landing_root, candidate_root.parent):
        _assert_plain_path(project_root, prospective, must_exist=False)
    checkpoint_path = state_root / "checkpoint.json"
    existing = read_dataset(production_root, contract, validate_global_index if phase == "yahoo" else validate_fred)
    pre = _files_manifest(production_root)
    plan = []
    for item in items:
        selected = existing.loc[existing["symbol"].eq(item), "date"] if phase == "yahoo" else existing.loc[pd.to_numeric(existing[item.lower()], errors="coerce").notna(), "date"]
        if selected.empty:
            raise RefreshError(f"cannot derive overlap start for {item}")
        start = date.fromisoformat(str(selected.max())) - timedelta(days=10)
        plan.append({"item": item, "start": start.isoformat(), "end": end.isoformat()})
    checkpoint = {
        "version": 2, "run_id": run_id, "phase": phase, "status": "CREATED",
        "frozen_plan": plan, "max_http_calls": limit, "retry_count": 0,
        "pre_dataset": pre, "pre_operational_state": _file_fingerprint(production_state),
        "normalized_mutation": False,
    }
    _atomic_json(checkpoint_path, checkpoint)
    budget = BudgetSession(limit, session)
    with _lock(project_root, run_id):
        try:
            frames = []
            for item_plan in plan:
                start = date.fromisoformat(item_plan["start"])
                if phase == "yahoo":
                    frames.append(fetch_global_index(item_plan["item"], start, end, session=budget, capture_root=landing_root))
                else:
                    frames.append(fetch_series(item_plan["item"], start, end=end, session=budget, capture_root=landing_root))
            if budget.calls != limit or budget.statuses != [200] * limit:
                raise RefreshError("phase call/status accounting differs")
            captures = _verify_captures(landing_root, phase, plan)
            frame_by_item = dict(zip(items, frames, strict=True))
            coverage = {}
            for item_plan in plan:
                item = item_plan["item"]
                frame = frame_by_item[item]
                observed_start = date.fromisoformat(str(frame.date.min()))
                observed_end = date.fromisoformat(str(frame.date.max()))
                planned_start = date.fromisoformat(item_plan["start"])
                if observed_start < planned_start or observed_end != end:
                    raise RefreshError(f"{item} response does not cover the strict planned endpoint window")
                retained_latest = existing.loc[existing.symbol.eq(item), "date"].max() if phase == "yahoo" else existing.loc[pd.to_numeric(existing[item.lower()], errors="coerce").notna(), "date"].max()
                if observed_start > date.fromisoformat(str(retained_latest)):
                    raise RefreshError(f"{item} response does not overlap retained coverage")
                coverage[item] = {"planned_start": item_plan["start"], "planned_end": item_plan["end"],
                                  "observed_start": observed_start.isoformat(), "observed_end": observed_end.isoformat()}
            if phase == "yahoo":
                incoming = pd.concat(frames, ignore_index=True).sort_values(["date", "symbol"], kind="stable").reset_index(drop=True)
                validate_global_index(incoming)
                if set(incoming.symbol) != set(CONFIG) or incoming.groupby("symbol")["date"].max().ne(end.isoformat()).any():
                    raise RefreshError("Yahoo did not reach the explicit completed session for every symbol")
                keys = ["date", "symbol"]
                validator = validate_global_index
            else:
                incoming = frames[0]
                for frame in frames[1:]:
                    incoming = incoming.merge(frame, on="date", how="outer", validate="one_to_one")
                incoming = incoming.sort_values("date", kind="stable").reset_index(drop=True)
                if pd.to_datetime(incoming.date).max().date() > end:
                    raise RefreshError("FRED response exceeded explicit end")
                validate_fred(incoming)
                old_latest = _finite_latest(existing, tuple(item.lower() for item in items))
                new_latest = _finite_latest(incoming, tuple(item.lower() for item in items))
                if any(new_latest[name] < old_latest[name] for name in old_latest):
                    raise RefreshError("FRED finite coverage regressed")
                keys = ["date"]
                validator = validate_fred
            plan_by_item = {entry["item"]: entry for entry in plan}
            revision = {
                item: _series_revision(
                    existing, frame_by_item[item], item=item, phase=phase,
                    planned_start=plan_by_item[item]["start"], planned_end=plan_by_item[item]["end"],
                ) for item in items
            }
            if any(report["source_omitted_existing_dates"] or report["finite_to_null_cells"] for report in revision.values()):
                raise RefreshError("source omitted retained dates or changed finite values to null")
            candidate = _merge(existing, incoming, keys)
            validator(candidate)
            write_dataset_atomic(candidate, candidate_root, contract, validator)
            if _files_manifest(production_root) != pre:
                raise RefreshError("production root changed during capture preparation")
            extra = {}
            if phase == "fred_yields":
                spread_root = candidate_root.parent / US_TREASURY_SPREAD_DAILY.name
                spread_state = candidate_root.parent / f"{US_TREASURY_SPREAD_DAILY.name}.state.json"
                spread_validation = _build_spread_candidate(candidate, spread_root)
                _atomic_json(spread_state, {
                    "dataset": US_TREASURY_SPREAD_DAILY.name,
                    "status": "artifact_complete_provenance_limited",
                    "source_dataset": contract.name,
                    "source_manifest": _files_manifest(candidate_root),
                    "output_manifest": _files_manifest(spread_root),
                    "validation": spread_validation,
                    "run_id": run_id,
                })
                extra = {
                    "pre_spread": _files_manifest(project_root / "data/derived" / US_TREASURY_SPREAD_DAILY.name),
                    "pre_spread_state": _file_fingerprint(project_root / "data/state" / f"{US_TREASURY_SPREAD_DAILY.name}.json"),
                    "candidate_spread": spread_validation,
                    "candidate_spread_manifest": _files_manifest(spread_root),
                    "candidate_spread_state": spread_state.relative_to(project_root).as_posix(),
                    "candidate_spread_state_fingerprint": _file_fingerprint(spread_state),
                }
            candidate_manifest = _files_manifest(candidate_root)
            operational_state = candidate_root.parent / f"{contract.name}.state.json"
            state_payload = {
                "dataset": contract.name, "status": "artifact_complete_provenance_limited", "run_id": run_id,
                "phase": phase, "frozen_plan": plan, "landing_captures": captures,
                "coverage": coverage, "revision_report": revision,
                "pre_dataset": pre, "candidate_dataset": candidate_manifest,
            }
            _atomic_json(operational_state, state_payload)
            checkpoint.update({
                "status": "CANDIDATE_REVIEW_REQUIRED", "http_calls": budget.calls,
                "http_statuses": budget.statuses, "landing_captures": captures,
                "coverage": coverage, "revision_report": revision, "candidate_dataset": candidate_manifest,
                "candidate_root": candidate_root.relative_to(project_root).as_posix(),
                "candidate_operational_state": operational_state.relative_to(project_root).as_posix(),
                "candidate_operational_state_fingerprint": _file_fingerprint(operational_state), **extra,
            })
            checkpoint["approval_digest"] = _approval_digest(checkpoint)
            _atomic_json(checkpoint_path, checkpoint)
            return checkpoint
        except Exception as error:
            checkpoint.update({"status": "STOPPED", "http_calls": budget.calls,
                               "http_statuses": budget.statuses, "error_type": type(error).__name__,
                               "post_dataset": _files_manifest(production_root)})
            _atomic_json(checkpoint_path, checkpoint)
            raise


def _remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _artifact_fingerprint(path: Path) -> dict[str, object]:
    if path.is_dir():
        digest = hashlib.sha256()
        files = []
        for child in sorted(path.rglob("*")):
            _assert_plain_path(path, child)
            if child.is_file():
                body = child.read_bytes()
                relative = child.relative_to(path).as_posix()
                value = hashlib.sha256(body).hexdigest()
                files.append({"path": relative, "bytes": len(body), "sha256": value})
                digest.update(relative.encode() + b"\0" + value.encode() + b"\n")
        return {"kind": "directory", "value": {"files": files, "manifest_sha256": digest.hexdigest()}}
    return {"kind": "file", "value": _file_fingerprint(path)}


def _copy_artifact(source: Path, target: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, target)
    else:
        shutil.copy2(source, target)


def _recover_transaction(
    journal_path: Path, *, committed: bool,
    allowed_pairs: list[tuple[Path, Path]], project_root: Path,
) -> None:
    if not journal_path.is_file():
        return
    _assert_plain_path(project_root, journal_path)
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    entries = journal.get("replacements", [])
    observed_pairs = [(Path(entry.get("source", "")), Path(entry.get("target", ""))) for entry in entries]
    if observed_pairs != allowed_pairs:
        raise RefreshError("transaction journal ordered source/target identity differs")
    token = journal_path.parent.name
    for number, entry in enumerate(entries):
        target = _assert_plain_path(project_root, Path(entry["target"]), must_exist=False)
        source = _assert_plain_path(project_root, Path(entry["source"]), must_exist=False)
        stage = _assert_plain_path(project_root, Path(entry["stage"]), must_exist=False)
        backup = _assert_plain_path(project_root, Path(entry["backup"]), must_exist=False)
        if (Path(entry["stage"]) != target.parent / f".{target.name}.refresh-{token}-{number}.stage"
                or Path(entry["backup"]) != target.parent / f".{target.name}.refresh-{token}-{number}.backup"):
            raise RefreshError("transaction journal scratch topology differs")
        if committed:
            available = next((path for path in (target, stage, source) if path.exists() and _artifact_fingerprint(path) == entry["source_fingerprint"]), None)
            if available is None:
                raise RefreshError("committed transaction has no verified canonical source copy")
        elif entry["original_exists"]:
            original_available = (
                backup.exists() and _artifact_fingerprint(backup) == entry["pre_target_fingerprint"]
            ) or (
                target.exists() and _artifact_fingerprint(target) == entry["pre_target_fingerprint"]
            )
            if not original_available:
                raise RefreshError("uncommitted transaction has no verified original copy")
    if committed:
        for entry in entries:
            target, source, stage = Path(entry["target"]), Path(entry["source"]), Path(entry["stage"])
            if not target.exists() or _artifact_fingerprint(target) != entry["source_fingerprint"]:
                verified = next(path for path in (stage, source) if path.exists() and _artifact_fingerprint(path) == entry["source_fingerprint"])
                if target.exists():
                    _remove_path(target)
                _copy_artifact(verified, target)
            if _artifact_fingerprint(target) != entry["source_fingerprint"]:
                raise RefreshError("committed recovery canonical verification failed")
    else:
        for entry in reversed(entries):
            target, backup = Path(entry["target"]), Path(entry["backup"])
            if backup.exists() and _artifact_fingerprint(backup) == entry["pre_target_fingerprint"]:
                if target.exists():
                    _remove_path(target)
                backup.replace(target)
            elif entry["original_exists"] and target.exists() and _artifact_fingerprint(target) == entry["pre_target_fingerprint"]:
                pass
            elif not entry["original_exists"] and target.exists():
                _remove_path(target)
    for entry in entries:
        for name in ("stage", "backup"):
            path = Path(entry[name])
            if path.exists():
                _remove_path(path)
    journal["status"] = "COMMITTED_RECOVERED" if committed else "ROLLED_BACK_RECOVERED"
    _atomic_json(journal_path, journal)


def _replace_roots_atomically(
    replacements: list[tuple[Path, Path]], finalize=None, *, journal_path: Path | None = None,
) -> None:
    """Install whole-root copies with rollback and optional crash journal."""
    stages, backups, installed = [], [], []
    cleanup_backups = False
    try:
        journal_entries = []
        token = journal_path.parent.name if journal_path is not None else uuid4().hex
        for number, (source, target) in enumerate(replacements):
            stage = target.parent / f".{target.name}.refresh-{token}-{number}.stage"
            backup = target.parent / f".{target.name}.refresh-{token}-{number}.backup"
            if stage.exists() or backup.exists():
                raise RefreshError("transaction scratch path already exists; recover first")
            target.parent.mkdir(parents=True, exist_ok=True)
            journal_entries.append({"source": str(source), "target": str(target), "stage": str(stage),
                                    "backup": str(backup), "original_exists": target.exists(),
                                    "source_fingerprint": _artifact_fingerprint(source),
                                    "pre_target_fingerprint": _artifact_fingerprint(target) if target.exists() else {"exists": False}})
            stages.append((stage, target))
        if journal_path is not None:
            _atomic_json(journal_path, {"version": 1, "status": "PREPARING", "replacements": journal_entries})
        for number, (source, target) in enumerate(replacements):
            stage = stages[number][0]
            if source.is_dir():
                shutil.copytree(source, stage)
            else:
                shutil.copy2(source, stage)
        if journal_path is not None:
            _atomic_json(journal_path, {"version": 1, "status": "PREPARED", "replacements": journal_entries})
        for number, (stage, target) in enumerate(stages):
            backup = Path(journal_entries[number]["backup"])
            if target.exists():
                target.replace(backup)
            else:
                backup = None
            backups.append((backup, target))
            stage.replace(target)
            installed.append(target)
        if finalize is not None:
            finalize()
        if journal_path is not None:
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
            journal["status"] = "COMMITTED"
            _atomic_json(journal_path, journal)
        cleanup_backups = True
    except BaseException:
        for target in reversed(installed):
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            else:
                target.unlink(missing_ok=True)
        for backup, target in reversed(backups):
            if backup is not None and backup.exists():
                backup.replace(target)
        cleanup_backups = True
        raise
    finally:
        for stage, _ in stages:
            if stage.is_dir():
                shutil.rmtree(stage, ignore_errors=True)
            else:
                stage.unlink(missing_ok=True)
        for backup, _ in backups:
            if cleanup_backups and backup is not None:
                if backup.is_dir():
                    shutil.rmtree(backup, ignore_errors=True)
                else:
                    backup.unlink(missing_ok=True)


def _approval_digest(checkpoint: dict[str, object]) -> str:
    keys = [
        "run_id", "phase", "frozen_plan", "max_http_calls", "retry_count",
        "http_calls", "http_statuses", "landing_captures", "coverage",
        "revision_report", "pre_dataset", "candidate_dataset",
        "pre_operational_state", "candidate_root", "candidate_operational_state",
        "candidate_operational_state_fingerprint",
    ]
    keys.extend(key for key in (
        "pre_spread", "pre_spread_state", "candidate_spread",
        "candidate_spread_manifest", "candidate_spread_state",
        "candidate_spread_state_fingerprint",
    ) if key in checkpoint)
    payload = {key: checkpoint[key] for key in keys}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def promote_phase(project_root: Path, checkpoint_path: Path, *, approval_digest: str) -> dict[str, object]:
    """Zero-network CAS promotion; the global lock covers recovery and preflight."""
    project_root = project_root.resolve()
    _assert_plain_path(project_root.parent, project_root)
    checkpoint_path = _assert_plain_path(project_root, checkpoint_path)
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    run_id = checkpoint.get("run_id")
    if not isinstance(run_id, str) or not re.fullmatch(r"\d{8}T\d{6}Z_[0-9a-f]{32}", run_id):
        raise RefreshError("invalid run identity")
    expected = project_root / "data/state/global_current_refresh" / run_id / "checkpoint.json"
    if checkpoint_path != expected.absolute():
        raise RefreshError("checkpoint path does not match its run identity")
    with _lock(project_root, run_id):
        return _promote_locked(project_root, checkpoint_path, approval_digest)


def _promote_locked(project_root: Path, checkpoint_path: Path, approval_digest: str) -> dict[str, object]:
    checkpoint_path = _assert_plain_path(project_root, checkpoint_path)
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    run_id, phase = checkpoint.get("run_id"), checkpoint.get("phase")
    if phase not in PHASES:
        raise RefreshError("unknown checkpoint phase")
    limit, contract, items = PHASES[phase]
    if (checkpoint.get("max_http_calls") != limit or checkpoint.get("http_calls") != limit
            or checkpoint.get("retry_count") != 0 or checkpoint.get("http_statuses") != [200] * limit
            or len(checkpoint.get("landing_captures", [])) != limit
            or [entry.get("item") for entry in checkpoint.get("frozen_plan", [])] != list(items)
            or checkpoint.get("approval_digest") != approval_digest
            or _approval_digest(checkpoint) != approval_digest):
        raise RefreshError("checkpoint approval/call/plan accounting differs")
    production = project_root / "data/normalized" / contract.name
    state = project_root / "data/state" / f"{contract.name}.json"
    candidate_parent = project_root / "data/staging/global_current_refresh" / run_id
    candidate = candidate_parent / contract.name
    candidate_state = candidate_parent / f"{contract.name}.state.json"
    if (checkpoint.get("candidate_root") != candidate.relative_to(project_root).as_posix()
            or checkpoint.get("candidate_operational_state") != candidate_state.relative_to(project_root).as_posix()):
        raise RefreshError("checkpoint candidate path topology differs")
    replacements = [(candidate, production), (candidate_state, state)]
    spread = spread_state = candidate_spread = candidate_spread_state = None
    if phase == "fred_yields":
        spread = project_root / "data/derived" / US_TREASURY_SPREAD_DAILY.name
        spread_state = project_root / "data/state" / f"{US_TREASURY_SPREAD_DAILY.name}.json"
        candidate_spread = candidate_parent / US_TREASURY_SPREAD_DAILY.name
        candidate_spread_state = candidate_parent / f"{US_TREASURY_SPREAD_DAILY.name}.state.json"
        if checkpoint.get("candidate_spread_state") != candidate_spread_state.relative_to(project_root).as_posix():
            raise RefreshError("checkpoint spread-state topology differs")
        replacements += [(candidate_spread, spread), (candidate_spread_state, spread_state)]
    for source, target in replacements:
        _assert_plain_path(project_root, source, must_exist=False)
        _assert_plain_path(project_root, target, must_exist=False)
    journal_path = checkpoint_path.with_name("promotion_transaction.json")
    _assert_plain_path(project_root, journal_path, must_exist=False)
    _recover_transaction(
        journal_path, committed=checkpoint.get("status") == "PROMOTED",
        allowed_pairs=replacements, project_root=project_root,
    )
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    if checkpoint.get("status") == "PROMOTED":
        return checkpoint
    if checkpoint.get("status") != "CANDIDATE_REVIEW_REQUIRED":
        raise RefreshError("checkpoint is not review-ready")
    landing = _assert_plain_path(project_root, project_root / "data/landing/global_current_refresh" / run_id)
    for source, target in replacements:
        _assert_plain_path(project_root, source)
        _assert_plain_path(project_root, target)
    if (_verify_captures(landing, phase, checkpoint["frozen_plan"]) != checkpoint["landing_captures"]
            or _files_manifest(production) != checkpoint["pre_dataset"]
            or _files_manifest(candidate) != checkpoint["candidate_dataset"]
            or _file_fingerprint(state) != checkpoint["pre_operational_state"]
            or _file_fingerprint(candidate_state) != checkpoint["candidate_operational_state_fingerprint"]):
        raise RefreshError("locked CAS/input validation differs")
    if phase == "fred_yields" and (
            _files_manifest(spread) != checkpoint["pre_spread"]
            or _files_manifest(candidate_spread) != checkpoint["candidate_spread_manifest"]
            or _file_fingerprint(spread_state) != checkpoint["pre_spread_state"]
            or _file_fingerprint(candidate_spread_state) != checkpoint["candidate_spread_state_fingerprint"]):
        raise RefreshError("locked Treasury spread CAS/input differs")
    promoted = dict(checkpoint)
    promoted.update({"status": "PROMOTED", "normalized_mutation": True,
                     "post_dataset": checkpoint["candidate_dataset"],
                     "promoted_at_utc": datetime.now(timezone.utc).isoformat()})
    _replace_roots_atomically(
        replacements, finalize=lambda: _atomic_json(checkpoint_path, promoted),
        journal_path=journal_path,
    )
    return promoted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--phase", choices=tuple(PHASES))
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument("--confirm-live-landing-only", action="store_true")
    parser.add_argument("--promote-checkpoint", type=Path)
    parser.add_argument("--confirm-offline-promotion", action="store_true")
    parser.add_argument("--approval-digest")
    parser.add_argument("--adopt-stopped-fred-yields", type=Path)
    parser.add_argument("--accepted-observed-end", type=date.fromisoformat)
    parser.add_argument("--confirm-requested-end", type=date.fromisoformat)
    args = parser.parse_args(argv)
    root = args.project_root.resolve()
    if args.adopt_stopped_fred_yields:
        if not args.accepted_observed_end or not args.confirm_requested_end:
            raise SystemExit("adoption requires accepted observed end and requested-end confirmation")
        result = adopt_stopped_fred_yields(
            root, args.adopt_stopped_fred_yields.resolve(),
            accepted_observed_end=args.accepted_observed_end,
            confirm_requested_end=args.confirm_requested_end,
        )
    elif args.promote_checkpoint:
        if not args.confirm_offline_promotion:
            raise SystemExit("explicit offline-promotion confirmation is required")
        if not args.approval_digest:
            raise SystemExit("exact approval digest is required")
        result = promote_phase(root, args.promote_checkpoint.resolve(), approval_digest=args.approval_digest)
    else:
        if not args.phase or not args.end or not args.confirm_live_landing_only:
            raise SystemExit("phase, explicit end, and Landing-only live confirmation are required")
        result = prepare_phase(root, args.phase, end=args.end)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
