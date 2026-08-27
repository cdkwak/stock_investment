"""Run one calendar-gated UR-246 Toss domestic 30-minute boundary."""
from __future__ import annotations
import argparse
import json
import os
import time as clock_time
from contextlib import contextmanager
from datetime import datetime, time, timezone
from pathlib import Path
from typing import BinaryIO, Callable, Iterator
from uuid import uuid4
from zoneinfo import ZoneInfo

from stock_data.orchestration.exchange_calendar import (
    ExchangeMarket,
    ExchangeTradingCalendar,
)
from stock_data.orchestration.toss_domestic_ur246 import TossDomesticTransport, runner
from stock_data.providers.tossinvest import TossInvestClient


KST = ZoneInfo("Asia/Seoul")
_VERIFIED_COLLECTION_START = time(9, 0)
_VERIFIED_COLLECTION_END = time(15, 30)

_OCCURRENCE_ROOT = Path(
    "data/state/provider_scheduler/toss_domestic_ur246_occurrences"
)
_LAST_OCCURRENCE_POINTER = Path(
    "data/state/provider_scheduler/toss_domestic_ur246_last.json"
)
_LAST_OCCURRENCE_LOCK = Path(
    "data/state/provider_scheduler/toss_domestic_ur246_last.lock"
)
_RECEIPT_TEMP_ROOT = Path(".tmp/agents/toss-domestic-ur246")
_SAFE_IDENTITY_SLOTS = {
    "000660": "DOMESTIC_ROUTE_1",
    "005930": "DOMESTIC_ROUTE_2",
    "KOSPI": "DOMESTIC_ROUTE_3",
    "KOSDAQ": "DOMESTIC_ROUTE_4",
}
_IDENTITY_BY_SAFE_SLOT = {
    slot: identity for identity, slot in _SAFE_IDENTITY_SLOTS.items()
}
_OPERATION_SLOT = "OPERATION"

_SCHEDULER_SUCCESS_STATUSES = frozenset({
    "CALENDAR_OR_WINDOW_INELIGIBLE_API_ZERO",
    "COMPLETE",
    "NO_REPEAT",
})
_ALLOWED_OUTCOME_CODES = frozenset({
    *_SCHEDULER_SUCCESS_STATUSES,
    "PROCESS_LOCKED",
    "NO_TRANSPORT_ADAPTER",
    "ORPHANED_NO_REPEAT",
    "COMPLETE_SEMANTIC_FAILURE",
    "COMPLETE_TRANSPORT_FAILURE",
    "FAIL_RESULT_CONTRACT",
    "FAIL_CLAIM_INCOMPLETE",
    "FAIL_RECEIPT_INVALID",
    "FAIL_RUNTIME",
})
_ALLOWED_FAILURE_REASONS = frozenset({
    "NONE",
    "CLAIM_INCOMPLETE",
    "OPERATION_OUTCOME_FAILURE",
    "RECEIPT_VALIDATION_FAILURE",
    "RESULT_CONTRACT_FAILURE",
    "RUNTIME_FAILURE",
})
_CLAIM_KEYS = frozenset({
    "schema_version", "operation_id", "receipt_kind", "scheduled_for",
    "claimed_at_utc",
})
_TERMINAL_KEYS = frozenset({
    "schema_version", "operation_id", "receipt_kind", "scheduled_for",
    "classification", "terminal_status", "terminal_exit_code",
    "finished_at_utc", "outcomes", "oauth_calls", "business_calls",
    "failure_reason",
})


class OccurrenceReceiptError(RuntimeError):
    """A local scheduler receipt failed its closed schema/readback boundary."""


def _scheduled_occurrence(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("timezone-aware clock required")
    local = now.astimezone(KST)
    minute = 0 if local.minute < 30 else 30
    return local.replace(minute=minute, second=0, microsecond=0)


def _receipt_token(scheduled_for: datetime) -> str:
    return scheduled_for.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _claim_path(root: Path, scheduled_for: datetime) -> Path:
    return Path(root) / _OCCURRENCE_ROOT / f"{_receipt_token(scheduled_for)}.claim.json"


def _terminal_path(root: Path, scheduled_for: datetime) -> Path:
    return Path(root) / _OCCURRENCE_ROOT / f"{_receipt_token(scheduled_for)}.json"


def _json_bytes(payload: dict[str, object]) -> bytes:
    return json.dumps(
        payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8") + b"\n"


def _publish_immutable_json(
    root: Path, path: Path, payload: dict[str, object],
) -> bool:
    """Publish once via a same-volume hard link from the allowed temp root."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_root = Path(root) / _RECEIPT_TEMP_ROOT
    temporary_root.mkdir(parents=True, exist_ok=True)
    temporary = temporary_root / f"{path.name}.{uuid4().hex}.tmp"
    body = _json_bytes(payload)
    try:
        with temporary.open("xb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            return False
        if path.read_bytes() != body:
            raise OccurrenceReceiptError("immutable occurrence readback differs")
        return True
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _atomic_pointer(
    root: Path, path: Path, payload: dict[str, object],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_root = Path(root) / _RECEIPT_TEMP_ROOT
    temporary_root.mkdir(parents=True, exist_ok=True)
    temporary = temporary_root / f"{path.name}.{uuid4().hex}.tmp"
    body = _json_bytes(payload)
    try:
        with temporary.open("xb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if path.read_bytes() != body:
            raise OccurrenceReceiptError("last occurrence pointer readback differs")
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _lock_stream(stream: BinaryIO) -> None:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        return
    import fcntl

    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_stream(stream: BinaryIO) -> None:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@contextmanager
def _last_pointer_lock(
    root: Path, *, timeout_seconds: float = 5.0,
) -> Iterator[None]:
    """Serialize pointer compare-and-replace with a crash-released OS lock."""

    path = Path(root) / _LAST_OCCURRENCE_LOCK
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        stream = path.open("a+b")
    except OSError as error:
        raise OccurrenceReceiptError(
            "last occurrence pointer lock is unavailable"
        ) from error
    with stream:
        try:
            if stream.seek(0, os.SEEK_END) == 0:
                stream.write(b"\0")
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as error:
            raise OccurrenceReceiptError(
                "last occurrence pointer lock is unavailable"
            ) from error
        deadline = clock_time.monotonic() + timeout_seconds
        while True:
            try:
                _lock_stream(stream)
                break
            except OSError:
                if clock_time.monotonic() >= deadline:
                    raise OccurrenceReceiptError(
                        "last occurrence pointer lock timed out"
                    ) from None
                clock_time.sleep(0.01)
        try:
            yield
        finally:
            try:
                _unlock_stream(stream)
            except OSError as error:
                raise OccurrenceReceiptError(
                    "last occurrence pointer unlock failed"
                ) from error


def _safe_outcomes(statuses: object) -> dict[str, str]:
    if not isinstance(statuses, dict) or not statuses:
        raise OccurrenceReceiptError("occurrence outcomes are missing")
    safe: dict[str, str] = {}
    for identity, outcome in statuses.items():
        if identity == "operation":
            slot = _OPERATION_SLOT
        elif isinstance(identity, str) and identity in _SAFE_IDENTITY_SLOTS:
            slot = _SAFE_IDENTITY_SLOTS[identity]
        else:
            raise OccurrenceReceiptError("occurrence identity is outside the bounded set")
        if not isinstance(outcome, str) or outcome not in _ALLOWED_OUTCOME_CODES:
            raise OccurrenceReceiptError("occurrence outcome is outside the bounded set")
        if slot in safe:
            raise OccurrenceReceiptError("occurrence safe slot is duplicated")
        safe[slot] = outcome
    return dict(sorted(safe.items()))


def _public_outcomes(outcomes: dict[str, str]) -> dict[str, str]:
    if set(outcomes) == {_OPERATION_SLOT}:
        return {"operation": outcomes[_OPERATION_SLOT]}
    if set(outcomes) != set(_IDENTITY_BY_SAFE_SLOT):
        raise OccurrenceReceiptError("occurrence safe slots differ")
    return {
        _IDENTITY_BY_SAFE_SLOT[slot]: outcome
        for slot, outcome in sorted(outcomes.items())
    }


def _aware_timestamp(value: object, *, field: str) -> datetime:
    if not isinstance(value, str):
        raise OccurrenceReceiptError(f"{field} is not a timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise OccurrenceReceiptError(f"{field} is not a timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise OccurrenceReceiptError(f"{field} is not timezone-aware")
    return parsed


def _validate_terminal(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict) or set(payload) != _TERMINAL_KEYS:
        raise OccurrenceReceiptError("terminal occurrence schema differs")
    if (
        payload["schema_version"] != 1
        or payload["operation_id"] != "UR-246"
        or payload["receipt_kind"] != "TERMINAL"
        or payload["classification"] not in {"ELIGIBLE", "INELIGIBLE"}
        or payload["terminal_status"] not in {
            "TERMINAL_SUCCESS", "TERMINAL_FAILURE",
        }
        or payload["terminal_exit_code"] not in {0, 1}
        or payload["failure_reason"] not in _ALLOWED_FAILURE_REASONS
    ):
        raise OccurrenceReceiptError("terminal occurrence enum differs")
    scheduled = _aware_timestamp(payload["scheduled_for"], field="scheduled_for")
    finished = _aware_timestamp(payload["finished_at_utc"], field="finished_at_utc")
    if (
        scheduled.astimezone(KST).minute not in {0, 30}
        or scheduled.second != 0
        or scheduled.microsecond != 0
        or finished.utcoffset() != timezone.utc.utcoffset(None)
        or finished < scheduled.astimezone(timezone.utc)
    ):
        raise OccurrenceReceiptError("terminal occurrence time differs")
    if not isinstance(payload["outcomes"], dict):
        raise OccurrenceReceiptError("terminal occurrence outcomes differ")
    outcomes = _safe_outcomes(_public_outcomes(dict(payload["outcomes"])))
    if outcomes != payload["outcomes"]:
        raise OccurrenceReceiptError("terminal occurrence outcome mapping differs")
    oauth = payload["oauth_calls"]
    business = payload["business_calls"]
    if (
        isinstance(oauth, bool) or not isinstance(oauth, int) or not 0 <= oauth <= 1
        or isinstance(business, bool) or not isinstance(business, int)
        or not 0 <= business <= 4
    ):
        raise OccurrenceReceiptError("terminal occurrence call counts differ")
    expected_exit = _scheduler_exit_code(_public_outcomes(outcomes))
    expected_failure_reason = _failure_reason_for(outcomes)
    if (
        payload["terminal_exit_code"] != expected_exit
        or (payload["terminal_status"] == "TERMINAL_SUCCESS") != (expected_exit == 0)
        or payload["failure_reason"] != expected_failure_reason
    ):
        raise OccurrenceReceiptError("terminal occurrence status differs")
    if payload["classification"] == "INELIGIBLE":
        allowed_ineligible = {
            "CALENDAR_OR_WINDOW_INELIGIBLE_API_ZERO",
            "FAIL_RECEIPT_INVALID",
            "FAIL_RESULT_CONTRACT",
            "FAIL_RUNTIME",
        }
        if (
            set(outcomes) != {_OPERATION_SLOT}
            or outcomes[_OPERATION_SLOT] not in allowed_ineligible
        ):
            raise OccurrenceReceiptError("ineligible occurrence outcome differs")
        if oauth != 0 or business != 0:
            raise OccurrenceReceiptError("ineligible occurrence calls differ")
    if (
        payload["classification"] == "ELIGIBLE"
        and set(outcomes) == {_OPERATION_SLOT}
        and outcomes[_OPERATION_SLOT] == "CALENDAR_OR_WINDOW_INELIGIBLE_API_ZERO"
    ):
        raise OccurrenceReceiptError("eligible occurrence outcome differs")
    return payload


def _claim_payload(scheduled_for: datetime, claimed_at: datetime) -> dict[str, object]:
    if claimed_at.tzinfo is None or claimed_at.utcoffset() is None:
        raise ValueError("timezone-aware claim clock required")
    return {
        "schema_version": 1,
        "operation_id": "UR-246",
        "receipt_kind": "CLAIM",
        "scheduled_for": scheduled_for.isoformat(),
        "claimed_at_utc": claimed_at.astimezone(timezone.utc).isoformat(),
    }


def _validate_claim(payload: object, *, scheduled_for: datetime) -> dict[str, object]:
    if not isinstance(payload, dict) or set(payload) != _CLAIM_KEYS:
        raise OccurrenceReceiptError("occurrence claim schema differs")
    if (
        payload["schema_version"] != 1
        or payload["operation_id"] != "UR-246"
        or payload["receipt_kind"] != "CLAIM"
    ):
        raise OccurrenceReceiptError("occurrence claim enum differs")
    claimed_scheduled = _aware_timestamp(
        payload["scheduled_for"], field="scheduled_for",
    )
    claimed_at = _aware_timestamp(payload["claimed_at_utc"], field="claimed_at_utc")
    if (
        claimed_scheduled != scheduled_for
        or claimed_scheduled.astimezone(KST).minute not in {0, 30}
        or claimed_scheduled.second != 0
        or claimed_scheduled.microsecond != 0
        or claimed_at.utcoffset() != timezone.utc.utcoffset(None)
        or claimed_at < scheduled_for.astimezone(timezone.utc)
    ):
        raise OccurrenceReceiptError("occurrence claim time differs")
    return payload


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise OccurrenceReceiptError("occurrence JSON cannot be read") from error


def _validate_pointer(root: Path) -> dict[str, object] | None:
    path = Path(root) / _LAST_OCCURRENCE_POINTER
    if not path.exists():
        return None
    payload = _read_json(path)
    if not isinstance(payload, dict) or set(payload) != {*_TERMINAL_KEYS, "receipt_path"}:
        raise OccurrenceReceiptError("last occurrence pointer schema differs")
    receipt_path = payload["receipt_path"]
    terminal_payload = {
        key: value for key, value in payload.items() if key != "receipt_path"
    }
    terminal = _validate_terminal(terminal_payload)
    scheduled = _aware_timestamp(terminal["scheduled_for"], field="scheduled_for")
    expected = _terminal_path(root, scheduled).relative_to(root).as_posix()
    if receipt_path != expected:
        raise OccurrenceReceiptError("last occurrence pointer path differs")
    receipt = Path(root) / expected
    if not receipt.exists() or _validate_terminal(_read_json(receipt)) != terminal:
        raise OccurrenceReceiptError("last occurrence pointer receipt differs")
    return payload


def _update_last_pointer(
    root: Path, terminal_path: Path, terminal: dict[str, object],
) -> None:
    with _last_pointer_lock(root):
        terminal = _validate_terminal(terminal)
        current_time = _aware_timestamp(
            terminal["scheduled_for"], field="scheduled_for",
        )
        expected_terminal_path = _terminal_path(root, current_time)
        if Path(terminal_path) != expected_terminal_path:
            raise OccurrenceReceiptError(
                "last occurrence terminal path is not canonical"
            )
        if (
            not expected_terminal_path.exists()
            or _validate_terminal(_read_json(expected_terminal_path)) != terminal
        ):
            raise OccurrenceReceiptError(
                "last occurrence terminal differs from immutable receipt"
            )
        previous = _validate_pointer(root)
        pointer = dict(terminal)
        pointer["receipt_path"] = expected_terminal_path.relative_to(root).as_posix()
        if previous is not None:
            previous_time = _aware_timestamp(
                previous["scheduled_for"], field="scheduled_for",
            )
            if current_time < previous_time:
                return
            if current_time == previous_time:
                if previous != pointer:
                    raise OccurrenceReceiptError(
                        "equal occurrence pointer conflicts with retained receipt"
                    )
                return
        _atomic_pointer(root, Path(root) / _LAST_OCCURRENCE_POINTER, pointer)


def _failure_result(
    scheduled_for: datetime, *, outcome: str,
) -> dict[str, object]:
    return {
        "date_kst": scheduled_for.astimezone(KST).date().isoformat(),
        "window_id": scheduled_for.isoformat(),
        "statuses": {"operation": outcome},
        "oauth_calls": 0,
        "business_calls": 0,
        "scheduled_for": scheduled_for.isoformat(),
        "occurrence_status": "FAIL_CLOSED_API_ZERO",
        "terminal_exit_code": 1,
        "replayed": False,
    }


def _validate_call_count(value: object, *, cap: int, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= cap:
        raise OccurrenceReceiptError(f"{field} differs")
    return value


def _transport_counts(
    transports: list[TossDomesticTransport], result: object | None = None,
) -> tuple[int, int]:
    if not transports and result is None:
        return 0, 0
    source = transports[-1] if transports else result
    counts = (
        _validate_call_count(
            getattr(source, "oauth_calls", None), cap=1, field="oauth_calls",
        ),
        _validate_call_count(
            getattr(source, "business_calls", None), cap=4, field="business_calls",
        ),
    )
    if transports and result is not None:
        result_counts = (
            _validate_call_count(
                getattr(result, "oauth_calls", None), cap=1, field="oauth_calls",
            ),
            _validate_call_count(
                getattr(result, "business_calls", None), cap=4,
                field="business_calls",
            ),
        )
        if result_counts != counts:
            raise OccurrenceReceiptError("result and transport call counts differ")
    return counts


def _terminal_payload(
    *,
    scheduled_for: datetime,
    classification: str,
    statuses: object,
    oauth_calls: int,
    business_calls: int,
    finished_at: datetime,
    failure_reason: str | None = None,
) -> dict[str, object]:
    safe_outcomes = _safe_outcomes(statuses)
    public_outcomes = _public_outcomes(safe_outcomes)
    exit_code = _scheduler_exit_code(public_outcomes)
    if finished_at.tzinfo is None or finished_at.utcoffset() is None:
        raise OccurrenceReceiptError("finished_at clock is not timezone-aware")
    expected_failure_reason = _failure_reason_for(safe_outcomes)
    if failure_reason is None:
        failure_reason = expected_failure_reason
    elif failure_reason != expected_failure_reason:
        raise OccurrenceReceiptError("terminal failure reason differs")
    terminal = {
        "schema_version": 1,
        "operation_id": "UR-246",
        "receipt_kind": "TERMINAL",
        "scheduled_for": scheduled_for.isoformat(),
        "classification": classification,
        "terminal_status": (
            "TERMINAL_SUCCESS" if exit_code == 0 else "TERMINAL_FAILURE"
        ),
        "terminal_exit_code": exit_code,
        "finished_at_utc": finished_at.astimezone(timezone.utc).isoformat(),
        "outcomes": safe_outcomes,
        "oauth_calls": _validate_call_count(
            oauth_calls, cap=1, field="oauth_calls",
        ),
        "business_calls": _validate_call_count(
            business_calls, cap=4, field="business_calls",
        ),
        "failure_reason": failure_reason,
    }
    return _validate_terminal(terminal)


def _failure_reason_for(outcomes: dict[str, str]) -> str:
    if _scheduler_exit_code(_public_outcomes(outcomes)) == 0:
        return "NONE"
    if outcomes == {_OPERATION_SLOT: "FAIL_CLAIM_INCOMPLETE"}:
        return "CLAIM_INCOMPLETE"
    if outcomes == {_OPERATION_SLOT: "FAIL_RECEIPT_INVALID"}:
        return "RECEIPT_VALIDATION_FAILURE"
    if outcomes == {_OPERATION_SLOT: "FAIL_RESULT_CONTRACT"}:
        return "RESULT_CONTRACT_FAILURE"
    if outcomes == {_OPERATION_SLOT: "FAIL_RUNTIME"}:
        return "RUNTIME_FAILURE"
    return "OPERATION_OUTCOME_FAILURE"


def _result_from_terminal(
    root: Path, terminal_path: Path, terminal: dict[str, object], *, replayed: bool,
) -> dict[str, object]:
    scheduled = _aware_timestamp(terminal["scheduled_for"], field="scheduled_for")
    eligible = terminal["classification"] == "ELIGIBLE"
    return {
        "date_kst": scheduled.astimezone(KST).date().isoformat(),
        "window_id": scheduled.isoformat() if eligible else None,
        "statuses": _public_outcomes(dict(terminal["outcomes"])),
        "oauth_calls": 0 if replayed else terminal["oauth_calls"],
        "business_calls": 0 if replayed else terminal["business_calls"],
        "scheduled_for": terminal["scheduled_for"],
        "occurrence_classification": terminal["classification"],
        "occurrence_status": terminal["terminal_status"],
        "terminal_exit_code": terminal["terminal_exit_code"],
        "finished_at_utc": terminal["finished_at_utc"],
        "occurrence_receipt": terminal_path.relative_to(root).as_posix(),
        "replayed": replayed,
    }


class _RuntimeTransport(TossDomesticTransport):
    def __init__(self, root: Path) -> None:
        self._client = TossInvestClient.from_environment(project_root=root, connect_timeout=10, read_timeout=10)
    @property
    def oauth_calls(self) -> int: return self._client.token_request_count
    @property
    def business_calls(self) -> int: return self._client.market_request_count
    def stock(self, symbol: str): return self._client.get_market_data("/api/v1/prices", params={"symbols": symbol}).payload
    def index(self, symbol: str): return self._client.get_market_data("/api/v1/market-indicators/prices", params={"symbols": symbol}).payload


def _collection_eligible(now: datetime) -> bool:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("timezone-aware clock required")
    local = now.astimezone(KST)
    if not ExchangeTradingCalendar(ExchangeMarket.KR).is_trading_day(local.date()):
        return False
    return _VERIFIED_COLLECTION_START <= local.time() < _VERIFIED_COLLECTION_END


def run(
    root: Path,
    *,
    now: datetime | None = None,
    transport_factory: Callable[[], TossDomesticTransport] | None = None,
    finished_clock: Callable[[], datetime] | None = None,
) -> dict[str, object]:
    root = Path(root)
    observed_at = now or datetime.now(timezone.utc)
    scheduled_for = _scheduled_occurrence(observed_at)
    claim_path = _claim_path(root, scheduled_for)
    terminal_path = _terminal_path(root, scheduled_for)

    if terminal_path.exists():
        try:
            terminal = _validate_terminal(_read_json(terminal_path))
            if _aware_timestamp(
                terminal["scheduled_for"], field="scheduled_for",
            ) != scheduled_for:
                raise OccurrenceReceiptError("terminal occurrence identity differs")
            _update_last_pointer(root, terminal_path, terminal)
            return _result_from_terminal(
                root, terminal_path, terminal, replayed=True,
            )
        except OccurrenceReceiptError:
            return _failure_result(scheduled_for, outcome="FAIL_RECEIPT_INVALID")

    classification = "ELIGIBLE" if _collection_eligible(observed_at) else "INELIGIBLE"
    claim = _claim_payload(scheduled_for, observed_at)
    try:
        created_claim = _publish_immutable_json(root, claim_path, claim)
        if not created_claim:
            _validate_claim(_read_json(claim_path), scheduled_for=scheduled_for)
            if terminal_path.exists():
                terminal = _validate_terminal(_read_json(terminal_path))
                _update_last_pointer(root, terminal_path, terminal)
                return _result_from_terminal(
                    root, terminal_path, terminal, replayed=True,
                )
            return _failure_result(scheduled_for, outcome="FAIL_CLAIM_INCOMPLETE")
        _validate_claim(_read_json(claim_path), scheduled_for=scheduled_for)
    except OccurrenceReceiptError:
        return _failure_result(scheduled_for, outcome="FAIL_RECEIPT_INVALID")

    transports: list[TossDomesticTransport] = []
    result: object | None = None
    statuses: object
    failure_reason: str | None = None
    try:
        _validate_pointer(root)
    except OccurrenceReceiptError:
        statuses = {"operation": "FAIL_RECEIPT_INVALID"}
        oauth_calls, business_calls = 0, 0
        failure_reason = "RECEIPT_VALIDATION_FAILURE"
    else:
        if classification == "INELIGIBLE":
            statuses = {"operation": "CALENDAR_OR_WINDOW_INELIGIBLE_API_ZERO"}
            oauth_calls, business_calls = 0, 0
        else:
            base_factory = transport_factory or (lambda: _RuntimeTransport(root))

            def tracked_transport_factory() -> TossDomesticTransport:
                transport = base_factory()
                transports.append(transport)
                return transport

            try:
                result = runner(root).run(
                    now=observed_at,
                    transport_factory=tracked_transport_factory,
                )
            except Exception:
                try:
                    oauth_calls, business_calls = _transport_counts(transports)
                except OccurrenceReceiptError:
                    oauth_calls, business_calls = 0, 0
                statuses = {"operation": "FAIL_RUNTIME"}
                failure_reason = "RUNTIME_FAILURE"
            else:
                try:
                    statuses = getattr(result, "statuses", None)
                    _safe_outcomes(statuses)
                    oauth_calls, business_calls = _transport_counts(
                        transports, result,
                    )
                except OccurrenceReceiptError:
                    try:
                        oauth_calls, business_calls = _transport_counts(transports)
                    except OccurrenceReceiptError:
                        oauth_calls, business_calls = 0, 0
                    statuses = {"operation": "FAIL_RESULT_CONTRACT"}
                    failure_reason = "RESULT_CONTRACT_FAILURE"

    try:
        finished_at = (finished_clock or (lambda: datetime.now(timezone.utc)))()
    except Exception:
        finished_at = datetime.now(timezone.utc)
        statuses = {"operation": "FAIL_RUNTIME"}
        failure_reason = "RUNTIME_FAILURE"

    try:
        terminal = _terminal_payload(
            scheduled_for=scheduled_for,
            classification=classification,
            statuses=statuses,
            oauth_calls=oauth_calls,
            business_calls=business_calls,
            finished_at=finished_at,
            failure_reason=failure_reason,
        )
    except OccurrenceReceiptError:
        terminal = _terminal_payload(
            scheduled_for=scheduled_for,
            classification=classification,
            statuses={"operation": "FAIL_RESULT_CONTRACT"},
            oauth_calls=0,
            business_calls=0,
            finished_at=datetime.now(timezone.utc),
            failure_reason="RESULT_CONTRACT_FAILURE",
        )

    try:
        if not _publish_immutable_json(root, terminal_path, terminal):
            terminal = _validate_terminal(_read_json(terminal_path))
        _update_last_pointer(root, terminal_path, terminal)
    except OccurrenceReceiptError:
        return _failure_result(scheduled_for, outcome="FAIL_RECEIPT_INVALID")
    return _result_from_terminal(root, terminal_path, terminal, replayed=False)


def _scheduler_exit_code(statuses: object) -> int:
    if not isinstance(statuses, dict) or not statuses:
        return 1
    return 0 if all(
        isinstance(status, str) and status in _SCHEDULER_SUCCESS_STATUSES
        for status in statuses.values()
    ) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--project-root", type=Path, required=True); parser.add_argument("--confirm-ur246-window", action="store_true"); args = parser.parse_args()
    if not args.confirm_ur246_window: parser.error("--confirm-ur246-window is required")
    result = run(args.project_root)
    print(result)
    terminal_exit = result.get("terminal_exit_code")
    if isinstance(terminal_exit, int) and not isinstance(terminal_exit, bool):
        return terminal_exit
    return _scheduler_exit_code(result.get("statuses"))
if __name__ == "__main__": raise SystemExit(main())
