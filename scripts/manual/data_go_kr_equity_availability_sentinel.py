"""Non-mutating two-stream DATA.GO equity availability sentinel.

Live mode performs at most one price/cap call and one universe call, serially,
with no retry. It never writes production checkpoints or Normalized data.
An independently invoked offline adoption step may stage only a fully audited,
non-empty pair for the existing production batch collector, avoiding duplicate
network calls; promotion remains a separate production action.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stock_data.pipelines.backfill_state import BackfillState
from stock_data.providers.data_go_kr.client import (
    DataGoKrClient, service_key_from_environment, write_landing_pages_atomic,
)
from stock_data.providers.data_go_kr.stock_price import (
    STOCK_PRICE_ENDPOINT, normalize_stock_price_items,
)
from stock_data.providers.data_go_kr.universe import (
    UNIVERSE_ENDPOINT, normalize_universe_items,
)


LANDING_RELATIVE = Path("data/landing/diagnostics/data_go_kr_equity_availability")
LOCK_RELATIVE = Path("data/state/data_go_kr_provider.lock")
NUM_ROWS = 9999
STREAMS = ("price_cap", "universe")


class SentinelError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_json(path: Path, value: object, *, exclusive: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if exclusive and path.exists():
        raise SentinelError(f"immutable file already exists: {path.name}")
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if exclusive and path.exists():
            raise SentinelError(f"immutable file already exists: {path.name}")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@contextmanager
def _lock(project_root: Path, run_id: str):
    path = project_root / LOCK_RELATIVE
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        raise SentinelError("DATA.GO equity sentinel/provider lock is already held") from error
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(run_id)
            stream.flush()
            os.fsync(stream.fileno())
        yield
    finally:
        if path.exists() and path.read_text(encoding="utf-8") == run_id:
            path.unlink()


def _validate_date(value: str) -> str:
    if re.fullmatch(r"\d{8}", value) is None:
        raise SentinelError("date must be YYYYMMDD")
    datetime.strptime(value, "%Y%m%d")
    return value


def _classify(stream: str, items, total_count: int, base_date: str) -> dict[str, object]:
    if total_count == 0:
        if items:
            raise SentinelError("zero totalCount returned non-empty items")
        return {"classification": "VALID_EMPTY_NOT_YET_AVAILABLE", "source_rows": 0}
    if len(items) != total_count or total_count > NUM_ROWS:
        raise SentinelError("single-page row/total gate failed")
    expected = datetime.strptime(base_date, "%Y%m%d").strftime("%Y-%m-%d")
    if stream == "price_cap":
        normalized = normalize_stock_price_items(items)
        frames = (normalized.price, normalized.market_cap)
        if any(frame.empty or set(frame["date"]) != {expected} for frame in frames):
            raise SentinelError("price/cap normalized date or non-empty gate failed")
        if len(normalized.price) != len(normalized.market_cap):
            raise SentinelError("price/cap fanout row counts differ")
        return {
            "classification": "NONEMPTY_AVAILABLE", "source_rows": total_count,
            "price_rows": len(normalized.price), "market_cap_rows": len(normalized.market_cap),
        }
    frame = normalize_universe_items(items)
    if frame.empty or set(frame["date"]) != {expected}:
        raise SentinelError("universe normalized date or non-empty gate failed")
    return {"classification": "NONEMPTY_AVAILABLE", "source_rows": total_count, "universe_rows": len(frame)}


def run_sentinel(project_root: Path, base_date: str, *, session=None) -> dict[str, object]:
    base_date = _validate_date(base_date)
    key = service_key_from_environment(project_root)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex
    run_root = project_root / LANDING_RELATIVE / run_id
    run_root.mkdir(parents=True, exist_ok=False)
    ledger_path = run_root / "call_ledger.jsonl"
    results: dict[str, object] = {}
    endpoints = {"price_cap": STOCK_PRICE_ENDPOINT, "universe": UNIVERSE_ENDPOINT}
    with _lock(project_root, run_id):
        for sequence, stream_name in enumerate(STREAMS, start=1):
            client = DataGoKrClient(
                endpoint=endpoints[stream_name], service_key=key,
                session=session or __import__("requests"), max_attempts=1,
            )
            try:
                result = client.fetch_all(
                    filters={"basDt": base_date}, num_of_rows=NUM_ROWS, max_pages=1,
                )
                assessment = _classify(stream_name, result.items, result.total_count, base_date)
                landing = run_root / f"response_{sequence:02d}_{stream_name}.json"
                write_landing_pages_atomic(result.pages, landing)
                record = {
                    "sequence": sequence, "stream": stream_name, "event": "CALL_COMPLETED",
                    "captured_at_utc": _now(), "endpoint": endpoints[stream_name],
                    "public_parameters": {"basDt": base_date, "numOfRows": NUM_ROWS, "pageNo": 1, "resultType": "json"},
                    "retry_count": 0, "pages": len(result.pages),
                    "landing_file": landing.name, "landing_sha256": _sha(landing),
                    **assessment,
                }
                results[stream_name] = record
            except Exception as error:
                safe = str(error).replace(key, "<redacted>")
                with ledger_path.open("a", encoding="utf-8", newline="\n") as stream:
                    stream.write(json.dumps({
                        "sequence": sequence, "stream": stream_name, "event": "ANOMALY",
                        "captured_at_utc": _now(), "error_type": type(error).__name__,
                        "error": safe[:240], "retry_count": 0,
                    }, ensure_ascii=False, sort_keys=True) + "\n")
                _atomic_json(run_root / "manifest.json", {
                    "version": 1, "status": "ANOMALY", "run_id": run_id,
                    "base_date": base_date, "raw_requests": sequence,
                    "retry_count": 0, "parallelism": 1,
                    "production_checkpoint_writes": False,
                    "normalized_writes": False, "results": results,
                    "failed_stream": stream_name, "error_type": type(error).__name__,
                    "call_ledger_sha256": _sha(ledger_path),
                    "adoption_eligible": False,
                }, exclusive=True)
                raise SentinelError(f"{stream_name} anomaly: {safe}") from error
            with ledger_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
                stream.flush()
                os.fsync(stream.fileno())
        overall = "NONEMPTY_AVAILABLE" if all(
            results[name]["classification"] == "NONEMPTY_AVAILABLE" for name in STREAMS
        ) else "VALID_EMPTY_NOT_YET_AVAILABLE"
        manifest = {
            "version": 1, "status": overall, "run_id": run_id, "base_date": base_date,
            "raw_requests": 2, "retry_count": 0, "parallelism": 1,
            "production_checkpoint_writes": False, "normalized_writes": False,
            "results": results, "call_ledger_sha256": _sha(ledger_path),
            "adoption_eligible": overall == "NONEMPTY_AVAILABLE",
        }
        _atomic_json(run_root / "manifest.json", manifest, exclusive=True)
        return {"run_root": str(run_root), "manifest_sha256": _sha(run_root / "manifest.json"), **manifest}


def _read_audited_pair(project_root: Path, run_root: Path) -> tuple[dict[str, object], dict[str, Path]]:
    expected_parent = (project_root / LANDING_RELATIVE).resolve()
    run_root = run_root.resolve()
    if run_root.parent != expected_parent:
        raise SentinelError("adoption run must be an immediate sentinel child")
    manifest_path = run_root / "manifest.json"
    ledger_path = run_root / "call_ledger.jsonl"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if (
        manifest.get("status") != "NONEMPTY_AVAILABLE"
        or manifest.get("adoption_eligible") is not True
        or manifest.get("raw_requests") != 2 or manifest.get("retry_count") != 0
        or manifest.get("production_checkpoint_writes") is not False
        or manifest.get("normalized_writes") is not False
        or manifest.get("call_ledger_sha256") != _sha(ledger_path)
    ):
        raise SentinelError("sentinel manifest is not adoption eligible")
    lines = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()]
    if len(lines) != 2 or [row.get("sequence") for row in lines] != [1, 2]:
        raise SentinelError("sentinel ledger is not exactly two calls")
    paths: dict[str, Path] = {}
    for name in STREAMS:
        record = manifest["results"].get(name)
        if not isinstance(record, dict) or record.get("classification") != "NONEMPTY_AVAILABLE":
            raise SentinelError("sentinel stream is not non-empty")
        landing = run_root / str(record.get("landing_file", ""))
        if not landing.is_file() or _sha(landing) != record.get("landing_sha256"):
            raise SentinelError("sentinel Landing hash differs")
        pages = json.loads(landing.read_text(encoding="utf-8"))
        body = pages[0]["response"]["body"]
        raw = body.get("items") or {}
        items = raw.get("item", []) if isinstance(raw, dict) else []
        items = items if isinstance(items, list) else [items]
        _classify(name, tuple(items), int(body["totalCount"]), str(manifest["base_date"]))
        paths[name] = landing
    return manifest, paths


def adopt_nonempty_pair(project_root: Path, run_root: Path) -> dict[str, object]:
    """Stage an audited pair for the existing collector, making zero API calls."""
    manifest, paths = _read_audited_pair(project_root, run_root)
    base_date = str(manifest["base_date"])
    targets = {
        "price_cap": project_root / "data/landing/data_go_kr/stock_price" / f"{base_date}.json",
        "universe": project_root / "data/landing/data_go_kr/kr_equity_universe_daily" / f"{base_date}.json",
    }
    states = {
        "price_cap": BackfillState.load(project_root / "data/state/kr_equity_price_cap_daily.json", "kr_equity_price_cap_daily"),
        "universe": BackfillState.load(project_root / "data/state/kr_equity_universe_daily.json", "kr_equity_universe_daily"),
    }
    if any(base_date in state.completed_partitions or base_date in state.valid_empty_partitions for state in states.values()):
        raise SentinelError("production state already classifies the adoption date")
    if any(target.exists() for target in targets.values()):
        raise SentinelError("production Landing already exists for adoption date")
    adoption_id = "adopt_" + uuid4().hex
    with _lock(project_root, adoption_id):
        adopted = []
        try:
            for name in STREAMS:
                payload = json.loads(paths[name].read_text(encoding="utf-8"))
                write_landing_pages_atomic(tuple(payload), targets[name])
                if _sha(targets[name]) != _sha(paths[name]):
                    raise SentinelError("adopted Landing serialization/hash differs")
                states[name].mark_staged(base_date)
                adopted.append(name)
        except Exception:
            # State-first rollback is intentionally avoided: mark_staged is called only
            # after the immutable target passes hash verification. An interrupted partial
            # adoption is fail-closed and must be audited, never silently retried.
            raise
        audit = {
            "version": 1, "status": "ADOPTED_STAGED_ZERO_NETWORK",
            "adopted_at_utc": _now(), "base_date": base_date,
            "source_manifest_sha256": _sha(run_root / "manifest.json"),
            "network_requests": 0, "production_normalized_writes": False,
            "targets": {name: {"path": str(path.relative_to(project_root)), "sha256": _sha(path)} for name, path in targets.items()},
        }
        _atomic_json(run_root / "adoption.json", audit, exclusive=True)
    return {"adoption_sha256": _sha(run_root / "adoption.json"), **audit}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--date")
    parser.add_argument("--confirm-live-two-call-sentinel", action="store_true")
    parser.add_argument("--adopt-run", type=Path)
    args = parser.parse_args(argv)
    if args.adopt_run is not None:
        if args.confirm_live_two_call_sentinel or args.date:
            raise SystemExit("adoption and live arguments are mutually exclusive")
        result = adopt_nonempty_pair(args.project_root.resolve(), args.adopt_run)
    else:
        if not args.confirm_live_two_call_sentinel or not args.date:
            raise SystemExit("live mode requires --date and explicit confirmation")
        result = run_sentinel(args.project_root.resolve(), args.date)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
