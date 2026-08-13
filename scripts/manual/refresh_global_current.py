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
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from uuid import uuid4

import pandas as pd
import requests

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
from stock_data.providers.yahoo import CONFIG, fetch_global_index  # noqa: E402
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic  # noqa: E402
from stock_data.validation.global_market import validate_fred, validate_global_index  # noqa: E402


PHASES = {
    "yahoo": (3, GLOBAL_INDEX_PRICE_DAILY, tuple(CONFIG)),
    "fred_yields": (3, FRED_TREASURY_YIELD_DAILY, ("DGS2", "DGS10", "DGS30")),
    "fred_fx": (2, FRED_USD_FX_DAILY, ("DEXKOUS", "DEXJPUS")),
}
LOCK = Path("data/state/global_current_refresh.lock")


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


def _files_manifest(root: Path) -> dict[str, object]:
    files = sorted(root.rglob("data.parquet")) if root.exists() else []
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
    return {"files": len(files), "rows": rows, "manifest_sha256": digest.hexdigest(), "entries": entries}


def _file_fingerprint(path: Path) -> dict[str, object]:
    if not path.is_file():
        return {"exists": False}
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


def _revision_report(existing: pd.DataFrame, incoming: pd.DataFrame, keys: list[str]) -> dict[str, object]:
    old = existing.set_index(keys)
    new = incoming.set_index(keys)
    overlap = old.index.intersection(new.index)
    columns = [column for column in incoming.columns if column not in keys]
    revised = 0
    for column in columns:
        left, right = old.loc[overlap, column], new.loc[overlap, column]
        revised += int((~(left.eq(right) | (left.isna() & right.isna()))).sum())
    if incoming.empty:
        omitted = len(old)
    else:
        dates = pd.to_datetime(incoming["date"])
        bounded_old = old.reset_index()
        bounded_old = bounded_old.loc[pd.to_datetime(bounded_old["date"]).between(dates.min(), dates.max())]
        retained_keys = set(map(tuple, bounded_old[keys].to_numpy()))
        returned_keys = set(map(tuple, incoming[keys].to_numpy()))
        omitted = len(retained_keys - returned_keys)
    return {
        "overlap_rows": len(overlap), "revised_cells": revised,
        "inserted_rows": len(new.index.difference(old.index)),
        "source_omitted_existing_keys_within_response_range": omitted,
    }


def _verify_captures(landing_root: Path, expected: int) -> list[dict[str, object]]:
    records = []
    for path in sorted(landing_root.rglob("call.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        body = path.with_name(record["landing_body_file"])
        if hashlib.sha256(body.read_bytes()).hexdigest() != record["response_body_sha256"]:
            raise RefreshError("Landing body hash differs from call record")
        records.append({"path": path.relative_to(landing_root).as_posix(), "body_sha256": record["response_body_sha256"]})
    if len(records) != expected:
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


def prepare_phase(project_root: Path, phase: str, *, end: date, session=None) -> dict[str, object]:
    """Make a reviewable Landing/candidate bundle; never mutate production."""
    if phase not in PHASES:
        raise RefreshError("unknown phase")
    limit, contract, items = PHASES[phase]
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex
    state_root = project_root / "data/state/global_current_refresh" / run_id
    landing_root = project_root / "data/landing/global_current_refresh" / run_id
    candidate_root = project_root / "data/staging/global_current_refresh" / run_id / contract.name
    production_root = project_root / "data/normalized" / contract.name
    checkpoint_path = state_root / "checkpoint.json"
    existing = read_dataset(production_root, contract, validate_global_index if phase == "yahoo" else validate_fred)
    pre = _files_manifest(production_root)
    plan = []
    for item in items:
        selected = existing.loc[existing["symbol"].eq(item), "date"] if phase == "yahoo" else existing.loc[pd.to_numeric(existing[item.lower()], errors="coerce").notna(), "date"]
        if selected.empty:
            raise RefreshError(f"cannot derive overlap start for {item}")
        start = (pd.Timestamp(selected.max()) - pd.Timedelta(days=10)).date()
        plan.append({"item": item, "start": start.isoformat(), "end": end.isoformat()})
    checkpoint = {
        "version": 2, "run_id": run_id, "phase": phase, "status": "CREATED",
        "frozen_plan": plan, "max_http_calls": limit, "retry_count": 0,
        "pre_dataset": pre, "normalized_mutation": False,
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
            captures = _verify_captures(landing_root, limit)
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
            revision = _revision_report(existing, incoming, keys)
            if revision["source_omitted_existing_keys_within_response_range"]:
                raise RefreshError("source omitted retained keys inside its returned date range")
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
            checkpoint.update({
                "status": "CANDIDATE_REVIEW_REQUIRED", "http_calls": budget.calls,
                "http_statuses": budget.statuses, "landing_captures": captures,
                "revision_report": revision, "candidate_dataset": _files_manifest(candidate_root),
                "candidate_root": candidate_root.relative_to(project_root).as_posix(), **extra,
            })
            _atomic_json(checkpoint_path, checkpoint)
            return checkpoint
        except Exception as error:
            checkpoint.update({"status": "STOPPED", "http_calls": budget.calls,
                               "http_statuses": budget.statuses, "error_type": type(error).__name__,
                               "post_dataset": _files_manifest(production_root)})
            _atomic_json(checkpoint_path, checkpoint)
            raise


def _replace_roots_atomically(replacements: list[tuple[Path, Path]], finalize=None) -> None:
    """Install whole-root copies with rollback across every root."""
    stages, backups, installed = [], [], []
    try:
        for source, target in replacements:
            stage = target.parent / f".{target.name}.promote-{uuid4().hex}"
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_dir():
                shutil.copytree(source, stage)
            else:
                shutil.copy2(source, stage)
            stages.append((stage, target))
        for stage, target in stages:
            backup = target.parent / f".{target.name}.backup-{uuid4().hex}"
            if target.exists():
                target.replace(backup)
            else:
                backup = None
            backups.append((backup, target))
            stage.replace(target)
            installed.append(target)
        if finalize is not None:
            finalize()
    except Exception:
        for target in reversed(installed):
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            else:
                target.unlink(missing_ok=True)
        for backup, target in reversed(backups):
            if backup is not None and backup.exists():
                backup.replace(target)
        raise
    finally:
        for stage, _ in stages:
            if stage.is_dir():
                shutil.rmtree(stage, ignore_errors=True)
            else:
                stage.unlink(missing_ok=True)
        for backup, _ in backups:
            if backup is not None:
                if backup.is_dir():
                    shutil.rmtree(backup, ignore_errors=True)
                else:
                    backup.unlink(missing_ok=True)


def promote_phase(project_root: Path, checkpoint_path: Path) -> dict[str, object]:
    """Zero-network CAS promotion of a reviewed whole candidate root."""
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    if checkpoint.get("status") != "CANDIDATE_REVIEW_REQUIRED":
        raise RefreshError("checkpoint is not review-ready")
    phase = checkpoint["phase"]
    _, contract, _ = PHASES[phase]
    production = project_root / "data/normalized" / contract.name
    candidate = project_root / checkpoint["candidate_root"]
    if _files_manifest(production) != checkpoint["pre_dataset"]:
        raise RefreshError("production CAS mismatch")
    if _files_manifest(candidate) != checkpoint["candidate_dataset"]:
        raise RefreshError("candidate manifest mismatch")
    replacements = [(candidate, production)]
    if phase == "fred_yields":
        spread = project_root / "data/derived" / US_TREASURY_SPREAD_DAILY.name
        candidate_spread = candidate.parent / US_TREASURY_SPREAD_DAILY.name
        spread_state = project_root / "data/state" / f"{US_TREASURY_SPREAD_DAILY.name}.json"
        candidate_spread_state = project_root / checkpoint["candidate_spread_state"]
        if (_files_manifest(spread) != checkpoint["pre_spread"]
                or _files_manifest(candidate_spread) != checkpoint["candidate_spread_manifest"]
                or _file_fingerprint(spread_state) != checkpoint["pre_spread_state"]
                or _file_fingerprint(candidate_spread_state) != checkpoint["candidate_spread_state_fingerprint"]):
            raise RefreshError("Treasury spread CAS/candidate mismatch")
        replacements.append((candidate_spread, spread))
        replacements.append((candidate_spread_state, spread_state))
    with _lock(project_root, checkpoint["run_id"]):
        if _files_manifest(production) != checkpoint["pre_dataset"]:
            raise RefreshError("production CAS mismatch after lock acquisition")
        promoted = dict(checkpoint)
        promoted.update({"status": "PROMOTED", "normalized_mutation": True,
                         "post_dataset": checkpoint["candidate_dataset"],
                         "promoted_at_utc": datetime.now(timezone.utc).isoformat()})
        _replace_roots_atomically(
            replacements, finalize=lambda: _atomic_json(checkpoint_path, promoted)
        )
        checkpoint = promoted
    return checkpoint


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--phase", choices=tuple(PHASES))
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument("--confirm-live-landing-only", action="store_true")
    parser.add_argument("--promote-checkpoint", type=Path)
    parser.add_argument("--confirm-offline-promotion", action="store_true")
    args = parser.parse_args(argv)
    root = args.project_root.resolve()
    if args.promote_checkpoint:
        if not args.confirm_offline_promotion:
            raise SystemExit("explicit offline-promotion confirmation is required")
        result = promote_phase(root, args.promote_checkpoint.resolve())
    else:
        if not args.phase or not args.end or not args.confirm_live_landing_only:
            raise SystemExit("phase, explicit end, and Landing-only live confirmation are required")
        result = prepare_phase(root, args.phase, end=args.end)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
