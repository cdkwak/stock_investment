"""Pure scope, parser, and approval gate for a one-shot OpenDART pilot.

No credential loading, network I/O, or Landing write occurs in this module.
The live transport remains unavailable until Lead approval of the frozen runbook.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Callable


class OpenDartFinancialStatementError(ValueError):
    """A redacted typed failure; never attach provider/authentication details."""

    def __init__(self, outcome: str):
        super().__init__(outcome)
        self.outcome = outcome


_CORP_CODE = re.compile(r"\d{8}\Z")
_RECEIPT = re.compile(r"\d{14}\Z")
_YEAR = re.compile(r"\d{4}\Z")
_REQUIRED_ITEM_FIELDS = (
    "rcept_no", "reprt_code", "bsns_year", "corp_code", "sj_div", "account_id",
    "account_nm", "account_detail", "thstrm_nm", "thstrm_amount",
    "thstrm_add_amount", "frmtrm_nm", "frmtrm_amount", "currency", "ord", "fs_div",
)


@dataclass(frozen=True)
class FinancialStatementPilotScope:
    """The only future live scope selected by UR-106."""

    corp_code: str = "01160363"  # ECOPRO BM; retained corporate-action baseline identity
    business_year: str = "2021"
    report_code: str = "11011"  # annual business report
    financial_statement_division: str = "CFS"
    endpoint: str = "fnlttSinglAcnt.json"
    maximum_gets: int = 1
    timeout_seconds: int = 10
    retry_count: int = 0

    def __post_init__(self) -> None:
        if not _CORP_CODE.fullmatch(self.corp_code):
            raise OpenDartFinancialStatementError("corp_code must be exactly eight digits")
        if not _YEAR.fullmatch(self.business_year):
            raise OpenDartFinancialStatementError("business_year must be exactly four digits")
        if self.report_code != "11011" or self.financial_statement_division != "CFS":
            raise OpenDartFinancialStatementError("only the frozen annual CFS scope is valid")
        if self.endpoint != "fnlttSinglAcnt.json" or self.maximum_gets != 1:
            raise OpenDartFinancialStatementError("only the frozen one-GET endpoint is valid")
        if self.timeout_seconds != 10 or self.retry_count != 0:
            raise OpenDartFinancialStatementError("timeout/retry boundary changed")

    def public_parameters(self) -> dict[str, str]:
        """Return only recordable public parameters; never include a key."""
        return {
            "corp_code": self.corp_code,
            "bsns_year": self.business_year,
            "reprt_code": self.report_code,
            "fs_div": self.financial_statement_division,
        }


@dataclass(frozen=True)
class ParsedFinancialStatement:
    body_sha256: str
    rows: tuple[dict[str, object], ...]


@dataclass(frozen=True)
class FinancialStatementPilotRunResult:
    typed_outcome: str
    call_count: int
    response_bytes: int
    response_body_sha256: str
    parsed: ParsedFinancialStatement


def _scope_token(scope: FinancialStatementPilotScope) -> str:
    public = json.dumps(scope.public_parameters(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(public.encode("utf-8")).hexdigest()


def _canonical_utc_iso(captured_at_utc: str) -> str:
    """Accept one aware timestamp and retain its canonical UTC representation."""
    try:
        captured = datetime.fromisoformat(captured_at_utc.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as error:
        raise OpenDartFinancialStatementError("CAPTURE_TIME_INVALID") from error
    if captured.tzinfo is None or captured.utcoffset() is None:
        raise OpenDartFinancialStatementError("CAPTURE_TIME_TIMEZONE_REQUIRED")
    return captured.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _landing_path(project_root: Path, scope: FinancialStatementPilotScope) -> Path:
    return (
        project_root / "data" / "landing" / "diagnostics"
        / "opendart_financial_statement_pilot" / _scope_token(scope) / "response.json"
    )


def _completed_checkpoint_path(project_root: Path, scope: FinancialStatementPilotScope) -> Path:
    return (
        project_root / "data" / "state" / "opendart_financial_statement_pilot"
        / f"{_scope_token(scope)}.json"
    )


def _failure_checkpoint_path(
    project_root: Path, scope: FinancialStatementPilotScope, response_body_sha256: str | None,
) -> Path:
    fingerprint = response_body_sha256 or "no_response_body"
    return (
        project_root / "data" / "state" / "opendart_financial_statement_pilot" / "failures"
        / f"{_scope_token(scope)}-{fingerprint}.json"
    )


def _commit_immutable_bytes(path: Path, body: bytes) -> None:
    """Create one same-volume immutable file without replacing an existing target."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        # A same-volume hard link atomically creates the final name and fails if
        # another writer already owns it; unlike replace(), it cannot overwrite.
        os.link(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _sanitized_checkpoint(
    *, scope: FinancialStatementPilotScope, call_count: int, response_bytes: int,
    response_body_sha256: str | None, typed_outcome: str, captured_at_utc: str,
) -> bytes:
    return json.dumps({
        "public_scope": scope.public_parameters(),
        "call_count": call_count,
        "response_bytes": response_bytes,
        "response_body_sha256": response_body_sha256,
        "captured_at_utc": captured_at_utc,
        "typed_outcome": typed_outcome,
    }, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _record_failure_checkpoint(
    *, project_root: Path, scope: FinancialStatementPilotScope, call_count: int,
    response_bytes: int, response_body_sha256: str | None, typed_outcome: str,
    captured_at_utc: str,
) -> None:
    """Retain one immutable redacted failure state without touching valid output."""
    try:
        _commit_immutable_bytes(
            _failure_checkpoint_path(project_root, scope, response_body_sha256),
            _sanitized_checkpoint(
                scope=scope, call_count=call_count, response_bytes=response_bytes,
                response_body_sha256=response_body_sha256, typed_outcome=typed_outcome,
                captured_at_utc=captured_at_utc,
            ),
        )
    except OSError:
        # The primary typed failure remains authoritative; a repeated failure
        # checkpoint or a checkpoint-storage problem must not replace anything.
        return


def _fail_closed(
    *, project_root: Path, scope: FinancialStatementPilotScope, call_count: int,
    response_bytes: int, response_body_sha256: str | None, typed_outcome: str,
    captured_at_utc: str,
) -> None:
    _record_failure_checkpoint(
        project_root=project_root, scope=scope, call_count=call_count,
        response_bytes=response_bytes, response_body_sha256=response_body_sha256,
        typed_outcome=typed_outcome, captured_at_utc=captured_at_utc,
    )
    raise OpenDartFinancialStatementError(typed_outcome)


def parse_single_account_response(
    body: bytes, *, scope: FinancialStatementPilotScope, captured_at_utc: str,
) -> ParsedFinancialStatement:
    """Validate one response without accepting numbers or deriving PIT availability."""
    try:
        captured = datetime.fromisoformat(captured_at_utc.replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError) as error:
        raise OpenDartFinancialStatementError("capture time is invalid") from error
    if captured.tzinfo is None or captured.utcoffset() is None:
        raise OpenDartFinancialStatementError("capture time must be timezone-aware")
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OpenDartFinancialStatementError("response is not UTF-8 JSON") from error
    if not isinstance(payload, dict) or payload.get("status") != "000":
        raise OpenDartFinancialStatementError("provider status is not successful")
    items = payload.get("list")
    if not isinstance(items, list) or not items:
        raise OpenDartFinancialStatementError("successful response has no financial rows")
    digest = hashlib.sha256(body).hexdigest()
    rows: list[dict[str, object]] = []
    for ordinal, item in enumerate(items):
        if not isinstance(item, dict) or any(field not in item for field in _REQUIRED_ITEM_FIELDS):
            raise OpenDartFinancialStatementError("documented financial-statement fields are missing")
        if (
            item["corp_code"] != scope.corp_code
            or item["bsns_year"] != scope.business_year
            or item["reprt_code"] != scope.report_code
            or item["fs_div"] != scope.financial_statement_division
        ):
            raise OpenDartFinancialStatementError("response scope does not match frozen request")
        if not isinstance(item["rcept_no"], str) or not _RECEIPT.fullmatch(item["rcept_no"]):
            raise OpenDartFinancialStatementError("receipt number is invalid")
        if not isinstance(item["sj_div"], str) or not item["sj_div"]:
            raise OpenDartFinancialStatementError("statement division is invalid")
        if not isinstance(item["account_nm"], str) or not item["account_nm"]:
            raise OpenDartFinancialStatementError("account name is invalid")
        rows.append({
            "source_operation": "opendart_financial_statement_pilot",
            "landing_response_body_sha256": digest,
            "source_item_ordinal": ordinal,
            "corp_code": item["corp_code"],
            "business_year": int(item["bsns_year"]),
            "report_code": item["reprt_code"],
            "financial_statement_division": item["fs_div"],
            "receipt_no": item["rcept_no"],
            "statement_division": item["sj_div"],
            "account_id": item["account_id"] or None,
            "account_name": item["account_nm"],
            "account_detail": item["account_detail"] or None,
            "current_term_name": item["thstrm_nm"] or None,
            "current_term_amount_raw": item["thstrm_amount"] or None,
            "current_term_add_amount_raw": item["thstrm_add_amount"] or None,
            "prior_term_name": item["frmtrm_nm"] or None,
            "prior_term_amount_raw": item["frmtrm_amount"] or None,
            "currency": item["currency"] or None,
            "display_order": item["ord"] or None,
            "revision_parent_receipt_no": None,
            "revision_status": "UNVERIFIED_NO_EXPLICIT_PARENT",
            "observation_time_utc": captured.astimezone(timezone.utc).isoformat(),
            "provider_published_at_utc": None,
            "available_at_utc": None,
            "usable_from": None,
            "pit_status": "PIT_BLOCKED_PUBLICATION_AND_REVISION_UNVERIFIED",
            "redistribution_status": "RIGHTS_AND_REDISTRIBUTION_UNVERIFIED",
            "capture_year": captured.year,
        })
    return ParsedFinancialStatement(digest, tuple(rows))


def run_landing_first_financial_statement_pilot(
    project_root: Path,
    *, approved: bool, fetch: Callable[[], bytes], scope: FinancialStatementPilotScope,
    captured_at_utc: str,
    parser: Callable[[bytes, FinancialStatementPilotScope, str], ParsedFinancialStatement] | None = None,
) -> FinancialStatementPilotRunResult:
    """Execute the bounded runner through injectable transport only.

    The function is intentionally credential-blind: callers provide a zero-arg
    transport that has already been authorized and configured elsewhere. The
    first possible read is a completed immutable checkpoint, which yields an
    API-zero replay. A new response is committed to same-volume immutable
    Landing before and independently of parsing; parsing receives only verified
    readback bytes.
    """
    project_root = Path(project_root)
    landing = _landing_path(project_root, scope)
    completed = _completed_checkpoint_path(project_root, scope)
    parse = parser or (
        lambda body, selected_scope, captured: parse_single_account_response(
            body, scope=selected_scope, captured_at_utc=captured,
        )
    )

    if completed.exists():
        try:
            checkpoint = json.loads(completed.read_text(encoding="utf-8"))
            retained_captured_at_utc = checkpoint["captured_at_utc"]
            if (
                not isinstance(retained_captured_at_utc, str)
                or _canonical_utc_iso(retained_captured_at_utc) != retained_captured_at_utc
                or not isinstance(checkpoint["response_bytes"], int)
                or not isinstance(checkpoint["response_body_sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", checkpoint["response_body_sha256"])
            ):
                raise ValueError
            expected = _sanitized_checkpoint(
                scope=scope,
                call_count=1,
                response_bytes=checkpoint["response_bytes"],
                response_body_sha256=checkpoint["response_body_sha256"],
                typed_outcome="COMPLETED",
                captured_at_utc=retained_captured_at_utc,
            )
            if completed.read_bytes() != expected or not landing.is_file():
                raise ValueError
            readback = landing.read_bytes()
            body_sha256 = hashlib.sha256(readback).hexdigest()
            if (
                len(readback) != checkpoint["response_bytes"]
                or body_sha256 != checkpoint["response_body_sha256"]
            ):
                raise ValueError
            # The new caller time is deliberately ignored: a replay represents
            # the original as-retrieved observation, not a new observation.
            parsed = parse(readback, scope, retained_captured_at_utc)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
            raise OpenDartFinancialStatementError("API_ZERO_REPLAY_READBACK_OR_CHECKPOINT_FAILED")
        return FinancialStatementPilotRunResult(
            typed_outcome="NOOP_API_ZERO_REPLAY",
            call_count=0,
            response_bytes=len(readback),
            response_body_sha256=body_sha256,
            parsed=parsed,
        )

    # Validate and canonicalize before any fresh transport or Landing action.
    canonical_captured_at_utc = _canonical_utc_iso(captured_at_utc)

    if landing.exists():
        # A prior incomplete Landing attempt is evidence, not a reason to
        # overwrite it or repeat the provider call under the same scope.
        raise OpenDartFinancialStatementError("ORPHANED_IMMUTABLE_LANDING_REVIEW_REQUIRED")

    if not approved:
        raise OpenDartFinancialStatementError("EXPLICIT_LIVE_APPROVAL_REQUIRED")

    call_count = 0
    try:
        call_count = 1
        response = fetch()
        if not isinstance(response, bytes):
            raise TypeError
    except Exception:
        _fail_closed(
            project_root=project_root, scope=scope, call_count=call_count,
            response_bytes=0, response_body_sha256=None, typed_outcome="TRANSPORT_FAILED",
            captured_at_utc=canonical_captured_at_utc,
        )
    response_bytes = len(response)
    body_sha256 = hashlib.sha256(response).hexdigest()
    try:
        _commit_immutable_bytes(landing, response)
    except OSError:
        _fail_closed(
            project_root=project_root, scope=scope, call_count=call_count,
            response_bytes=response_bytes, response_body_sha256=body_sha256,
            typed_outcome="LANDING_COMMIT_FAILED", captured_at_utc=canonical_captured_at_utc,
        )
    try:
        readback = landing.read_bytes()
    except OSError:
        _fail_closed(
            project_root=project_root, scope=scope, call_count=call_count,
            response_bytes=response_bytes, response_body_sha256=body_sha256,
            typed_outcome="LANDING_READBACK_FAILED", captured_at_utc=canonical_captured_at_utc,
        )
    if hashlib.sha256(readback).hexdigest() != body_sha256:
        _fail_closed(
            project_root=project_root, scope=scope, call_count=call_count,
            response_bytes=response_bytes, response_body_sha256=body_sha256,
            typed_outcome="LANDING_READBACK_HASH_MISMATCH", captured_at_utc=canonical_captured_at_utc,
        )
    try:
        parsed = parse(readback, scope, canonical_captured_at_utc)
    except Exception:
        _fail_closed(
            project_root=project_root, scope=scope, call_count=call_count,
            response_bytes=response_bytes, response_body_sha256=body_sha256,
            typed_outcome="PARSE_FAILED", captured_at_utc=canonical_captured_at_utc,
        )
    try:
        _commit_immutable_bytes(
            completed,
            _sanitized_checkpoint(
                scope=scope, call_count=call_count, response_bytes=response_bytes,
                response_body_sha256=body_sha256, typed_outcome="COMPLETED",
                captured_at_utc=canonical_captured_at_utc,
            ),
        )
    except OSError:
        _fail_closed(
            project_root=project_root, scope=scope, call_count=call_count,
            response_bytes=response_bytes, response_body_sha256=body_sha256,
            typed_outcome="CHECKPOINT_COMMIT_FAILED", captured_at_utc=canonical_captured_at_utc,
        )
    return FinancialStatementPilotRunResult(
        typed_outcome="COMPLETED",
        call_count=call_count,
        response_bytes=response_bytes,
        response_body_sha256=body_sha256,
        parsed=parsed,
    )


__all__ = [
    "FinancialStatementPilotScope", "OpenDartFinancialStatementError",
    "FinancialStatementPilotRunResult", "ParsedFinancialStatement",
    "parse_single_account_response",
    "run_landing_first_financial_statement_pilot",
]
