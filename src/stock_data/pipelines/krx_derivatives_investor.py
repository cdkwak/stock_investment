from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, timedelta
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Callable, Mapping
from uuid import uuid4


SOURCE_START = date(1999, 4, 26)
SOURCE_OPERATION = "KRX_BASIC_STATISTICS_15007_DAILY_TREND"
SESSIONS = ("ALL", "REGULAR", "NIGHT")
OPTION_RIGHTS = ("ALL", "CALL", "PUT")
MEASURES = ("volume", "trading_value")
SIDES = ("sell", "buy", "net_buy")


class KrxInvestorCollectionStopped(RuntimeError):
    pass


@dataclass(frozen=True)
class RequestSpec:
    product: str
    option_right: str
    session: str
    measure: str
    side: str
    start_date: str
    end_date: str
    volume_unit_source: str
    trading_value_unit_source: str

    @property
    def request_id(self) -> str:
        raw = json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]


@dataclass(frozen=True)
class ResponseEnvelope:
    status_code: int
    headers: Mapping[str, str]
    body: bytes


def _end_before_two_year_anniversary(start: date, final: date) -> date:
    try:
        anniversary = start.replace(year=start.year + 2)
    except ValueError:
        anniversary = start.replace(year=start.year + 2, day=28)
    return min(final, anniversary - timedelta(days=1))


def coverage_chunks(end_date: date, *, start_date: date = SOURCE_START) -> tuple[tuple[date, date], ...]:
    if start_date < SOURCE_START:
        raise ValueError("cannot plan synthetic pre-source coverage")
    if end_date < start_date:
        raise ValueError("end date precedes start date")
    chunks = []
    cursor = start_date
    while cursor <= end_date:
        chunk_end = _end_before_two_year_anniversary(cursor, end_date)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return tuple(chunks)


def build_request_plan(
    product: str,
    end_date: date,
    *,
    start_date: date = SOURCE_START,
    volume_unit_source: str = "계약",
    trading_value_unit_source: str = "백만원",
) -> tuple[RequestSpec, ...]:
    if product not in {"KOSPI200_FUTURES", "KOSPI200_OPTIONS"}:
        raise ValueError("unsupported product")
    rights = ("NA",) if product == "KOSPI200_FUTURES" else OPTION_RIGHTS
    plan = []
    for chunk_start, chunk_end in coverage_chunks(end_date, start_date=start_date):
        for option_right in rights:
            for session in SESSIONS:
                for measure in MEASURES:
                    for side in SIDES:
                        plan.append(RequestSpec(
                            product=product,
                            option_right=option_right,
                            session=session,
                            measure=measure,
                            side=side,
                            start_date=chunk_start.isoformat(),
                            end_date=chunk_end.isoformat(),
                            volume_unit_source=volume_unit_source,
                            trading_value_unit_source=trading_value_unit_source,
                        ))
    return tuple(plan)


def bounded_pilot_plan(product: str) -> tuple[RequestSpec, ...]:
    """Return one source call: retry-zero, seven calendar days, ALL scope."""
    plan = build_request_plan(product, date(1999, 5, 2), start_date=SOURCE_START)
    return tuple(
        spec for spec in plan
        if spec.option_right in {"NA", "ALL"}
        and spec.session == "ALL"
        and spec.measure == "volume"
        and spec.side == "sell"
    )


def _atomic_new(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise KrxInvestorCollectionStopped(f"refusing overwrite: {path.name}")
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_json(path: Path, payload: object) -> None:
    encoded = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _append_ledger(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, encoded)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _classify(response: ResponseEnvelope) -> tuple[str, int]:
    if response.status_code in {403, 429}:
        raise KrxInvestorCollectionStopped(f"restriction HTTP {response.status_code}")
    if response.status_code != 200:
        raise KrxInvestorCollectionStopped(f"unexpected HTTP {response.status_code}")
    # KRX can label successful JSON as text/html; classify the retained bytes.
    if response.body.lstrip().startswith(b"<"):
        raise KrxInvestorCollectionStopped("restriction/auth HTML response")
    try:
        payload = json.loads(response.body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise KrxInvestorCollectionStopped("non-JSON source response") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("OutBlock_1"), list):
        raise KrxInvestorCollectionStopped("unexpected source schema")
    rows = payload["OutBlock_1"]
    return ("SUCCESS" if rows else "VALID_EMPTY", len(rows))


def collect_landing_serial(
    plan: tuple[RequestSpec, ...],
    *,
    run_dir: Path,
    request_fn: Callable[[RequestSpec], ResponseEnvelope],
    minimum_interval_seconds: float = 5.0,
    sleep_fn: Callable[[float], None] = time.sleep,
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> dict[str, object]:
    """Execute an injected authenticated transport serially with retry zero.

    This deliberately does not know credentials, cookies, or undocumented request
    parameter codes. The reviewed transport adapter must map RequestSpec to KRX.
    Every response is Landing-preserved before classification.
    """
    if not plan:
        raise ValueError("empty plan")
    run_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = run_dir / "checkpoint.json"
    ledger_path = run_dir / "request_ledger.jsonl"
    plan_hash = hashlib.sha256(json.dumps(
        [asdict(spec) for spec in plan], sort_keys=True, separators=(",", ":")
    ).encode("utf-8")).hexdigest()
    if checkpoint_path.exists():
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if checkpoint.get("plan_sha256") != plan_hash:
            raise KrxInvestorCollectionStopped("resume plan differs")
    else:
        checkpoint = {"plan_sha256": plan_hash, "status": "CREATED", "completed": {}}
        _atomic_json(checkpoint_path, checkpoint)
    completed = checkpoint.get("completed")
    if not isinstance(completed, dict):
        raise KrxInvestorCollectionStopped("invalid checkpoint")
    for record in completed.values():
        if not isinstance(record, dict):
            raise KrxInvestorCollectionStopped("invalid completed record")
        body_file, expected_hash = record.get("body_file"), record.get("body_sha256")
        path = run_dir / str(body_file)
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
            raise KrxInvestorCollectionStopped("Landing/checkpoint mismatch")
    last_started = None
    for sequence, spec in enumerate(plan, 1):
        if spec.request_id in completed:
            continue
        path = run_dir / f"response_{sequence:04d}_{spec.request_id}.json"
        if path.exists():
            raise KrxInvestorCollectionStopped("orphan Landing response requires audit")
        if last_started is not None:
            remaining = minimum_interval_seconds - (monotonic_fn() - last_started)
            if remaining > 0:
                sleep_fn(remaining)
        last_started = monotonic_fn()
        _append_ledger(ledger_path, {
            "event": "REQUEST_STARTED", "sequence": sequence,
            "request_id": spec.request_id, "scope": asdict(spec), "retry": 0,
        })
        try:
            response = request_fn(spec)
        except Exception as error:
            checkpoint.update(status="STOPPED", stop_reason=f"transport:{type(error).__name__}")
            _atomic_json(checkpoint_path, checkpoint)
            _append_ledger(ledger_path, {
                "event": "REQUEST_STOPPED", "sequence": sequence,
                "request_id": spec.request_id, "reason": f"transport:{type(error).__name__}",
            })
            raise KrxInvestorCollectionStopped("transport stopped; retry zero") from error
        _atomic_new(path, response.body)
        body_hash = hashlib.sha256(response.body).hexdigest()
        try:
            classification, row_count = _classify(response)
        except Exception as error:
            checkpoint.update(status="STOPPED", stop_reason=str(error))
            _atomic_json(checkpoint_path, checkpoint)
            _append_ledger(ledger_path, {
                "event": "REQUEST_STOPPED", "sequence": sequence,
                "request_id": spec.request_id, "status_code": response.status_code,
                "body_sha256": body_hash, "body_file": path.name,
                "reason": str(error),
            })
            raise
        completed[spec.request_id] = {
            "sequence": sequence, "body_file": path.name, "body_sha256": body_hash,
            "classification": classification, "rows": row_count,
        }
        checkpoint["status"] = "IN_PROGRESS"
        _atomic_json(checkpoint_path, checkpoint)
        _append_ledger(ledger_path, {
            "event": "REQUEST_COMPLETED", "sequence": sequence,
            "request_id": spec.request_id, "status_code": response.status_code,
            "body_sha256": body_hash, "body_file": path.name,
            "classification": classification, "rows": row_count,
        })
    checkpoint["status"] = "COMPLETE"
    _atomic_json(checkpoint_path, checkpoint)
    return {"status": "COMPLETE", "completed_requests": len(completed), "plan_sha256": plan_hash}
