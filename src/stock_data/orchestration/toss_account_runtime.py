from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from dotenv import dotenv_values

from stock_data.orchestration.toss_account_snapshot import (
    AccountRefreshTrigger,
    TossAccountRefreshResult,
    TossAccountSnapshotRefresher,
)
from stock_data.orchestration.account_privacy import (
    account_snapshot_lifecycle_lock,
    retain_positions_history,
)
from stock_data.providers.tossinvest import (
    TossInvestClient,
)


TOSS_ACCOUNT_CLIENT_ID_ENV = "TOSSINVEST_CLIENT_ID"
TOSS_ACCOUNT_CLIENT_SECRET_ENV = "TOSSINVEST_CLIENT_SECRET"
TOSS_ACCOUNT_SEQ_ENV = "TOSSINVEST_ACCOUNT_SEQ"
TOSS_ACCOUNT_REQUIRED_ENV_NAMES = (
    TOSS_ACCOUNT_CLIENT_ID_ENV,
    TOSS_ACCOUNT_CLIENT_SECRET_ENV,
    TOSS_ACCOUNT_SEQ_ENV,
)
TOSS_ACCOUNT_ENV_NAMES = TOSS_ACCOUNT_REQUIRED_ENV_NAMES
TOSS_ACCOUNT_DAILY_OPERATION = "TOSS_ACCOUNT_READONLY_DAILY"
TOSS_ACCOUNT_DAILY_SCHEMA_VERSION = 1
TOSS_ACCOUNT_DAILY_TIMEZONE = ZoneInfo("Asia/Seoul")
TOSS_ACCOUNT_DAILY_TIME = time(7, 0)
_CLAIM_KEYS = {
    "schema_version", "operation", "occurrence_date", "scheduled_for",
    "claimed_at_utc", "status",
}
_RECOVERY_KEYS = _CLAIM_KEYS | {
    "recovery_required_at_utc", "reason",
}
_TERMINAL_KEYS = _CLAIM_KEYS | {
    "finished_at_utc", "outcome", "reason", "token_calls", "account_calls",
    "normalized", "normalized_sha256",
}
_NORMALIZED_ACCOUNT_PATH = "data/normalized/toss_account_snapshot/latest.json"
_STATE_ACCOUNT_PATH = "data/state/toss_account_snapshot.json"
_LANDING_ACCOUNT_ROOT = "data/landing/tossinvest/account_snapshot"
_HISTORY_ACCOUNT_ROOT = "data/local/account_value_history/toss_self"
_POSITIONS_HISTORY_ACCOUNT_ROOT = "data/local/account_positions_history/toss_self"
_JOURNAL_ACCOUNT_ROOT = "data/state/transactions/toss_account_snapshot"


class TossAccountRuntimeState(str, Enum):
    ENABLED = "ENABLED"
    NOT_AVAILABLE_MISSING_CONFIG = "NOT_AVAILABLE_MISSING_CONFIG"
    NOT_AVAILABLE_INVALID_CONFIG = "NOT_AVAILABLE_INVALID_CONFIG"


@dataclass(frozen=True)
class TossAccountRuntimeWiring:
    state: TossAccountRuntimeState
    refresher: Callable[[AccountRefreshTrigger], TossAccountRefreshResult] | None
    missing_names: tuple[str, ...] = ()
    reason: str | None = None

    @property
    def enabled(self) -> bool:
        return self.state is TossAccountRuntimeState.ENABLED


def load_toss_account_environment(
    project_root: Path, process_environment: Mapping[str, str],
) -> dict[str, str]:
    """Load only the three approved Toss settings without mutating the process."""

    file_values = dotenv_values(
        project_root.resolve() / ".env", encoding="utf-8", interpolate=False,
    )
    result: dict[str, str] = {}
    for name in TOSS_ACCOUNT_ENV_NAMES:
        process_value = process_environment.get(name)
        selected = process_value if process_value is not None else file_values.get(name)
        result[name] = selected if isinstance(selected, str) else ""
    return result


def build_toss_account_runtime(
    project_root: Path,
    environment: Mapping[str, str],
    *,
    client_factory: Callable[..., TossInvestClient] = TossInvestClient,
) -> TossAccountRuntimeWiring:
    """Build the manual-only account refresher from three named variables.

    This function never loads ``.env`` and never places configuration values in
    its status, errors, logs, or return metadata.  The account selector is
    required by this app wiring so missing or ambiguous selection performs zero
    provider calls; provider account discovery remains available to lower-level
    explicitly bounded operations but is not used by desktop startup.
    """

    values = {name: environment.get(name, "") for name in TOSS_ACCOUNT_ENV_NAMES}
    missing = tuple(
        name for name in TOSS_ACCOUNT_REQUIRED_ENV_NAMES
        if not isinstance(values[name], str) or not values[name].strip()
    )
    if missing:
        return TossAccountRuntimeWiring(
            state=TossAccountRuntimeState.NOT_AVAILABLE_MISSING_CONFIG,
            refresher=None,
            missing_names=missing,
            reason="RUNTIME_CONFIG_REQUIRED",
        )

    selector_text = values[TOSS_ACCOUNT_SEQ_ENV].strip()
    if re.fullmatch(r"[1-9]\d*", selector_text) is None:
        return TossAccountRuntimeWiring(
            state=TossAccountRuntimeState.NOT_AVAILABLE_INVALID_CONFIG,
            refresher=None,
            reason="ACCOUNT_SELECTOR_INVALID",
        )
    account_seq = int(selector_text)

    try:
        client = client_factory(
            client_id=values[TOSS_ACCOUNT_CLIENT_ID_ENV],
            client_secret=values[TOSS_ACCOUNT_CLIENT_SECRET_ENV],
        )
        coordinator = TossAccountSnapshotRefresher(
            project_root=project_root,
            client=client,
            account_seq=account_seq,
            periodic_interval_seconds=60.0,
        )
    except Exception:
        return TossAccountRuntimeWiring(
            state=TossAccountRuntimeState.NOT_AVAILABLE_INVALID_CONFIG,
            refresher=None,
            reason="CLIENT_INITIALIZATION_FAILED",
        )
    root = project_root.resolve()

    def refresh(trigger: AccountRefreshTrigger) -> TossAccountRefreshResult:
        try:
            with account_snapshot_lifecycle_lock(root):
                result = coordinator.refresh(trigger)
                if (
                    result.status == "SUCCEEDED"
                    and result.account_calls == 3
                    and result.token_calls in {0, 1}
                    and result.normalized_path == _NORMALIZED_ACCOUNT_PATH
                ):
                    snapshot = json.loads(
                        (root / _NORMALIZED_ACCOUNT_PATH).read_text(encoding="utf-8")
                    )
                    retain_positions_history(root, "toss_self", snapshot)
                return result
        except TimeoutError:
            return TossAccountRefreshResult(
                "NOOP_CONCURRENT_REFRESH", trigger, 0,
                reason="CONCURRENT_REFRESH_IN_PROGRESS",
            )

    return TossAccountRuntimeWiring(
        state=TossAccountRuntimeState.ENABLED,
        refresher=refresh,
    )


class TossAccountScheduleError(RuntimeError):
    pass


class TossAccountRecoveryError(RuntimeError):
    """A claimed occurrence could not prove exact projection recovery."""


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise TossAccountScheduleError("scheduled account receipt has duplicate keys")
        result[key] = value
    return result


def _strict_json_object(raw: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw, object_pairs_hook=_reject_duplicate_pairs)
    except (json.JSONDecodeError, TossAccountScheduleError) as error:
        raise TossAccountScheduleError("scheduled account receipt is invalid") from error
    if not isinstance(payload, dict):
        raise TossAccountScheduleError("scheduled account receipt is not an object")
    return payload


def _aware_clock(value: object, *, field: str) -> datetime:
    if not isinstance(value, str):
        raise TossAccountScheduleError(f"scheduled account {field} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise TossAccountScheduleError(
            f"scheduled account {field} is invalid"
        ) from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise TossAccountScheduleError(f"scheduled account {field} is naive")
    return parsed


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(_json_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_bytes(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _direct_json_files(root: Path, relative_root: str) -> tuple[Path, ...]:
    boundary = (root / relative_root).resolve()
    try:
        boundary.relative_to(root)
    except ValueError as error:
        raise TossAccountScheduleError("account projection boundary differs") from error
    if not boundary.exists():
        return ()
    if not boundary.is_dir():
        raise TossAccountScheduleError("account projection boundary differs")
    files: list[Path] = []
    for path in sorted(boundary.glob("*.json")):
        resolved = path.resolve()
        if path.parent != boundary or resolved.parent != boundary or not path.is_file():
            raise TossAccountScheduleError("account projection boundary differs")
        files.append(path)
    return tuple(files)


def _projection_bytes(root: Path) -> dict[str, bytes]:
    """Capture only exact sanitized projection targets for fail-closed restore."""

    captured: dict[str, bytes] = {}
    for relative in (_NORMALIZED_ACCOUNT_PATH, _STATE_ACCOUNT_PATH):
        path = (root / relative).resolve()
        try:
            path.relative_to(root)
        except ValueError as error:
            raise TossAccountScheduleError("account projection boundary differs") from error
        if path.exists():
            if not path.is_file():
                raise TossAccountScheduleError("account projection boundary differs")
            captured[relative] = path.read_bytes()
    for relative_root in (
        _LANDING_ACCOUNT_ROOT, _HISTORY_ACCOUNT_ROOT,
        _POSITIONS_HISTORY_ACCOUNT_ROOT, _JOURNAL_ACCOUNT_ROOT,
    ):
        for path in _direct_json_files(root, relative_root):
            captured[path.relative_to(root).as_posix()] = path.read_bytes()
    return captured


def _verified_projection_digest(root: Path) -> str | None:
    """Return a digest only for a complete, journal-bound sanitized projection."""

    try:
        normalized = (root / _NORMALIZED_ACCOUNT_PATH).resolve()
        state_path = (root / _STATE_ACCOUNT_PATH).resolve()
        state = _strict_json_object(state_path.read_text(encoding="utf-8"))
        digest = state.get("payload_sha256")
        landing_relative = state.get("landing")
        if (
            state.get("schema_version") != 1
            or state.get("status") != "SUCCEEDED"
            or state.get("normalized") != _NORMALIZED_ACCOUNT_PATH
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            or not isinstance(landing_relative, str)
            or hashlib.sha256(normalized.read_bytes()).hexdigest() != digest
        ):
            return None
        landing = (root / landing_relative).resolve()
        landing_root = (root / _LANDING_ACCOUNT_ROOT).resolve()
        history_root = (root / _HISTORY_ACCOUNT_ROOT).resolve()
        if landing.parent != landing_root or not landing.is_file():
            return None
        landing_payload = _strict_json_object(landing.read_text(encoding="utf-8"))
        if (
            landing_payload.get("capture_kind") != "SANITIZED_CONTRACT_PROJECTION"
            or landing_payload.get("payload_sha256") != digest
            or hashlib.sha256(_json_bytes(landing_payload.get("snapshot"))).hexdigest() != digest
        ):
            return None
        for journal_path in _direct_json_files(root, _JOURNAL_ACCOUNT_ROOT):
            journal = _strict_json_object(journal_path.read_text(encoding="utf-8"))
            targets = journal.get("targets")
            history_relative = targets.get("history") if isinstance(targets, dict) else None
            if (
                journal.get("status") == "SUCCEEDED"
                and journal.get("payload_sha256") == digest
                and isinstance(targets, dict)
                and targets.get("normalized") == _NORMALIZED_ACCOUNT_PATH
                and targets.get("state") == _STATE_ACCOUNT_PATH
                and targets.get("landing") == landing_relative
                and isinstance(history_relative, str)
                and (root / history_relative).resolve().parent == history_root
                and (root / history_relative).resolve().is_file()
            ):
                return digest
    except (OSError, UnicodeError, TossAccountScheduleError, json.JSONDecodeError):
        return None
    return None


def _restore_projection_bytes_exact(root: Path, before: dict[str, bytes]) -> None:
    """Restore exact pre-refresh projection bytes; never touch another dataset."""

    try:
        current = _projection_bytes(root)
        for relative in sorted(set(current) - set(before)):
            (root / relative).unlink()
        for relative, value in before.items():
            _atomic_bytes(root / relative, value)
        if _projection_bytes(root) != before:
            raise TossAccountRecoveryError("account projection restore readback differs")
    except TossAccountRecoveryError:
        raise
    except BaseException as error:
        raise TossAccountRecoveryError(
            "account projection exact restore could not be verified"
        ) from error


def _projection_differs_from(root: Path, before: dict[str, bytes]) -> bool:
    """Compare a projection without allowing readback faults to terminalize."""

    try:
        return _projection_bytes(root) != before
    except BaseException as error:
        raise TossAccountRecoveryError(
            "account projection recovery readback could not be verified"
        ) from error


def _restore_projection_bytes(root: Path, before: dict[str, bytes]) -> None:
    """Indirection retained for fault injection around exact restore behavior."""

    _restore_projection_bytes_exact(root, before)


def _occurrence(now: datetime) -> tuple[date, datetime]:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("scheduled account refresh clock must be timezone-aware")
    local = now.astimezone(TOSS_ACCOUNT_DAILY_TIMEZONE)
    day = local.date()
    scheduled = datetime.combine(day, TOSS_ACCOUNT_DAILY_TIME, TOSS_ACCOUNT_DAILY_TIMEZONE)
    if local < scheduled:
        day -= timedelta(days=1)
        scheduled = datetime.combine(
            day, TOSS_ACCOUNT_DAILY_TIME, TOSS_ACCOUNT_DAILY_TIMEZONE,
        )
    return day, scheduled


def _receipt_path(root: Path, occurrence_date: date) -> Path:
    return (
        root.resolve() / "data/state/toss_account_snapshot_occurrences"
        / f"{occurrence_date.isoformat()}.json"
    )


def _claim(
    root: Path, *, occurrence_date: date, scheduled_for: datetime,
    claimed_at: datetime,
) -> tuple[Path, bool]:
    path = _receipt_path(root, occurrence_date)
    path.parent.mkdir(parents=True, exist_ok=True)
    claim = {
        "schema_version": TOSS_ACCOUNT_DAILY_SCHEMA_VERSION,
        "operation": TOSS_ACCOUNT_DAILY_OPERATION,
        "occurrence_date": occurrence_date.isoformat(),
        "scheduled_for": scheduled_for.isoformat(),
        "claimed_at_utc": claimed_at.astimezone(timezone.utc).isoformat(),
        "status": "CLAIMED_BEFORE_PROVIDER",
    }
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError:
        return path, False
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(_json_bytes(claim))
        stream.flush()
        os.fsync(stream.fileno())
    return path, True


def _strict_receipt(path: Path, *, occurrence_date: date) -> dict[str, Any]:
    try:
        payload = _strict_json_object(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, TossAccountScheduleError) as error:
        raise TossAccountScheduleError("scheduled account receipt is invalid") from error
    status = payload.get("status") if isinstance(payload, dict) else None
    expected_keys = (
        _CLAIM_KEYS if status == "CLAIMED_BEFORE_PROVIDER"
        else _RECOVERY_KEYS if status == "RECOVERY_REQUIRED"
        else _TERMINAL_KEYS
    )
    if (
        not isinstance(payload, dict)
        or set(payload) != expected_keys
        or payload.get("schema_version") != TOSS_ACCOUNT_DAILY_SCHEMA_VERSION
        or payload.get("operation") != TOSS_ACCOUNT_DAILY_OPERATION
        or payload.get("occurrence_date") != occurrence_date.isoformat()
        or status not in {
            "CLAIMED_BEFORE_PROVIDER", "RECOVERY_REQUIRED", "TERMINAL_SUCCESS",
            "TERMINAL_FAILURE", "TERMINAL_INELIGIBLE",
        }
    ):
        raise TossAccountScheduleError("scheduled account receipt identity differs")
    scheduled = _aware_clock(payload.get("scheduled_for"), field="scheduled_for")
    claimed = _aware_clock(payload.get("claimed_at_utc"), field="claimed_at_utc")
    expected_scheduled = datetime.combine(
        occurrence_date, TOSS_ACCOUNT_DAILY_TIME, TOSS_ACCOUNT_DAILY_TIMEZONE,
    )
    if scheduled.astimezone(TOSS_ACCOUNT_DAILY_TIMEZONE) != expected_scheduled:
        raise TossAccountScheduleError("scheduled account occurrence clock differs")
    if status == "CLAIMED_BEFORE_PROVIDER":
        return payload
    if status == "RECOVERY_REQUIRED":
        recovery_required = _aware_clock(
            payload.get("recovery_required_at_utc"), field="recovery_required_at_utc",
        )
        if (
            recovery_required < claimed
            or payload.get("reason") != "ACCOUNT_PROJECTION_RECOVERY_REQUIRED"
        ):
            raise TossAccountScheduleError("scheduled account recovery receipt differs")
        return payload
    finished = _aware_clock(payload.get("finished_at_utc"), field="finished_at_utc")
    if finished < claimed:
        raise TossAccountScheduleError("scheduled account terminal clock differs")
    token_calls = payload.get("token_calls")
    account_calls = payload.get("account_calls")
    counts_known = type(token_calls) is int and type(account_calls) is int
    if counts_known and not (0 <= token_calls <= 1 and 0 <= account_calls <= 3):
        raise TossAccountScheduleError("scheduled account call counts differ")
    if status == "TERMINAL_SUCCESS":
        digest = payload.get("normalized_sha256")
        if (
            payload.get("outcome") != "SUCCEEDED"
            or payload.get("reason") is not None
            or token_calls not in {0, 1}
            or account_calls != 3
            or payload.get("normalized") != _NORMALIZED_ACCOUNT_PATH
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        ):
            raise TossAccountScheduleError("scheduled account success differs")
    else:
        if payload.get("normalized") is not None or payload.get("normalized_sha256") is not None:
            raise TossAccountScheduleError("scheduled account failure retained a target")
        outcome = payload.get("outcome")
        reason = payload.get("reason")
        if status == "TERMINAL_INELIGIBLE":
            if (
                outcome != "NOT_AVAILABLE" or token_calls != 0 or account_calls != 0
                or reason not in {
                    "RUNTIME_CONFIG_REQUIRED", "ACCOUNT_SELECTOR_INVALID",
                    "CLIENT_INITIALIZATION_FAILED",
                }
            ):
                raise TossAccountScheduleError("scheduled account ineligible result differs")
        elif not (
            (outcome == "FAILED_PRESERVED_PRIOR"
             and reason == "ACCOUNT_REFRESH_FAILED_CLOSED")
            or (outcome == "SCHEDULE_INTERNAL_FAILURE"
                and reason in {
                    "SCHEDULE_INTERNAL_FAILURE",
                    "SCHEDULE_INTERRUPTED_AFTER_COMMIT",
                }
                and (counts_known or (token_calls is None and account_calls is None)))
            or (outcome == "SCHEDULE_CONCURRENT_REFRESH"
                and reason == "CONCURRENT_REFRESH_IN_PROGRESS"
                and token_calls == account_calls == 0)
        ):
            raise TossAccountScheduleError("scheduled account failure result differs")
    return payload


def _terminalize(path: Path, claim: dict[str, Any], terminal: dict[str, Any]) -> None:
    if set(claim) != _CLAIM_KEYS or claim.get("status") != "CLAIMED_BEFORE_PROVIDER":
        raise TossAccountScheduleError("scheduled account claim is not terminalizable")
    payload = {
        **claim, **terminal,
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    _atomic_json(path, payload)
    if _strict_json_object(path.read_text(encoding="utf-8")) != payload:
        raise TossAccountScheduleError("scheduled account receipt readback differs")


def _mark_recovery_required(path: Path, claim: dict[str, Any]) -> None:
    """Persist identifier-free nonterminal evidence when exact rollback is unknown."""

    if set(claim) != _CLAIM_KEYS or claim.get("status") != "CLAIMED_BEFORE_PROVIDER":
        raise TossAccountScheduleError("scheduled account claim is not recoverable")
    payload = {
        **claim,
        "status": "RECOVERY_REQUIRED",
        "recovery_required_at_utc": datetime.now(timezone.utc).isoformat(),
        "reason": "ACCOUNT_PROJECTION_RECOVERY_REQUIRED",
    }
    _atomic_json(path, payload)
    if _strict_json_object(path.read_text(encoding="utf-8")) != payload:
        raise TossAccountScheduleError("scheduled account recovery receipt readback differs")


def _summary(
    *, occurrence_date: date, scheduled_for: datetime, status: str,
    token_calls: int | None = 0, account_calls: int | None = 0, reason: str | None = None,
    outcome: str | None = None, receipt: str | None = None,
    retained_status: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": TOSS_ACCOUNT_DAILY_SCHEMA_VERSION,
        "operation": TOSS_ACCOUNT_DAILY_OPERATION,
        "occurrence_date": occurrence_date.isoformat(),
        "scheduled_for": scheduled_for.isoformat(),
        "status": status, "outcome": outcome, "reason": reason,
        "token_calls": token_calls, "account_calls": account_calls,
        "receipt": receipt, "retained_status": retained_status,
    }


def _terminal_from_refresh_result(
    root: Path, result: TossAccountRefreshResult,
) -> dict[str, object]:
    token_calls = result.token_calls
    account_calls = result.account_calls
    if not 0 <= token_calls <= 1 or not 0 <= account_calls <= 3:
        raise TossAccountScheduleError("scheduled account call budget differs")
    if result.status == "SUCCEEDED" and (
        token_calls not in {0, 1} or account_calls != 3
        or result.normalized_path != _NORMALIZED_ACCOUNT_PATH
    ):
        raise TossAccountScheduleError("scheduled account success evidence differs")
    if result.status not in {"SUCCEEDED", "FAILED_PRESERVED_PRIOR"}:
        raise TossAccountScheduleError("scheduled account outcome differs")
    terminal_status = (
        "TERMINAL_SUCCESS" if result.status == "SUCCEEDED" else "TERMINAL_FAILURE"
    )
    normalized_sha256 = None
    if terminal_status == "TERMINAL_SUCCESS":
        normalized = (root / _NORMALIZED_ACCOUNT_PATH).resolve()
        normalized.relative_to(root)
        normalized_sha256 = hashlib.sha256(normalized.read_bytes()).hexdigest()
    return {
        "status": terminal_status, "outcome": result.status,
        "reason": result.reason, "token_calls": token_calls,
        "account_calls": account_calls, "normalized": result.normalized_path,
        "normalized_sha256": normalized_sha256,
    }


def _unknown_internal_failure_terminal(
    *, after_complete_commit: bool = False,
) -> dict[str, object]:
    return {
        "status": "TERMINAL_FAILURE",
        "outcome": "SCHEDULE_INTERNAL_FAILURE",
        "reason": (
            "SCHEDULE_INTERRUPTED_AFTER_COMMIT"
            if after_complete_commit else "SCHEDULE_INTERNAL_FAILURE"
        ),
        "token_calls": None,
        "account_calls": None,
        "normalized": None,
        "normalized_sha256": None,
    }


def run_toss_account_daily(
    project_root: Path,
    environment: Mapping[str, str],
    *,
    now: datetime | None = None,
    dry_run: bool = False,
    client_factory: Callable[..., TossInvestClient] = TossInvestClient,
    runtime_builder: Callable[..., TossAccountRuntimeWiring] = build_toss_account_runtime,
) -> dict[str, Any]:
    """Run one identifier-free, occurrence-claimed daily read-only refresh."""

    root = project_root.resolve()
    started = now or datetime.now(timezone.utc)
    occurrence_date, scheduled_for = _occurrence(started)
    if dry_run:
        wiring = runtime_builder(root, environment, client_factory=client_factory)
        return _summary(
            occurrence_date=occurrence_date, scheduled_for=scheduled_for,
            status="DRY_RUN_READY" if wiring.enabled else "DRY_RUN_INELIGIBLE",
            reason=wiring.reason,
        )

    path, claimed = _claim(
        root, occurrence_date=occurrence_date, scheduled_for=scheduled_for,
        claimed_at=started,
    )
    if not claimed:
        retained = _strict_receipt(path, occurrence_date=occurrence_date)
        if retained["status"] == "RECOVERY_REQUIRED":
            return _summary(
                occurrence_date=occurrence_date, scheduled_for=scheduled_for,
                status="RECOVERY_REQUIRED", outcome="SCHEDULE_INTERNAL_FAILURE",
                reason=retained["reason"], token_calls=None, account_calls=None,
                receipt=path.relative_to(root).as_posix(),
                retained_status=retained["status"],
            )
        return _summary(
            occurrence_date=occurrence_date, scheduled_for=scheduled_for,
            status="NOOP_OCCURRENCE_ALREADY_CLAIMED",
            retained_status=retained["status"],
            receipt=path.relative_to(root).as_posix(),
        )

    claim = _strict_receipt(path, occurrence_date=occurrence_date)
    try:
        with account_snapshot_lifecycle_lock(root, timeout_seconds=0.0):
            projection_before = _projection_bytes(root)
            try:
                wiring = runtime_builder(
                    root, environment, client_factory=client_factory,
                )
                if not wiring.enabled or wiring.refresher is None:
                    terminal = {
                        "status": "TERMINAL_INELIGIBLE", "outcome": "NOT_AVAILABLE",
                        "reason": wiring.reason, "token_calls": 0, "account_calls": 0,
                        "normalized": None, "normalized_sha256": None,
                    }
                    _terminalize(path, claim, terminal)
                    return _summary(
                        occurrence_date=occurrence_date, scheduled_for=scheduled_for,
                        status=terminal["status"], outcome=terminal["outcome"],
                        reason=terminal["reason"], receipt=path.relative_to(root).as_posix(),
                    )
                result = wiring.refresher(AccountRefreshTrigger.PERIODIC)
                terminal = _terminal_from_refresh_result(root, result)
                _terminalize(path, claim, terminal)
            except BaseException:
                after_complete_commit = False
                if _projection_differs_from(root, projection_before):
                    after_complete_commit = _verified_projection_digest(root) is not None
                    try:
                        _restore_projection_bytes(root, projection_before)
                    except BaseException:
                        # A wrapper/intermediate restore can fail after one
                        # target. Retry the exact byte-map primitive while the
                        # occurrence still owns the lifecycle lease.
                        try:
                            _restore_projection_bytes_exact(root, projection_before)
                        except TossAccountRecoveryError:
                            raise
                        except BaseException as error:
                            raise TossAccountRecoveryError(
                                "account projection exact restore could not be verified"
                            ) from error
                    if _projection_differs_from(root, projection_before):
                        raise TossAccountRecoveryError(
                            "account projection restore readback differs"
                        )
                terminal = _unknown_internal_failure_terminal(
                    after_complete_commit=after_complete_commit,
                )
                _terminalize(path, claim, terminal)
                return _summary(
                    occurrence_date=occurrence_date, scheduled_for=scheduled_for,
                    status=terminal["status"], outcome=terminal["outcome"],
                    reason=terminal["reason"], token_calls=None, account_calls=None,
                    receipt=path.relative_to(root).as_posix(),
                )
        return _summary(
            occurrence_date=occurrence_date, scheduled_for=scheduled_for,
            status=terminal["status"], outcome=result.status, reason=result.reason,
            token_calls=result.token_calls, account_calls=result.account_calls,
            receipt=path.relative_to(root).as_posix(),
        )
    except TossAccountRecoveryError:
        # Never turn incomplete restoration into a terminal receipt or a silent
        # claim replay.  The recovery receipt is identifier-free and API-zero.
        _mark_recovery_required(path, claim)
        raise
    except TimeoutError:
        # This occurrence never acquired the shared lease, so it made no
        # provider attempt and must not reconcile or restore another owner's
        # projection.
        terminal = {
            "status": "TERMINAL_FAILURE",
            "outcome": "SCHEDULE_CONCURRENT_REFRESH",
            "reason": "CONCURRENT_REFRESH_IN_PROGRESS",
            "token_calls": 0,
            "account_calls": 0,
            "normalized": None,
            "normalized_sha256": None,
        }
        _terminalize(path, claim, terminal)
        return _summary(
            occurrence_date=occurrence_date, scheduled_for=scheduled_for,
            status=terminal["status"], outcome=terminal["outcome"],
            reason=terminal["reason"], token_calls=0, account_calls=0,
            receipt=path.relative_to(root).as_posix(),
        )
    except BaseException:
        # No lifecycle lease was acquired for this occurrence.  Do not restore
        # a projection because another valid refresher may own it.
        terminal = _unknown_internal_failure_terminal()
        _terminalize(path, claim, terminal)
        return _summary(
            occurrence_date=occurrence_date, scheduled_for=scheduled_for,
            status=terminal["status"], outcome=terminal["outcome"],
            reason=terminal["reason"], token_calls=None,
            account_calls=None, receipt=path.relative_to(root).as_posix(),
        )


__all__ = [
    "TOSS_ACCOUNT_CLIENT_ID_ENV",
    "TOSS_ACCOUNT_CLIENT_SECRET_ENV",
    "TOSS_ACCOUNT_ENV_NAMES",
    "TOSS_ACCOUNT_REQUIRED_ENV_NAMES",
    "TOSS_ACCOUNT_SEQ_ENV",
    "TOSS_ACCOUNT_DAILY_OPERATION",
    "TOSS_ACCOUNT_DAILY_SCHEMA_VERSION",
    "TOSS_ACCOUNT_DAILY_TIME",
    "TOSS_ACCOUNT_DAILY_TIMEZONE",
    "TossAccountScheduleError",
    "TossAccountRecoveryError",
    "TossAccountRuntimeState",
    "TossAccountRuntimeWiring",
    "build_toss_account_runtime",
    "load_toss_account_environment",
    "run_toss_account_daily",
]
