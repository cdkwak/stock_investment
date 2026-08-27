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
from urllib.parse import quote, unquote


ROOT = Path(__file__).resolve().parents[3]
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
SCOPED_MARKETS = {"KOSPI", "KOSDAQ"}
KNOWN_EXCLUDED_MARKETS = {"KONEX"}
REPARSE_POINT = 0x400


class SentinelError(RuntimeError):
    pass


def _assert_plain(path: Path) -> Path:
    if not os.path.lexists(path):
        raise SentinelError(f"required evidence path is missing: {path.name}")
    info = path.lstat()
    if path.is_symlink() or (getattr(info, "st_file_attributes", 0) & REPARSE_POINT):
        raise SentinelError("links/reparse points are forbidden in sentinel evidence")
    return path


def _immediate_file(root: Path, name: object) -> Path:
    if not isinstance(name, str) or re.fullmatch(r"[A-Za-z0-9_.-]+", name) is None:
        raise SentinelError("evidence filename is not a safe immediate child")
    root = _assert_plain(root)
    path = root / name
    if path.parent != root or not os.path.lexists(path):
        raise SentinelError("evidence path is not an existing immediate child")
    _assert_plain(path)
    if not path.is_file():
        raise SentinelError("evidence path is not a plain file")
    return path


def _secret_variants(value: str) -> set[bytes]:
    decoded = unquote(value)
    values = {value, decoded, quote(decoded, safe=""), quote(decoded, safe="~")}
    return {item.encode("utf-8") for item in values if item}


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


def _atomic_bytes(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise SentinelError(f"immutable file already exists: {path.name}")
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(body); stream.flush(); os.fsync(stream.fileno())
        if path.exists():
            raise SentinelError(f"immutable file already exists: {path.name}")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class _CaptureSession:
    """Persist exact HTTP evidence before the client parses/classifies it."""
    def __init__(self, backend, run_root: Path, sequence: int, stream: str, endpoint: str, service_key: str):
        self.backend, self.run_root = backend, run_root
        self.sequence, self.stream, self.endpoint = sequence, stream, endpoint
        self.secret_variants = _secret_variants(service_key)
        self.receipt: dict[str, object] | None = None

    def get(self, url, *, params, headers, timeout):
        response = self.backend.get(url, params=params, headers=headers, timeout=timeout)
        body = response.content
        if not isinstance(body, bytes):
            raise SentinelError("HTTP response content is not exact bytes")
        raw = self.run_root / f"raw_response_{self.sequence:02d}_{self.stream}.body"
        call = self.run_root / f"raw_call_{self.sequence:02d}_{self.stream}.json"
        if any(secret in body for secret in self.secret_variants):
            safe_record = {"version": 1, "sequence": self.sequence, "stream": self.stream,
                "event": "SECRET_ECHO_BLOCKED", "captured_at_utc": _now(), "endpoint": self.endpoint,
                "http_status": int(response.status_code), "retry_count": 0,
                "raw_body_persisted": False, "response_bytes": len(body),
                "response_sha256": hashlib.sha256(body).hexdigest()}
            _atomic_json(call, safe_record, exclusive=True)
            self.receipt = {"raw_call_file": call.name, "raw_call_sha256": _sha(call),
                            "http_status": int(response.status_code), "raw_body_persisted": False}
            raise SentinelError("response body contained a configured credential variant; body not persisted")
        _atomic_bytes(raw, body)
        safe_params = {str(key): str(value) for key, value in params.items() if str(key) != "serviceKey"}
        record = {"version": 1, "sequence": self.sequence, "stream": self.stream,
                  "captured_at_utc": _now(), "endpoint": self.endpoint,
                  "public_parameters": dict(sorted(safe_params.items())),
                  "http_status": int(response.status_code), "retry_count": 0,
                  "raw_body_file": raw.name, "raw_body_bytes": len(body),
                  "raw_body_sha256": _sha(raw)}
        _atomic_json(call, record, exclusive=True)
        self.receipt = {"raw_body_file": raw.name, "raw_body_bytes": len(body),
                        "raw_body_sha256": record["raw_body_sha256"],
                        "raw_call_file": call.name, "raw_call_sha256": _sha(call),
                        "http_status": int(response.status_code)}
        return response


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
    market_counts: dict[str, int] = {}
    for item in items:
        if not isinstance(item, dict) or "mrktCtg" not in item:
            raise SentinelError("source item market field is missing")
        market = str(item["mrktCtg"]).strip()
        market_counts[market] = market_counts.get(market, 0) + 1
    unknown = set(market_counts) - SCOPED_MARKETS - KNOWN_EXCLUDED_MARKETS
    if unknown:
        raise SentinelError(f"unknown data.go.kr markets: {sorted(unknown)}")
    scoped = tuple(item for item in items if str(item["mrktCtg"]).strip() in SCOPED_MARKETS)
    if not scoped:
        raise SentinelError("source is non-empty but KOSPI/KOSDAQ scoped rows are empty")
    expected = datetime.strptime(base_date, "%Y%m%d").strftime("%Y-%m-%d")
    if stream == "price_cap":
        normalized = normalize_stock_price_items(scoped)
        frames = (normalized.price, normalized.market_cap)
        if any(frame.empty or set(frame["date"]) != {expected} for frame in frames):
            raise SentinelError("price/cap normalized date or non-empty gate failed")
        if len(normalized.price) != len(normalized.market_cap):
            raise SentinelError("price/cap fanout row counts differ")
        return {
            "classification": "NONEMPTY_AVAILABLE", "source_rows": total_count,
            "scoped_rows": len(scoped), "excluded_known_rows": total_count - len(scoped),
            "source_market_counts": dict(sorted(market_counts.items())),
            "price_rows": len(normalized.price), "market_cap_rows": len(normalized.market_cap),
        }
    frame = normalize_universe_items(scoped)
    if frame.empty or set(frame["date"]) != {expected}:
        raise SentinelError("universe normalized date or non-empty gate failed")
    return {"classification": "NONEMPTY_AVAILABLE", "source_rows": total_count,
            "scoped_rows": len(scoped), "excluded_known_rows": total_count - len(scoped),
            "source_market_counts": dict(sorted(market_counts.items())), "universe_rows": len(frame)}


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
            capture_session = _CaptureSession(
                session or __import__("requests"), run_root, sequence,
                stream_name, endpoints[stream_name], key,
            )
            client = DataGoKrClient(
                endpoint=endpoints[stream_name], service_key=key,
                session=capture_session, max_attempts=1,
            )
            try:
                result = client.fetch_all(
                    filters={"basDt": base_date}, num_of_rows=NUM_ROWS, max_pages=1,
                )
                landing = run_root / f"response_{sequence:02d}_{stream_name}.json"
                write_landing_pages_atomic(result.pages, landing)
                evidence = {
                    "sequence": sequence, "stream": stream_name, "event": "CALL_COMPLETED",
                    "captured_at_utc": _now(), "endpoint": endpoints[stream_name],
                    "public_parameters": {"basDt": base_date, "numOfRows": NUM_ROWS, "pageNo": 1, "resultType": "json"},
                    "retry_count": 0, "pages": len(result.pages),
                    "landing_file": landing.name, "landing_sha256": _sha(landing),
                    **(capture_session.receipt or {}),
                }
                results[stream_name] = evidence
                assessment = _classify(stream_name, result.items, result.total_count, base_date)
                record = {**evidence, **assessment}
                results[stream_name] = record
            except Exception as error:
                safe = str(error).replace(key, "<redacted>")
                evidence = results.get(stream_name, capture_session.receipt or {})
                if evidence:
                    results[stream_name] = evidence
                with ledger_path.open("a", encoding="utf-8", newline="\n") as stream:
                    stream.write(json.dumps({
                        "sequence": sequence, "stream": stream_name, "event": "ANOMALY",
                        "captured_at_utc": _now(), "error_type": type(error).__name__,
                        "error": safe[:240], "retry_count": 0,
                        **{key: evidence[key] for key in ("landing_file", "landing_sha256", "pages") if key in evidence},
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
    run_root = Path(os.path.abspath(run_root))
    if run_root.parent != expected_parent or not re.fullmatch(r"\d{8}T\d{6}Z_[0-9a-f]{32}", run_root.name):
        raise SentinelError("adoption run must be an immediate sentinel child")
    _assert_plain(expected_parent); _assert_plain(run_root)
    manifest_path = _immediate_file(run_root, "manifest.json")
    ledger_path = _immediate_file(run_root, "call_ledger.jsonl")
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
    configured_key = service_key_from_environment(project_root)
    for evidence_file in run_root.iterdir():
        _assert_plain(evidence_file)
        if evidence_file.is_file() and any(secret in evidence_file.read_bytes() for secret in _secret_variants(configured_key)):
            raise SentinelError("configured credential variant found in retained sentinel evidence")
    paths: dict[str, Path] = {}
    endpoints = {"price_cap": STOCK_PRICE_ENDPOINT, "universe": UNIVERSE_ENDPOINT}
    for sequence, name in enumerate(STREAMS, start=1):
        record = manifest["results"].get(name)
        if not isinstance(record, dict) or record.get("classification") != "NONEMPTY_AVAILABLE":
            raise SentinelError("sentinel stream is not non-empty")
        if lines[sequence - 1] != record:
            raise SentinelError("sentinel ledger and manifest stream record differ")
        common = {"sequence", "stream", "event", "captured_at_utc", "endpoint",
                  "public_parameters", "retry_count", "pages", "landing_file", "landing_sha256",
                  "raw_body_file", "raw_body_bytes", "raw_body_sha256", "raw_call_file",
                  "raw_call_sha256", "http_status", "classification", "source_rows",
                  "scoped_rows", "excluded_known_rows", "source_market_counts"}
        expected_keys = common | ({"price_rows", "market_cap_rows"} if name == "price_cap" else {"universe_rows"})
        expected_public = {"basDt": str(manifest["base_date"]), "numOfRows": NUM_ROWS,
                           "pageNo": 1, "resultType": "json"}
        if (set(record) != expected_keys or record.get("sequence") != sequence
                or record.get("stream") != name or record.get("event") != "CALL_COMPLETED"
                or record.get("endpoint") != endpoints[name] or record.get("public_parameters") != expected_public
                or record.get("retry_count") != 0 or record.get("pages") != 1
                or record.get("http_status") != 200):
            raise SentinelError("sentinel ledger/manifest schema or call identity differs")
        raw_body = _immediate_file(run_root, record.get("raw_body_file"))
        raw_call = _immediate_file(run_root, record.get("raw_call_file"))
        raw_call_record = json.loads(raw_call.read_text(encoding="utf-8"))
        expected_raw_keys = {"version", "sequence", "stream", "captured_at_utc", "endpoint",
                             "public_parameters", "http_status", "retry_count", "raw_body_file",
                             "raw_body_bytes", "raw_body_sha256"}
        expected_raw_public = {"basDt": str(manifest["base_date"]), "numOfRows": str(NUM_ROWS),
                               "pageNo": "1", "resultType": "json"}
        if (set(raw_call_record) != expected_raw_keys or raw_call_record.get("version") != 1
                or raw_call_record.get("sequence") != sequence or raw_call_record.get("stream") != name
                or raw_call_record.get("endpoint") != endpoints[name]
                or raw_call_record.get("public_parameters") != expected_raw_public
                or raw_call_record.get("http_status") != 200 or raw_call_record.get("retry_count") != 0
                or raw_call_record.get("raw_body_file") != raw_body.name
                or raw_call_record.get("raw_body_sha256") != record.get("raw_body_sha256")
                or raw_call_record.get("raw_body_bytes") != record.get("raw_body_bytes")
                or _sha(raw_call) != record.get("raw_call_sha256")):
            raise SentinelError("sentinel raw call evidence differs")
        if (_sha(raw_body) != record.get("raw_body_sha256")
                or raw_body.stat().st_size != record.get("raw_body_bytes")
                or _sha(raw_call) != record.get("raw_call_sha256")):
            raise SentinelError("sentinel exact HTTP evidence differs")
        landing = _immediate_file(run_root, record.get("landing_file"))
        if _sha(landing) != record.get("landing_sha256"):
            raise SentinelError("sentinel Landing hash differs")
        pages = json.loads(landing.read_text(encoding="utf-8"))
        try:
            raw_payload = json.loads(raw_body.read_bytes())
        except json.JSONDecodeError as error:
            raise SentinelError("sentinel raw body is not JSON") from error
        if pages != [raw_payload]:
            raise SentinelError("parsed Landing is not exactly the captured raw response")
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
