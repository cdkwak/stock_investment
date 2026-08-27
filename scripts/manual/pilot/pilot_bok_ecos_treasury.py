"""Explicit, bounded BOK ECOS Treasury diagnostic pilot.

Importing this module performs no I/O. Live execution requires an explicit
phase, reviewed configuration, hard-coded caps, an environment credential, and
the confirm flag. It writes diagnostic Landing only, never Normalized data.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from typing import Iterable
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.manual.pilot.bok_ecos_treasury_pilot_support import (
    EcosPilotError,
    FINALITY_MAX_VALUE_REQUESTS,
    FINALITY_TABLE_CODE,
    FINALITY_UI_TRANSACTION,
    ITEM_LIST_OPERATION,
    MAX_METADATA_REQUESTS,
    MAX_VALUE_OBSERVATIONS,
    MAX_VALUE_REQUESTS,
    VALUE_OPERATION,
    config_sha256,
    finality_redacted_route,
    finality_value_url,
    item_list_url,
    load_finality_config,
    load_config,
    parse_finality_ui_marker,
    parse_finality_value,
    parse_item_metadata,
    parse_value,
    plan_finality_scopes,
    plan_value_scopes,
    redacted_route,
    select_finality_target,
    value_url,
)


LANDING_RELATIVE = Path("data/landing/diagnostics/bok_ecos_treasury_pilot")
API_KEY_ENV = "BOK_ECOS_API_KEY"
TIMEOUT_SECONDS = 20
FINALITY_LANDING_RELATIVE = Path(
    "data/landing/diagnostics/bok_ecos_treasury_finality_observation"
)
FINALITY_STATE_RELATIVE = Path(
    "data/state/bok_ecos_treasury_finality_observation.json"
)
FINALITY_UI_URL = "https://ecos.bok.or.kr/serviceEndpoint/httpService/request.json"
FINALITY_WINDOW_START_HOUR_KST = 17
FINALITY_WINDOW_END_HOUR_KST = 18
SEOUL = ZoneInfo("Asia/Seoul")


class PilotStopped(EcosPilotError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def _atomic_replace(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(_json_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _immutable_bytes(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
    except FileExistsError as error:
        raise PilotStopped(f"immutable Landing already exists: {path.name}") from error
    finally:
        Path(temporary).unlink(missing_ok=True)


class Ledger:
    def __init__(self, path: Path, *, secrets: Iterable[str] = ()):
        self.path = path
        self.secrets = tuple(value for value in secrets if value)
        path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: str, **fields: object) -> None:
        forbidden = {"api_key", "authorization", "cookie", "headers", "full_url", "url"}
        if forbidden.intersection(key.lower() for key in fields):
            raise PilotStopped("sensitive ledger field name rejected")
        record = {"timestamp_utc": _now(), "event": event, **fields}
        text = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        for secret in self.secrets:
            text = text.replace(secret, "<redacted>")
        with self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(text + "\n")
            stream.flush()
            os.fsync(stream.fileno())


class BudgetedClient:
    def __init__(
        self, session, ledger: Ledger, *, api_key: str, max_requests: int,
        initial_requests: int = 0,
    ):
        if not 0 <= initial_requests <= max_requests:
            raise PilotStopped("initial raw-request count is outside the hard cap")
        self.session = session
        self.ledger = ledger
        self.api_key = api_key
        self.max_requests = max_requests
        self.initial_requests = initial_requests
        self.requests = initial_requests

    @property
    def requests_this_process(self) -> int:
        return self.requests - self.initial_requests

    def get(self, *, operation: str, scope_id: str, route: str, url: str, landing: Path) -> bytes:
        if self.requests >= self.max_requests:
            raise PilotStopped("hard raw-request cap reached")
        self.requests += 1
        started = time.monotonic()
        try:
            response = self.session.get(url, timeout=TIMEOUT_SECONDS)
        except requests.RequestException as error:
            message = str(error).replace(self.api_key, "<redacted>")
            self.ledger.append(
                "HTTP_ERROR", sequence=self.requests, operation=operation,
                scope=scope_id, route=route, error=message,
            )
            raise PilotStopped(message) from error
        body = bytes(response.content)
        if self.api_key.encode("utf-8") in body:
            self.ledger.append(
                "SECRET_RESPONSE_BLOCKED", sequence=self.requests,
                operation=operation, scope=scope_id, route=route,
            )
            raise PilotStopped("response body contains the API credential")
        digest = hashlib.sha256(body).hexdigest()
        self.ledger.append(
            "HTTP_RESPONSE", sequence=self.requests, operation=operation,
            scope=scope_id, route=route, status_code=int(response.status_code),
            elapsed_ms=round((time.monotonic() - started) * 1000, 3),
            response_bytes=len(body), response_sha256=digest,
        )
        _immutable_bytes(landing, body)
        if response.status_code != 200:
            raise PilotStopped(f"ECOS HTTP {response.status_code}")
        return body


@contextmanager
def _lock(root: Path, run_id: str):
    path = root / ".pilot.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as error:
        raise PilotStopped("BOK ECOS diagnostic lock is already held") from error
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(run_id)
            stream.flush()
            os.fsync(stream.fileno())
        yield
    finally:
        if path.read_text(encoding="utf-8") == run_id:
            path.unlink()


def _new_run(root: Path, phase: str) -> tuple[str, Path]:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex
    run_dir = root / f"{phase}_{run_id}"
    run_dir.mkdir(parents=True, exist_ok=False)
    return run_id, run_dir


def _validated_run_dir(root: Path, value: Path) -> Path:
    resolved = value.resolve()
    if resolved.parent != root.resolve():
        raise PilotStopped("run directory must be an immediate child of the pilot Landing root")
    return resolved


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_metadata(*, project_root: Path, config_path: Path, session=None) -> dict[str, object]:
    config = load_config(config_path)
    key = os.environ.get(API_KEY_ENV, "")
    if not key:
        raise PilotStopped(f"{API_KEY_ENV} is required in the process environment")
    root = project_root / LANDING_RELATIVE
    run_id, run_dir = _new_run(root, "metadata")
    ledger = Ledger(run_dir / "call_ledger.jsonl", secrets=(key,))
    checkpoint = {
        "version": 1, "phase": "metadata", "run_id": run_id,
        "config_sha256": config_sha256(config), "status": "CREATED",
        "max_raw_requests": MAX_METADATA_REQUESTS, "completed": {},
    }
    _atomic_replace(run_dir / "checkpoint.json", checkpoint)
    with _lock(root, run_id):
        client = BudgetedClient(session or requests.Session(), ledger, api_key=key, max_requests=1)
        ledger.append("RUN_CREATED", run_id=run_id, phase="metadata", config_sha256=checkpoint["config_sha256"])
        landing = run_dir / "response_01_item_metadata.json"
        body = client.get(
            operation=ITEM_LIST_OPERATION, scope_id=config.table_code,
            route=redacted_route(ITEM_LIST_OPERATION, config),
            url=item_list_url(key, config), landing=landing,
        )
        metadata = parse_item_metadata(body, config)
        summary = {
            "version": 1, "source": "bok_ecos", "operation": ITEM_LIST_OPERATION,
            "config_sha256": checkpoint["config_sha256"], "captured_at_utc": _now(),
            "landing_file": landing.name, "landing_sha256": _sha(landing),
            "six_tenor_identity": metadata,
            "publication_semantics": "not_supplied_by_item_metadata",
            "revision_semantics": "not_supplied_by_item_metadata",
        }
        summary_path = run_dir / "metadata_summary.json"
        _atomic_replace(summary_path, summary)
        checkpoint["completed"] = {
            "item_metadata": {"landing_sha256": _sha(landing), "verified_tenors": len(metadata)}
        }
        checkpoint["status"] = "METADATA_CAPTURED_REVIEW_REQUIRED"
        checkpoint["raw_requests"] = client.requests
        checkpoint["metadata_summary_sha256"] = _sha(summary_path)
        _atomic_replace(run_dir / "checkpoint.json", checkpoint)
        ledger.append(
            "RUN_COMPLETED", status=checkpoint["status"], raw_requests=client.requests,
            metadata_summary_sha256=checkpoint["metadata_summary_sha256"],
        )
    return {"run_dir": str(run_dir), **checkpoint}


def finalize_retained_metadata(
    *, project_root: Path, config_path: Path, run_dir: Path,
    original_config_sha256: str,
) -> dict[str, object]:
    """Finalize one retained HTTP-200 metadata response without network I/O.

    This recovery path is deliberately narrow: it accepts only the exact
    fail-closed state produced when the reviewed table label differed from the
    live response.  It never adopts an unledgered response or a failed call.
    """
    config = load_config(config_path)
    root = project_root / LANDING_RELATIVE
    directory = _validated_run_dir(root, run_dir)
    if not directory.name.startswith("metadata_"):
        raise PilotStopped("retained run is not a metadata run")
    checkpoint_path = directory / "checkpoint.json"
    ledger_path = directory / "call_ledger.jsonl"
    landing = directory / "response_01_item_metadata.json"
    if not all(path.is_file() for path in (checkpoint_path, ledger_path, landing)):
        raise PilotStopped("retained metadata evidence is incomplete")
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    if checkpoint.get("phase") != "metadata" or checkpoint.get("status") != "CREATED":
        raise PilotStopped("retained metadata checkpoint is not the fail-closed state")
    if checkpoint.get("config_sha256") != original_config_sha256:
        raise PilotStopped("original reviewed config hash differs")
    if checkpoint.get("completed") != {} or checkpoint.get("max_raw_requests") != 1:
        raise PilotStopped("retained metadata checkpoint is not pristine")
    try:
        records = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()]
    except (OSError, json.JSONDecodeError) as error:
        raise PilotStopped("retained metadata ledger is invalid") from error
    if [record.get("event") for record in records] != ["RUN_CREATED", "HTTP_RESPONSE"]:
        raise PilotStopped("retained metadata ledger events are not exact")
    response = records[1]
    landing_hash = _sha(landing)
    if (
        response.get("sequence") != 1
        or response.get("operation") != ITEM_LIST_OPERATION
        or response.get("status_code") != 200
        or response.get("response_bytes") != landing.stat().st_size
        or response.get("response_sha256") != landing_hash
    ):
        raise PilotStopped("retained metadata HTTP evidence does not reconcile")
    metadata = parse_item_metadata(landing.read_bytes(), config)
    summary = {
        "version": 1, "source": "bok_ecos", "operation": ITEM_LIST_OPERATION,
        "config_sha256": config_sha256(config),
        "original_reviewed_config_sha256": original_config_sha256,
        "finalized_offline_at_utc": _now(), "network_requests_during_finalization": 0,
        "landing_file": landing.name, "landing_sha256": landing_hash,
        "call_ledger_sha256_before_finalization": _sha(ledger_path),
        "six_tenor_identity": metadata,
        "publication_semantics": "not_supplied_by_item_metadata",
        "revision_semantics": "not_supplied_by_item_metadata",
    }
    summary_path = directory / "metadata_summary.json"
    if summary_path.exists():
        raise PilotStopped("metadata summary already exists")
    _atomic_replace(summary_path, summary)
    checkpoint["original_reviewed_config_sha256"] = original_config_sha256
    checkpoint["config_sha256"] = config_sha256(config)
    checkpoint["completed"] = {
        "item_metadata": {"landing_sha256": landing_hash, "verified_tenors": len(metadata)}
    }
    checkpoint["status"] = "METADATA_CAPTURED_REVIEW_REQUIRED"
    checkpoint["raw_requests"] = 1
    checkpoint["network_requests_during_finalization"] = 0
    checkpoint["metadata_summary_sha256"] = _sha(summary_path)
    _atomic_replace(checkpoint_path, checkpoint)
    Ledger(ledger_path).append(
        "OFFLINE_FINALIZATION_COMPLETED", status=checkpoint["status"],
        corrected_config_sha256=checkpoint["config_sha256"],
        metadata_summary_sha256=checkpoint["metadata_summary_sha256"],
        network_requests=0,
    )
    return {"run_dir": str(directory), **checkpoint}


def _load_approved_metadata(root: Path, run_dir: Path, approval: str, config) -> dict[str, object]:
    directory = _validated_run_dir(root, run_dir)
    summary_path = directory / "metadata_summary.json"
    checkpoint_path = directory / "checkpoint.json"
    if not summary_path.is_file() or not checkpoint_path.is_file():
        raise PilotStopped("approved metadata artifacts are missing")
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    if checkpoint.get("status") != "METADATA_CAPTURED_REVIEW_REQUIRED":
        raise PilotStopped("metadata run is not reviewable")
    if checkpoint.get("config_sha256") != config_sha256(config):
        raise PilotStopped("metadata and value configs differ")
    actual = _sha(summary_path)
    if approval != actual or checkpoint.get("metadata_summary_sha256") != actual:
        raise PilotStopped("metadata approval hash differs")
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    landing = directory / str(summary.get("landing_file", ""))
    if not landing.is_file() or _sha(landing) != summary.get("landing_sha256"):
        raise PilotStopped("approved metadata Landing/hash differs")
    parse_item_metadata(landing.read_bytes(), config)
    return summary


def _restore_value_checkpoint(run_dir: Path, *, run_id: str, config_hash: str, metadata_hash: str) -> dict[str, object]:
    path = run_dir / "checkpoint.json"
    if not path.exists():
        return {
            "version": 1, "phase": "values", "run_id": run_id,
            "config_sha256": config_hash, "metadata_summary_sha256": metadata_hash,
            "status": "CREATED", "max_raw_requests": MAX_VALUE_REQUESTS,
            "max_observations": MAX_VALUE_OBSERVATIONS, "completed": {},
        }
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("phase") != "values" or value.get("run_id") != run_id:
        raise PilotStopped("value checkpoint identity differs")
    if value.get("config_sha256") != config_hash or value.get("metadata_summary_sha256") != metadata_hash:
        raise PilotStopped("value checkpoint plan differs")
    if not isinstance(value.get("completed"), dict):
        raise PilotStopped("value checkpoint completed map is invalid")
    return value


def _resume_request_count(ledger_path: Path, completed: dict[str, object]) -> int:
    if not ledger_path.exists():
        if completed:
            raise PilotStopped("completed checkpoint has no call ledger")
        return 0
    try:
        records = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()]
    except (OSError, json.JSONDecodeError) as error:
        raise PilotStopped("value call ledger is invalid") from error
    failures = [
        row for row in records
        if row.get("event") in {"HTTP_ERROR", "SECRET_RESPONSE_BLOCKED"}
    ]
    if failures:
        raise PilotStopped("retry0 policy forbids resuming a run after a request failure")
    responses = [row for row in records if row.get("event") == "HTTP_RESPONSE"]
    sequences = [row.get("sequence") for row in responses]
    if sequences != list(range(1, len(responses) + 1)):
        raise PilotStopped("HTTP ledger sequence is not exact and contiguous")
    if len(responses) != len(completed):
        raise PilotStopped("HTTP ledger and completed checkpoint counts differ")
    return len(responses)


def _read_toss_close(project_root: Path, source_date: str, tenor: str) -> str | None:
    instrument = f"KR_BOND_{tenor}"
    year = source_date[:4]
    path = project_root / "data/normalized/kr_treasury_yield_daily" / f"instrument={instrument}" / f"year={year}" / "data.parquet"
    if not path.is_file():
        return None
    frame = pd.read_parquet(path, columns=["date", "instrument", "close"])
    dates = pd.to_datetime(frame["date"], errors="raise").dt.strftime("%Y%m%d")
    matched = frame[(dates == source_date) & (frame["instrument"] == instrument)]
    if len(matched) > 1:
        raise PilotStopped("retained Toss comparison key is duplicated")
    return None if matched.empty else str(matched.iloc[0]["close"])


def _comparisons(project_root: Path, observations: list[dict[str, object]]) -> list[dict[str, object]]:
    result = []
    for observation in observations:
        toss = _read_toss_close(project_root, str(observation["source_date"]), str(observation["tenor"]))
        official = str(observation["value"])
        classification = "TOSS_MISSING" if toss is None else (
            "EXACT_VALUE_MATCH" if float(toss) == float(official) else "DISTINCT_SERIES_CANDIDATE"
        )
        result.append(
            {
                "source_date": observation["source_date"], "tenor": observation["tenor"],
                "official_value": official, "official_unit": observation["unit_name"],
                "toss_close": toss, "classification": classification,
                "compatibility_inferred": False,
            }
        )
    return result


def _write_or_verify_json(path: Path, value: object) -> None:
    body = _json_bytes(value)
    if path.exists():
        if path.read_bytes() != body:
            raise PilotStopped(f"retained {path.name} differs from reconstructed content")
        return
    _immutable_bytes(path, body)


def finalize_retained_values(
    *, project_root: Path, config_path: Path, metadata_run_dir: Path,
    approve_metadata_sha256: str, run_dir: Path,
) -> dict[str, object]:
    """Finalize an eight-response value run using retained evidence only."""
    config = load_config(config_path)
    config_hash = config_sha256(config)
    root = project_root / LANDING_RELATIVE
    _load_approved_metadata(root, metadata_run_dir, approve_metadata_sha256, config)
    directory = _validated_run_dir(root, run_dir)
    if not directory.name.startswith("values_"):
        raise PilotStopped("retained run is not a values run")
    checkpoint_path = directory / "checkpoint.json"
    ledger_path = directory / "call_ledger.jsonl"
    if not checkpoint_path.is_file() or not ledger_path.is_file():
        raise PilotStopped("retained value evidence is incomplete")
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    if checkpoint.get("phase") != "values" or checkpoint.get("status") != "IN_PROGRESS":
        raise PilotStopped("retained value checkpoint is not finalizable")
    if (
        checkpoint.get("run_id") != directory.name.removeprefix("values_")
        or checkpoint.get("config_sha256") != config_hash
        or checkpoint.get("metadata_summary_sha256") != approve_metadata_sha256
        or checkpoint.get("max_raw_requests") != MAX_VALUE_REQUESTS
        or checkpoint.get("max_observations") != MAX_VALUE_OBSERVATIONS
    ):
        raise PilotStopped("retained value checkpoint identity differs")
    completed = checkpoint.get("completed")
    scopes = plan_value_scopes(config)
    if not isinstance(completed, dict) or set(completed) != {scope.scope_id for scope in scopes}:
        raise PilotStopped("retained value scopes are not exactly complete")
    try:
        records = [json.loads(line) for line in ledger_path.read_text(encoding="utf-8").splitlines()]
    except (OSError, json.JSONDecodeError) as error:
        raise PilotStopped("retained value ledger is invalid") from error
    if [record.get("event") for record in records] != ["RUN_CREATED"] + [
        "HTTP_RESPONSE"
    ] * MAX_VALUE_REQUESTS:
        raise PilotStopped("retained value ledger events are not exact")
    observations: list[dict[str, object]] = []
    classifications: dict[str, str] = {}
    for sequence, scope in enumerate(scopes, start=1):
        entry = completed[scope.scope_id]
        landing = directory / f"response_{sequence:02d}_{scope.scope_id}.json"
        response = records[sequence]
        if not landing.is_file():
            raise PilotStopped("retained value Landing is missing")
        landing_hash = _sha(landing)
        if (
            response.get("sequence") != sequence
            or response.get("operation") != VALUE_OPERATION
            or response.get("scope") != scope.scope_id
            or response.get("status_code") != 200
            or response.get("response_bytes") != landing.stat().st_size
            or response.get("response_sha256") != landing_hash
            or entry.get("landing_file") != landing.name
            or entry.get("landing_sha256") != landing_hash
        ):
            raise PilotStopped("retained value HTTP/Landing/checkpoint evidence differs")
        parsed = parse_value(landing.read_bytes(), config, scope)
        if (
            entry.get("classification") != parsed.classification
            or entry.get("observations") != len(parsed.observations)
            or not isinstance(entry.get("captured_at_utc"), str)
        ):
            raise PilotStopped("retained value parser/checkpoint evidence differs")
        classifications[scope.scope_id] = parsed.classification
        observations.extend(
            {**row, "captured_at_utc": entry["captured_at_utc"]}
            for row in parsed.observations
        )
    if len(observations) > MAX_VALUE_OBSERVATIONS:
        raise PilotStopped("hard observation cap exceeded")
    _write_or_verify_json(directory / "observations.json", observations)
    comparisons = _comparisons(project_root, observations)
    _write_or_verify_json(directory / "comparison_to_toss.json", comparisons)
    checkpoint["status"] = "VALUE_PILOT_COMPLETE_REVIEW_REQUIRED"
    checkpoint["raw_requests_total"] = MAX_VALUE_REQUESTS
    checkpoint["raw_requests_during_finalization"] = 0
    checkpoint["observations"] = len(observations)
    checkpoint["valid_empty_scopes"] = sorted(
        scope for scope, classification in classifications.items()
        if classification == "VALID_EMPTY"
    )
    checkpoint["observations_sha256"] = _sha(directory / "observations.json")
    checkpoint["comparison_sha256"] = _sha(directory / "comparison_to_toss.json")
    _atomic_replace(checkpoint_path, checkpoint)
    Ledger(ledger_path).append(
        "OFFLINE_VALUE_FINALIZATION_COMPLETED", status=checkpoint["status"],
        raw_requests_total=MAX_VALUE_REQUESTS, network_requests=0,
        observations=len(observations),
        observations_sha256=checkpoint["observations_sha256"],
        comparison_sha256=checkpoint["comparison_sha256"],
    )
    return {"run_dir": str(directory), **checkpoint}


def run_values(
    *, project_root: Path, config_path: Path, metadata_run_dir: Path,
    approve_metadata_sha256: str, resume_run_dir: Path | None = None, session=None,
) -> dict[str, object]:
    config = load_config(config_path)
    config_hash = config_sha256(config)
    key = os.environ.get(API_KEY_ENV, "")
    if not key:
        raise PilotStopped(f"{API_KEY_ENV} is required in the process environment")
    root = project_root / LANDING_RELATIVE
    _load_approved_metadata(root, metadata_run_dir, approve_metadata_sha256, config)
    if resume_run_dir is None:
        run_id, run_dir = _new_run(root, "values")
    else:
        run_dir = _validated_run_dir(root, resume_run_dir)
        if not run_dir.name.startswith("values_"):
            raise PilotStopped("resume directory is not a values run")
        run_id = run_dir.name.removeprefix("values_")
    checkpoint = _restore_value_checkpoint(
        run_dir, run_id=run_id, config_hash=config_hash,
        metadata_hash=approve_metadata_sha256,
    )
    _atomic_replace(run_dir / "checkpoint.json", checkpoint)
    completed = checkpoint["completed"]
    ledger_path = run_dir / "call_ledger.jsonl"
    initial_requests = _resume_request_count(ledger_path, completed)
    ledger = Ledger(ledger_path, secrets=(key,))
    observations: list[dict[str, object]] = []
    with _lock(root, run_id):
        client = BudgetedClient(
            session or requests.Session(), ledger, api_key=key,
            max_requests=MAX_VALUE_REQUESTS, initial_requests=initial_requests,
        )
        ledger.append("RUN_RESUMED" if resume_run_dir else "RUN_CREATED", run_id=run_id, phase="values")
        for sequence, scope in enumerate(plan_value_scopes(config), start=1):
            landing = run_dir / f"response_{sequence:02d}_{scope.scope_id}.json"
            if scope.scope_id in completed:
                if not landing.is_file() or _sha(landing) != completed[scope.scope_id].get("landing_sha256"):
                    raise PilotStopped("completed Landing/checkpoint hash differs")
                parsed = parse_value(landing.read_bytes(), config, scope)
            else:
                if landing.exists():
                    raise PilotStopped("uncheckpointed Landing cannot be adopted")
                body = client.get(
                    operation=VALUE_OPERATION, scope_id=scope.scope_id,
                    route=redacted_route(VALUE_OPERATION, config, scope),
                    url=value_url(key, config, scope), landing=landing,
                )
                parsed = parse_value(body, config, scope)
                completed[scope.scope_id] = {
                    "landing_file": landing.name, "landing_sha256": _sha(landing),
                    "classification": parsed.classification,
                    "observations": len(parsed.observations), "captured_at_utc": _now(),
                }
                checkpoint["status"] = "IN_PROGRESS"
                _atomic_replace(run_dir / "checkpoint.json", checkpoint)
            captured = completed[scope.scope_id]["captured_at_utc"]
            observations.extend({**row, "captured_at_utc": captured} for row in parsed.observations)
            if len(observations) > MAX_VALUE_OBSERVATIONS:
                raise PilotStopped("hard observation cap exceeded")
        _atomic_replace(run_dir / "observations.json", observations)
        _atomic_replace(run_dir / "comparison_to_toss.json", _comparisons(project_root, observations))
        checkpoint["status"] = "VALUE_PILOT_COMPLETE_REVIEW_REQUIRED"
        checkpoint["raw_requests_total"] = client.requests
        checkpoint["raw_requests_this_process"] = client.requests_this_process
        checkpoint["observations"] = len(observations)
        _atomic_replace(run_dir / "checkpoint.json", checkpoint)
        ledger.append(
            "RUN_COMPLETED", status=checkpoint["status"],
            raw_requests_total=client.requests,
            raw_requests_this_process=client.requests_this_process,
            observations=len(observations),
        )
    return {"run_dir": str(run_dir), **checkpoint}


def _strict_json_bytes(body: bytes, *, label: str) -> dict[str, object]:
    def pairs(values):
        result = {}
        for key, value in values:
            if key in result:
                raise PilotStopped(f"{label} contains a duplicate JSON key")
            result[key] = value
        return result

    try:
        value = json.loads(body, object_pairs_hook=pairs)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PilotStopped(f"{label} is not valid JSON") from error
    if not isinstance(value, dict):
        raise PilotStopped(f"{label} root must be an object")
    return value


def _finality_state(path: Path) -> dict[str, object]:
    if not path.exists():
        return {
            "version": 1,
            "dataset": "bok_ecos_kr_treasury_yield_source_observation",
            "status": "PUBLICATION_FINALITY_UNKNOWN",
            "batches": [],
        }
    value = _strict_json_bytes(path.read_bytes(), label="finality state")
    if (
        value.get("version") != 1
        or value.get("dataset") != "bok_ecos_kr_treasury_yield_source_observation"
        or not isinstance(value.get("batches"), list)
    ):
        raise PilotStopped("finality state identity or schema differs")
    dates = [row.get("observation_date_kst") for row in value["batches"] if isinstance(row, dict)]
    if len(dates) != len(value["batches"]) or len(dates) != len(set(dates)):
        raise PilotStopped("finality state batch dates are invalid or duplicated")
    return value


def _finality_ui_request_body(now_kst: datetime) -> dict[str, object]:
    return {
        "header": {
            "guidSeq": 1,
            "trxCd": FINALITY_UI_TRANSACTION,
            "scrId": "IECOSPC",
            "sysCd": "03",
            "fstChnCd": "WEB",
            "langDvsnCd": "KO",
            "envDvsnCd": "D",
            "sndRspnDvsnCd": "S",
            "sndDtm": now_kst.strftime("%Y%m%d%H%M%S000"),
            "ipAddr": None,
            "usrId": "IECOSPC",
            "pageNum": 1,
            "pageCnt": 1000,
        },
        "data": {"dsIdList": [FINALITY_TABLE_CODE]},
    }


def _sanitized_ui_body(body: bytes) -> bytes:
    payload = _strict_json_bytes(body, label="official UI response")
    header = payload.get("header")
    if isinstance(header, dict) and "ipAddr" in header:
        header["ipAddr"] = "<redacted>"
    return _json_bytes(payload)


def _canonical_row_sha256(row: dict[str, object]) -> str:
    body = json.dumps(
        row, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _read_finality_ledger(path: Path) -> list[dict[str, object]]:
    try:
        rows = [
            _strict_json_bytes(line.encode("utf-8"), label="finality ledger record")
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    except OSError as error:
        raise PilotStopped("finality ledger is unreadable") from error
    return rows


def _finalize_finality_run(
    *, project_root: Path, run_dir: Path, metadata_summary_path: Path,
    approve_metadata_sha256: str,
) -> dict[str, object]:
    config = load_finality_config(
        metadata_summary_path, approve_sha256=approve_metadata_sha256,
    )
    checkpoint_path = run_dir / "checkpoint.json"
    ledger_path = run_dir / "observation_ledger.jsonl"
    if not checkpoint_path.is_file() or not ledger_path.is_file():
        raise PilotStopped("finality run evidence is incomplete")
    checkpoint = _strict_json_bytes(
        checkpoint_path.read_bytes(), label="finality checkpoint",
    )
    if (
        checkpoint.get("version") != 1
        or checkpoint.get("status") not in {"IN_PROGRESS", "CAPTURED", "COMPLETE"}
        or checkpoint.get("max_statistic_search_calls") != FINALITY_MAX_VALUE_REQUESTS
        or checkpoint.get("retry_budget") != 0
        or checkpoint.get("metadata_summary_sha256") != approve_metadata_sha256
    ):
        raise PilotStopped("finality checkpoint identity or policy differs")
    start_date = str(checkpoint.get("range_start_date", ""))
    end_date = str(checkpoint.get("range_end_date", ""))
    scopes = plan_finality_scopes(config, start_date=start_date, end_date=end_date)
    ui_path = run_dir / "response_00_official_ui_table_info.json"
    value_paths = [
        run_dir / f"response_{sequence:02d}_{scope.scope_id}.json"
        for sequence, scope in enumerate(scopes, start=1)
    ]
    if not ui_path.is_file() or any(not path.is_file() for path in value_paths):
        raise PilotStopped("retry0 finality run has incomplete Landing and cannot resume")
    records = _read_finality_ledger(ledger_path)
    expected_events = ["RUN_CREATED", "UI_RESPONSE"] + [
        "VALUE_RESPONSE"
    ] * FINALITY_MAX_VALUE_REQUESTS
    if [row.get("event") for row in records] != expected_events:
        raise PilotStopped("finality ledger event sequence is not exact")
    ui_record = records[1]
    if (
        ui_record.get("response_sha256") != _sha(ui_path)
        or ui_record.get("response_bytes") != ui_path.stat().st_size
        or ui_record.get("status_code") != 200
    ):
        raise PilotStopped("official UI Landing and ledger differ")
    ui_marker = parse_finality_ui_marker(ui_path.read_bytes())
    parsed: dict[str, tuple[dict[str, object], ...]] = {}
    response_evidence: list[dict[str, object]] = []
    for sequence, (scope, path) in enumerate(zip(scopes, value_paths), start=1):
        record = records[sequence + 1]
        if (
            record.get("sequence") != sequence
            or record.get("scope") != scope.scope_id
            or record.get("status_code") != 200
            or record.get("response_sha256") != _sha(path)
            or record.get("response_bytes") != path.stat().st_size
        ):
            raise PilotStopped("finality value Landing and ledger differ")
        parsed[scope.tenor] = parse_finality_value(path.read_bytes(), config, scope)
        response_evidence.append(
            {
                "tenor": scope.tenor,
                "landing_file": path.name,
                "landing_sha256": _sha(path),
                "rows": len(parsed[scope.tenor]),
            }
        )
    target_date = select_finality_target(parsed)
    selected_rows: dict[str, dict[str, object]] = {}
    for tenor in config.tenors:
        matches = [row for row in parsed[tenor] if row["source_date"] == target_date]
        if len(matches) != 1:
            raise PilotStopped("selected provider-native date is not exact across six tenors")
        row = matches[0]
        selected_rows[tenor] = {
            "fields": row,
            "canonical_row_sha256": _canonical_row_sha256(row),
        }
    state_path = project_root / FINALITY_STATE_RELATIVE
    state = _finality_state(state_path)
    batches = state["batches"]
    observation_date = str(checkpoint.get("observation_date_kst", ""))
    existing = [row for row in batches if row.get("observation_date_kst") == observation_date]
    if existing:
        retained = existing[0] if len(existing) == 1 else None
        retained_comparison = (
            retained.get("next_provider_day_comparison")
            if isinstance(retained, dict) else None
        )
        if (
            retained is None
            or retained.get("run_id") != checkpoint.get("run_id")
            or retained.get("selected_date") != target_date
            or retained.get("selected_rows") != selected_rows
            or retained.get("official_ui_marker") != ui_marker
            or retained.get("official_ui_landing_sha256") != _sha(ui_path)
            or retained.get("responses") != response_evidence
            or retained.get("statistic_search_calls") != FINALITY_MAX_VALUE_REQUESTS
            or retained.get("official_ui_calls") != 1
            or retained.get("retry_count") != 0
            or retained.get("normalized_writes") != 0
            or not isinstance(retained_comparison, dict)
            or not isinstance(retained_comparison.get("status"), str)
        ):
            raise PilotStopped("finality state already contains a different observation batch")
        checkpoint["status"] = "COMPLETE"
        checkpoint["selected_date"] = target_date
        checkpoint["state_sha256"] = _sha(state_path)
        checkpoint["comparison_status"] = retained_comparison["status"]
        expected_checkpoint = _json_bytes(checkpoint)
        if checkpoint_path.read_bytes() != expected_checkpoint:
            _atomic_replace(checkpoint_path, checkpoint)
        return {
            "status": "NOOP_ALREADY_SUCCEEDED",
            "run_dir": str(run_dir),
            "statistic_search_calls": 0,
            "official_ui_calls": 0,
            "selected_date": existing[0]["selected_date"],
        }
    previous = batches[-1] if batches else None
    comparison: dict[str, object]
    if previous is None:
        comparison = {"status": "PENDING_FIRST_BATCH", "tenors": {}}
    else:
        previous_date = str(previous.get("selected_date", ""))
        previous_rows = previous.get("selected_rows")
        if not isinstance(previous_rows, dict):
            raise PilotStopped("previous finality batch selected rows are invalid")
        tenor_comparison: dict[str, dict[str, object]] = {}
        missing_previous = False
        changed = False
        for tenor in config.tenors:
            prior_matches = [
                row for row in parsed[tenor] if row["source_date"] == previous_date
            ]
            if len(prior_matches) != 1:
                missing_previous = True
                tenor_comparison[tenor] = {"status": "PREVIOUS_DATE_NOT_RETURNED"}
                continue
            observed_hash = _canonical_row_sha256(prior_matches[0])
            prior_entry = previous_rows.get(tenor)
            if not isinstance(prior_entry, dict):
                raise PilotStopped("previous finality tenor evidence is invalid")
            prior_hash = prior_entry.get("canonical_row_sha256")
            fields_match = prior_entry.get("fields") == prior_matches[0]
            hash_match = prior_hash == observed_hash
            changed = changed or not fields_match or not hash_match
            tenor_comparison[tenor] = {
                "status": "SAME" if fields_match and hash_match else "CHANGED",
                "fields_match": fields_match,
                "canonical_row_sha256_match": hash_match,
                "observed_canonical_row_sha256": observed_hash,
            }
        comparison = {
            "status": (
                "PREVIOUS_DATE_NOT_RETURNED" if missing_previous
                else "CHANGED" if changed else "SAME"
            ),
            "previous_selected_date": previous_date,
            "tenors": tenor_comparison,
        }
    batch = {
        "observation_date_kst": observation_date,
        "observation_window_kst": checkpoint["observation_window_kst"],
        "captured_at_utc": checkpoint["captured_at_utc"],
        "run_id": checkpoint["run_id"],
        "range_start_date": start_date,
        "range_end_date": end_date,
        "selected_date": target_date,
        "statistic_search_calls": FINALITY_MAX_VALUE_REQUESTS,
        "official_ui_calls": 1,
        "retry_count": 0,
        "official_ui_marker": ui_marker,
        "official_ui_landing_sha256": _sha(ui_path),
        "selected_rows": selected_rows,
        "responses": response_evidence,
        "next_provider_day_comparison": comparison,
        "normalized_writes": 0,
    }
    batches.append(batch)
    consistent_three = (
        len(batches) >= 3
        and all(
            batches[index]["selected_date"] > batches[index - 1]["selected_date"]
            and batches[index]["next_provider_day_comparison"]["status"] == "SAME"
            for index in range(len(batches) - 2, len(batches))
        )
    )
    state["status"] = (
        "THREE_BATCH_CONSISTENT_REVIEW_REQUIRED"
        if consistent_three else "PUBLICATION_FINALITY_UNKNOWN"
    )
    state["batch_count"] = len(batches)
    state["last_selected_date"] = target_date
    state["updated_at_utc"] = _now()
    _atomic_replace(state_path, state)
    checkpoint["status"] = "COMPLETE"
    checkpoint["selected_date"] = target_date
    checkpoint["state_sha256"] = _sha(state_path)
    checkpoint["comparison_status"] = comparison["status"]
    _atomic_replace(checkpoint_path, checkpoint)
    return {
        "status": "FINALITY_OBSERVATION_COMPLETE",
        "run_dir": str(run_dir),
        "statistic_search_calls": 0,
        "official_ui_calls": 0,
        "selected_date": target_date,
        "comparison_status": comparison["status"],
        "state_status": state["status"],
    }


def run_finality_observation(
    *, project_root: Path, metadata_summary_path: Path,
    approve_metadata_sha256: str, range_start_date: str | None = None,
    observation_kst: datetime | None = None, session=None,
) -> dict[str, object]:
    """Capture one predeclared KST-window finality batch, never promotion."""
    now_kst = observation_kst or datetime.now(SEOUL)
    if now_kst.tzinfo is None or now_kst.utcoffset() is None:
        raise PilotStopped("observation time must be timezone-aware")
    now_kst = now_kst.astimezone(SEOUL)
    if not FINALITY_WINDOW_START_HOUR_KST <= now_kst.hour < FINALITY_WINDOW_END_HOUR_KST:
        raise PilotStopped("outside the predeclared 17:00-18:00 KST observation window")
    observation_date = now_kst.strftime("%Y%m%d")
    state_path = project_root / FINALITY_STATE_RELATIVE
    state = _finality_state(state_path)
    root = project_root / FINALITY_LANDING_RELATIVE
    for batch in state["batches"]:
        if batch.get("observation_date_kst") == observation_date:
            run_id = batch.get("run_id")
            if not isinstance(run_id, str) or not run_id:
                raise PilotStopped("retained finality batch run identity is invalid")
            run_dir = root / f"observation_{observation_date}_{run_id}"
            if not run_dir.is_dir() or run_dir.is_symlink():
                raise PilotStopped("retained finality batch Landing is unavailable")
            return _finalize_finality_run(
                project_root=project_root,
                run_dir=run_dir,
                metadata_summary_path=metadata_summary_path,
                approve_metadata_sha256=approve_metadata_sha256,
            )
    retained_runs = sorted(root.glob(f"observation_{observation_date}_*")) if root.exists() else []
    if len(retained_runs) > 1:
        raise PilotStopped("multiple retained finality runs exist for one observation window")
    if retained_runs:
        return _finalize_finality_run(
            project_root=project_root,
            run_dir=retained_runs[0],
            metadata_summary_path=metadata_summary_path,
            approve_metadata_sha256=approve_metadata_sha256,
        )
    previous = state["batches"][-1] if state["batches"] else None
    if previous is not None:
        previous_target = str(previous.get("selected_date", ""))
        if range_start_date is not None and range_start_date != previous_target:
            raise PilotStopped("range start must equal the previous provider-native date")
        range_start_date = previous_target
    if range_start_date is None:
        raise PilotStopped("the first observation requires a predeclared range start date")
    range_end_date = observation_date
    config = load_finality_config(
        metadata_summary_path, approve_sha256=approve_metadata_sha256,
    )
    scopes = plan_finality_scopes(
        config, start_date=range_start_date, end_date=range_end_date,
    )
    key = os.environ.get(API_KEY_ENV, "")
    if not key:
        raise PilotStopped(f"{API_KEY_ENV} is required in the process environment")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex
    run_dir = root / f"observation_{observation_date}_{run_id}"
    run_dir.mkdir(parents=True, exist_ok=False)
    checkpoint = {
        "version": 1,
        "status": "IN_PROGRESS",
        "run_id": run_id,
        "observation_date_kst": observation_date,
        "observation_window_kst": "17:00-18:00",
        "captured_at_utc": now_kst.astimezone(timezone.utc).isoformat(),
        "range_start_date": range_start_date,
        "range_end_date": range_end_date,
        "metadata_summary_sha256": approve_metadata_sha256,
        "max_statistic_search_calls": FINALITY_MAX_VALUE_REQUESTS,
        "retry_budget": 0,
        "normalized_writes": 0,
    }
    _atomic_replace(run_dir / "checkpoint.json", checkpoint)
    ledger = Ledger(run_dir / "observation_ledger.jsonl", secrets=(key,))
    transport = session or requests.Session()
    with _lock(root, run_id):
        ledger.append(
            "RUN_CREATED", run_id=run_id, observation_date_kst=observation_date,
            range_start_date=range_start_date, range_end_date=range_end_date,
            max_statistic_search_calls=FINALITY_MAX_VALUE_REQUESTS, retry_budget=0,
        )
        ui_landing = run_dir / "response_00_official_ui_table_info.json"
        try:
            response = transport.post(
                FINALITY_UI_URL,
                json=_finality_ui_request_body(now_kst),
                timeout=TIMEOUT_SECONDS,
            )
        except requests.RequestException as error:
            raise PilotStopped("official UI request failed with retry zero") from error
        ui_body = _sanitized_ui_body(bytes(response.content))
        _immutable_bytes(ui_landing, ui_body)
        ledger.append(
            "UI_RESPONSE", operation=FINALITY_UI_TRANSACTION,
            route="/serviceEndpoint/httpService/request.json",
            status_code=int(response.status_code), response_bytes=len(ui_body),
            response_sha256=_sha(ui_landing), sanitized_fields=["header.ipAddr"],
        )
        if response.status_code != 200:
            raise PilotStopped(f"official UI HTTP {response.status_code}")
        parse_finality_ui_marker(ui_body)
        for sequence, scope in enumerate(scopes, start=1):
            landing = run_dir / f"response_{sequence:02d}_{scope.scope_id}.json"
            try:
                response = transport.get(
                    finality_value_url(key, config, scope), timeout=TIMEOUT_SECONDS,
                )
            except requests.RequestException as error:
                raise PilotStopped("StatisticSearch request failed with retry zero") from error
            body = bytes(response.content)
            if key.encode("utf-8") in body:
                raise PilotStopped("StatisticSearch response contains the credential")
            _immutable_bytes(landing, body)
            ledger.append(
                "VALUE_RESPONSE", sequence=sequence, operation=VALUE_OPERATION,
                scope=scope.scope_id, route=finality_redacted_route(config, scope),
                status_code=int(response.status_code), response_bytes=len(body),
                response_sha256=_sha(landing),
            )
            if response.status_code != 200:
                raise PilotStopped(f"ECOS HTTP {response.status_code}")
            parse_finality_value(body, config, scope)
        checkpoint["status"] = "CAPTURED"
        _atomic_replace(run_dir / "checkpoint.json", checkpoint)
    result = _finalize_finality_run(
        project_root=project_root,
        run_dir=run_dir,
        metadata_summary_path=metadata_summary_path,
        approve_metadata_sha256=approve_metadata_sha256,
    )
    result["statistic_search_calls"] = FINALITY_MAX_VALUE_REQUESTS
    result["official_ui_calls"] = 1
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bounded BOK ECOS Treasury diagnostic pilot")
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--config", type=Path)
    parser.add_argument(
        "--phase", choices=("metadata", "values", "finality-observation"),
        required=True,
    )
    parser.add_argument("--metadata-run-dir", type=Path)
    parser.add_argument("--metadata-summary", type=Path)
    parser.add_argument("--approve-metadata-sha256")
    parser.add_argument("--resume-run-dir", type=Path)
    parser.add_argument("--range-start-date")
    parser.add_argument("--confirm-live-manual-pilot", action="store_true")
    parser.add_argument("--confirm-live-finality-observation", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.phase == "finality-observation":
            if not args.confirm_live_finality_observation:
                raise PilotStopped(
                    "Refusing to run: --confirm-live-finality-observation is required"
                )
            if args.metadata_summary is None or args.approve_metadata_sha256 is None:
                raise PilotStopped(
                    "finality observation requires retained metadata summary and SHA-256"
                )
            if args.config or args.metadata_run_dir or args.resume_run_dir:
                raise PilotStopped("finality observation rejects pilot config/run arguments")
            load_dotenv(args.project_root.resolve() / ".env", override=False)
            result = run_finality_observation(
                project_root=args.project_root.resolve(),
                metadata_summary_path=args.metadata_summary.resolve(),
                approve_metadata_sha256=args.approve_metadata_sha256,
                range_start_date=args.range_start_date,
            )
        elif args.phase == "metadata":
            if not args.confirm_live_manual_pilot:
                raise PilotStopped("Refusing to run: --confirm-live-manual-pilot is required")
            if args.config is None:
                raise PilotStopped("metadata phase requires --config")
            if args.metadata_run_dir or args.approve_metadata_sha256 or args.resume_run_dir:
                raise PilotStopped("metadata phase rejects value/resume arguments")
            result = run_metadata(
                project_root=args.project_root.resolve(), config_path=args.config.resolve()
            )
        else:
            if not args.confirm_live_manual_pilot:
                raise PilotStopped("Refusing to run: --confirm-live-manual-pilot is required")
            if args.config is None:
                raise PilotStopped("values phase requires --config")
            if args.metadata_run_dir is None or args.approve_metadata_sha256 is None:
                raise PilotStopped("values phase requires reviewed metadata directory and SHA-256")
            result = run_values(
                project_root=args.project_root.resolve(), config_path=args.config.resolve(),
                metadata_run_dir=args.metadata_run_dir,
                approve_metadata_sha256=args.approve_metadata_sha256,
                resume_run_dir=args.resume_run_dir,
            )
    except (EcosPilotError, requests.RequestException) as error:
        key = os.environ.get(API_KEY_ENV, "")
        message = str(error).replace(key, "<redacted>") if key else str(error)
        raise SystemExit(message) from error
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
