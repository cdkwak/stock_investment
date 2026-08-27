from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import json
import re
import unicodedata
from typing import Iterable, Mapping


ISSUE_SCHEMA = "issue-state/v1"
FINGERPRINT_VERSION = "issue-fingerprint/v1"
_TOKEN = re.compile(r"^[A-Z][A-Z0-9_.-]{0,63}$")
_TARGET = re.compile(r"^[a-z0-9][a-z0-9_.:/=-]{0,159}$")
_SOURCE_SCHEMA = re.compile(r"^[a-z][a-z0-9_.-]{0,63}/v[0-9]+$")
_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_RELATIVE_EVIDENCE = re.compile(
    r"^(?![A-Za-z]:)(?!/)(?!.*(?:^|/)\.\.(?:/|$))"
    r"[A-Za-z0-9_.+\-/]+@sha256:[a-f0-9]{64}$"
)
_PRIVATE_EVIDENCE = re.compile(
    r"(?i)(?:account|acct|holding|balance|order|password|passwd|secret|token|"
    r"authorization|credential)|(?:^|[._\-/])auth(?:[._\-/]|$)|"
    r"(?:^|[._\-/])\d{10,}(?:[._\-/]|$)|https?[:/]"
)
_ACTOR = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_SUPPRESSION_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,95}$")
_REASON = re.compile(r"^[A-Z][A-Z0-9_.-]{0,63}$")
SEVERITIES = ("INFO", "WARNING", "ERROR", "CRITICAL")
RETRYABILITIES = frozenset({
    "NOT_RETRYABLE", "SAFE_LOCAL_RETRY", "AUTHORIZED_OPERATION_REQUIRED", "UNKNOWN",
})
FRESHNESS = frozenset({"CURRENT", "EXPECTED_LAG", "STALE", "UNKNOWN", "NOT_APPLICABLE", "BLOCKED"})
IMPORTANCE = ("UNKNOWN", "LOW", "NORMAL", "HIGH", "CRITICAL")


def _aware_utc(value: str, name: str) -> str:
    if type(value) is not str:
        raise ValueError(f"{name} must be text")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{name} must be an ISO timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return parsed.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _utc_datetime(value: str, name: str) -> datetime:
    return datetime.fromisoformat(_aware_utc(value, name).replace("Z", "+00:00"))


def _token(value: str, name: str) -> str:
    if type(value) is not str:
        raise ValueError(f"{name} must be text")
    normalized = unicodedata.normalize("NFC", value)
    if normalized != value or not _TOKEN.fullmatch(value):
        raise ValueError(f"{name} is not canonical")
    return value


def _target(value: str) -> str:
    if type(value) is not str:
        raise ValueError("target_id must be text")
    normalized = unicodedata.normalize("NFC", value).lower()
    if normalized != value or not _TARGET.fullmatch(value):
        raise ValueError("target_id is not canonical")
    return value


def canonical_json(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")


def validate_evidence_identity(value: str) -> str:
    if type(value) is not str or not _RELATIVE_EVIDENCE.fullmatch(value):
        raise ValueError("evidence identity is unsafe")
    relative = value.split("@sha256:", 1)[0]
    if "\\" in relative or _PRIVATE_EVIDENCE.search(relative):
        raise ValueError("evidence identity is private")
    return value


def stable_fingerprint(*, stable_code: str, domain: str, target_kind: str, target_id: str) -> str:
    payload = {
        "domain": _token(domain, "domain"),
        "fingerprint_version": FINGERPRINT_VERSION,
        "stable_code": _token(stable_code, "stable_code"),
        "target_id": _target(target_id),
        "target_kind": _token(target_kind, "target_kind"),
    }
    return hashlib.sha256(canonical_json(payload)).hexdigest()


@dataclass(frozen=True, slots=True)
class IssueEvent:
    source_schema: str
    source_event_id: str
    occurred_at: str
    stable_code: str
    domain: str
    target_kind: str
    target_id: str
    outcome: str
    severity: str
    retryability: str
    freshness: str = "UNKNOWN"
    source_as_of: str | None = None
    expected_by: str | None = None
    evidence: tuple[str, ...] = ()
    importance: str = "UNKNOWN"

    def __post_init__(self) -> None:
        _token(self.stable_code, "stable_code")
        _token(self.domain, "domain")
        _token(self.target_kind, "target_kind")
        _target(self.target_id)
        if type(self.source_schema) is not str or not _SOURCE_SCHEMA.fullmatch(self.source_schema):
            raise ValueError("source_schema is invalid")
        if type(self.source_event_id) is not str or not _TARGET.fullmatch(self.source_event_id):
            raise ValueError("source_event_id is invalid")
        _aware_utc(self.occurred_at, "occurred_at")
        if self.outcome not in {"FAILURE", "SUCCESS"}:
            raise ValueError("outcome differs")
        if self.severity not in SEVERITIES or self.retryability not in RETRYABILITIES:
            raise ValueError("issue classification differs")
        if self.freshness not in FRESHNESS:
            raise ValueError("freshness differs")
        if self.importance not in IMPORTANCE:
            raise ValueError("importance differs")
        for name, value in (("source_as_of", self.source_as_of), ("expected_by", self.expected_by)):
            if value is not None:
                _aware_utc(value, name)
        if len(self.evidence) > 8 or len(set(self.evidence)) != len(self.evidence):
            raise ValueError("event evidence is invalid")
        for item in self.evidence:
            validate_evidence_identity(item)

    @property
    def fingerprint(self) -> str:
        return stable_fingerprint(
            stable_code=self.stable_code, domain=self.domain,
            target_kind=self.target_kind, target_id=self.target_id,
        )

    @property
    def event_identity(self) -> str:
        return f"{self.source_schema}:{self.source_event_id}"


@dataclass(slots=True)
class IssueRecord:
    fingerprint: str
    stable_code: str
    domain: str
    target_kind: str
    target_id: str
    state: str
    severity: str
    retryability: str
    freshness: str
    epoch: int
    opened_at: str
    first_at: str
    latest_at: str
    occurrence_count: int
    source_event_count: int
    source_event_ids: list[str]
    last_success_at: str | None = None
    recovered_at: str | None = None
    recovery_count: int = 0
    previous_epochs: list[dict[str, object]] = field(default_factory=list)
    historical_epoch_count: int = 0
    historical_occurrence_count: int = 0
    historical_first_at: str | None = None
    historical_latest_at: str | None = None
    source_as_of: str | None = None
    expected_by: str | None = None
    evidence: list[str] = field(default_factory=list)
    importance: str = "UNKNOWN"
    suppression: dict[str, object] = field(default_factory=lambda: {"state": "NONE", "history": []})
    schema: str = ISSUE_SCHEMA
    fingerprint_version: str = FINGERPRINT_VERSION

    def validate(self) -> None:
        if self.schema != ISSUE_SCHEMA or self.fingerprint_version != FINGERPRINT_VERSION:
            raise ValueError("issue record schema differs")
        expected = stable_fingerprint(
            stable_code=self.stable_code, domain=self.domain,
            target_kind=self.target_kind, target_id=self.target_id,
        )
        if self.fingerprint != expected or not _DIGEST.fullmatch(self.fingerprint):
            raise ValueError("issue fingerprint differs")
        if self.state not in {"ACTIVE", "OBSERVING", "RECOVERED"}:
            raise ValueError("issue state differs")
        if self.severity not in SEVERITIES or self.retryability not in RETRYABILITIES:
            raise ValueError("issue classification differs")
        if self.freshness not in FRESHNESS:
            raise ValueError("issue freshness differs")
        if self.importance not in IMPORTANCE:
            raise ValueError("issue importance differs")
        if type(self.epoch) is not int or self.epoch < 1:
            raise ValueError("issue epoch differs")
        for name in ("occurrence_count", "source_event_count", "recovery_count", "historical_epoch_count", "historical_occurrence_count"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} differs")
        if self.occurrence_count < 1 or self.source_event_count < self.occurrence_count:
            raise ValueError("issue counts differ")
        if len(self.source_event_ids) != self.source_event_count or len(set(self.source_event_ids)) != len(self.source_event_ids):
            raise ValueError("source event identities differ")
        if len(self.source_event_ids) > 4096 or len(self.previous_epochs) > 8 or len(self.evidence) > 15:
            raise ValueError("issue retention bound exceeded")
        for name in ("opened_at", "first_at", "latest_at"):
            _aware_utc(getattr(self, name), name)
        for name in (
            "last_success_at", "recovered_at", "source_as_of", "expected_by",
            "historical_first_at", "historical_latest_at",
        ):
            value = getattr(self, name)
            if value is not None:
                _aware_utc(value, name)
        if self.state == "RECOVERED" and self.recovered_at is None:
            raise ValueError("recovered issue lacks timestamp")
        if self.state != "RECOVERED" and self.recovered_at is not None:
            raise ValueError("active issue has recovered timestamp")
        expected_epoch = self.historical_epoch_count + len(self.previous_epochs) + 1
        if self.epoch != expected_epoch:
            raise ValueError("issue epoch retention differs")
        prior_occurrences = self.historical_occurrence_count
        prior_epoch = self.historical_epoch_count
        for item in self.previous_epochs:
            if type(item) is not dict or set(item) != {"epoch", "opened_at", "recovered_at", "occurrence_count"}:
                raise ValueError("previous issue epoch differs")
            prior_epoch += 1
            if item["epoch"] != prior_epoch or type(item["occurrence_count"]) is not int or item["occurrence_count"] < 1:
                raise ValueError("previous issue epoch count differs")
            _aware_utc(item["opened_at"], "previous epoch opened_at")
            _aware_utc(item["recovered_at"], "previous epoch recovered_at")
            prior_occurrences += item["occurrence_count"]
        if self.occurrence_count <= prior_occurrences:
            raise ValueError("current epoch occurrence count differs")
        expected_recoveries = self.historical_epoch_count + len(self.previous_epochs)
        if self.state == "RECOVERED":
            expected_recoveries += 1
        if self.recovery_count != expected_recoveries:
            raise ValueError("issue recovery count differs")
        if (
            self.historical_epoch_count == 0
            and (self.historical_first_at is not None or self.historical_latest_at is not None)
        ) or (
            self.historical_epoch_count > 0
            and (self.historical_first_at is None or self.historical_latest_at is None)
        ):
            raise ValueError("historical epoch timestamps differ")
        for item in self.evidence:
            validate_evidence_identity(item)
        self._validate_suppression()

    def _validate_suppression(self) -> None:
        value = self.suppression
        if type(value) is not dict or value.get("state") not in {"NONE", "ACTIVE", "EXPIRED", "RELEASED"}:
            raise ValueError("suppression differs")
        history = value.get("history")
        if type(history) is not list or len(history) > 32:
            raise ValueError("suppression history differs")
        state = value["state"]
        if state == "NONE":
            if set(value) != {"state", "history"}:
                raise ValueError("empty suppression fields differ")
        else:
            required = {
                "state", "history", "fingerprint", "suppression_id", "reason_code",
                "started_at", "expires_at", "actor", "evidence",
                "discovery_after_source_event_count",
            }
            if state == "RELEASED":
                required |= {"released_at", "release_reason_code", "release_actor"}
            if set(value) != required:
                raise ValueError("suppression fields differ")
            if value["fingerprint"] != self.fingerprint:
                raise ValueError("suppression fingerprint differs")
            if type(value["suppression_id"]) is not str or not _SUPPRESSION_ID.fullmatch(value["suppression_id"]):
                raise ValueError("suppression id differs")
            if type(value["reason_code"]) is not str or not _REASON.fullmatch(value["reason_code"]):
                raise ValueError("suppression reason differs")
            if type(value["actor"]) is not str or not _ACTOR.fullmatch(value["actor"]):
                raise ValueError("suppression actor differs")
            validate_evidence_identity(value["evidence"])
            started = datetime.fromisoformat(_aware_utc(value["started_at"], "suppression started_at").replace("Z", "+00:00"))
            expires = datetime.fromisoformat(_aware_utc(value["expires_at"], "suppression expires_at").replace("Z", "+00:00"))
            if expires <= started or expires - started > timedelta(days=30):
                raise ValueError("suppression expiry differs")
            gate = value["discovery_after_source_event_count"]
            if type(gate) is not int or gate < 0 or gate > self.source_event_count:
                raise ValueError("suppression discovery gate differs")
            if state == "RELEASED":
                released = datetime.fromisoformat(_aware_utc(value["released_at"], "suppression released_at").replace("Z", "+00:00"))
                if released < started or released > expires:
                    raise ValueError("suppression release time differs")
                if type(value["release_reason_code"]) is not str or not _REASON.fullmatch(value["release_reason_code"]):
                    raise ValueError("suppression release reason differs")
                if type(value["release_actor"]) is not str or not _ACTOR.fullmatch(value["release_actor"]):
                    raise ValueError("suppression release actor differs")
        for item in history:
            base = {
                "state", "fingerprint", "suppression_id", "reason_code", "started_at",
                "expires_at", "actor", "evidence", "discovery_after_source_event_count",
            }
            if type(item) is not dict or item.get("state") not in {"EXPIRED", "RELEASED"}:
                raise ValueError("suppression history entry differs")
            if item["state"] == "RELEASED":
                base |= {"released_at", "release_reason_code", "release_actor"}
            if set(item) != base or item["fingerprint"] != self.fingerprint:
                raise ValueError("suppression history fields differ")
            if type(item["suppression_id"]) is not str or not _SUPPRESSION_ID.fullmatch(item["suppression_id"]):
                raise ValueError("suppression history id differs")
            if type(item["reason_code"]) is not str or not _REASON.fullmatch(item["reason_code"]):
                raise ValueError("suppression history reason differs")
            if type(item["actor"]) is not str or not _ACTOR.fullmatch(item["actor"]):
                raise ValueError("suppression history actor differs")
            validate_evidence_identity(item["evidence"])
            _aware_utc(item["started_at"], "suppression history started_at")
            _aware_utc(item["expires_at"], "suppression history expires_at")
            if type(item["discovery_after_source_event_count"]) is not int:
                raise ValueError("suppression history gate differs")
            if item["state"] == "RELEASED":
                _aware_utc(item["released_at"], "suppression history released_at")
                if type(item["release_reason_code"]) is not str or not _REASON.fullmatch(item["release_reason_code"]):
                    raise ValueError("suppression history release reason differs")
                if type(item["release_actor"]) is not str or not _ACTOR.fullmatch(item["release_actor"]):
                    raise ValueError("suppression history release actor differs")

    def to_dict(self) -> dict[str, object]:
        self.validate()
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "IssueRecord":
        allowed = {field.name for field in cls.__dataclass_fields__.values()}
        if type(value) is not dict or set(value) != allowed:
            raise ValueError("issue record fields differ")
        record = cls(**dict(value))  # type: ignore[arg-type]
        record.validate()
        return record


def _severity_max(left: str, right: str) -> str:
    return SEVERITIES[max(SEVERITIES.index(left), SEVERITIES.index(right))]


def _epoch_occurrences(record: IssueRecord) -> int:
    prior = record.historical_occurrence_count + sum(
        int(item["occurrence_count"]) for item in record.previous_epochs
    )
    current = record.occurrence_count - prior
    if current < 1:
        raise ValueError("current epoch occurrence count differs")
    return current


def suppress_issue(
    record: IssueRecord, *, suppression_id: str, reason_code: str,
    started_at: str, expires_at: str, actor: str, evidence: str,
) -> IssueRecord:
    retained = IssueRecord.from_dict(record.to_dict())
    if retained.suppression["state"] == "ACTIVE":
        raise ValueError("issue already has an active suppression")
    history = list(retained.suppression["history"])
    prior_ids = {item["suppression_id"] for item in history}
    if retained.suppression["state"] in {"EXPIRED", "RELEASED"}:
        prior_ids.add(retained.suppression["suppression_id"])
        history.append({key: value for key, value in retained.suppression.items() if key != "history"})
    if suppression_id in prior_ids:
        raise ValueError("suppression id cannot be reused")
    if len(history) > 32:
        raise ValueError("suppression history retention exhausted")
    retained.suppression = {
        "state": "ACTIVE", "history": history, "fingerprint": retained.fingerprint,
        "suppression_id": suppression_id, "reason_code": reason_code,
        "started_at": _aware_utc(started_at, "suppression started_at"),
        "expires_at": _aware_utc(expires_at, "suppression expires_at"),
        "actor": actor, "evidence": evidence,
        "discovery_after_source_event_count": retained.source_event_count,
    }
    retained.validate()
    return retained


def evaluate_suppression(record: IssueRecord, *, evaluated_at: str) -> IssueRecord:
    retained = IssueRecord.from_dict(record.to_dict())
    if retained.suppression["state"] != "ACTIVE":
        return retained
    instant = _aware_utc(evaluated_at, "suppression evaluated_at")
    if _utc_datetime(instant, "suppression evaluated_at") >= _utc_datetime(
        retained.suppression["expires_at"], "suppression expires_at",
    ):
        retained.suppression["state"] = "EXPIRED"
        retained.suppression["discovery_after_source_event_count"] = retained.source_event_count
        retained.validate()
    return retained


def release_suppression(
    record: IssueRecord, *, released_at: str, reason_code: str, actor: str,
) -> IssueRecord:
    retained = IssueRecord.from_dict(record.to_dict())
    if retained.suppression["state"] != "ACTIVE":
        raise ValueError("only active suppression can be released")
    retained.suppression.update({
        "state": "RELEASED", "released_at": _aware_utc(released_at, "suppression released_at"),
        "release_reason_code": reason_code, "release_actor": actor,
        "discovery_after_source_event_count": retained.source_event_count,
    })
    retained.validate()
    return retained


def aggregate_events(records: Iterable[IssueRecord], events: Iterable[IssueEvent]) -> tuple[IssueRecord, ...]:
    retained = tuple(records)
    indexed = {
        record.fingerprint: IssueRecord.from_dict(record.to_dict())
        for record in retained
    }
    if len(indexed) != len(retained):
        raise ValueError("duplicate issue records")
    for record in indexed.values():
        record.validate()
    for event in sorted(events, key=lambda item: (_utc_datetime(item.occurred_at, "occurred_at"), item.event_identity)):
        record = indexed.get(event.fingerprint)
        if record is None:
            if event.outcome == "SUCCESS":
                continue
            instant = _aware_utc(event.occurred_at, "occurred_at")
            record = IssueRecord(
                fingerprint=event.fingerprint, stable_code=event.stable_code,
                domain=event.domain, target_kind=event.target_kind,
                target_id=event.target_id, state="ACTIVE", severity=event.severity,
                retryability=event.retryability, freshness=event.freshness,
                epoch=1, opened_at=instant, first_at=instant, latest_at=instant,
                occurrence_count=1, source_event_count=1,
                source_event_ids=[event.event_identity], source_as_of=event.source_as_of,
                expected_by=event.expected_by, evidence=list(event.evidence),
                importance=event.importance,
            )
            record.validate()
            indexed[record.fingerprint] = record
            continue
        if event.event_identity in record.source_event_ids:
            continue
        if len(record.source_event_ids) >= 4096:
            raise ValueError("source event identity retention exhausted")
        instant = _aware_utc(event.occurred_at, "occurred_at")
        instant_value = _utc_datetime(instant, "occurred_at")
        latest_value = _utc_datetime(record.latest_at, "latest_at")
        if instant_value < latest_value:
            # A file may be discovered after a newer generation was accepted.
            # It cannot revise counts, recovery, evidence, or current state.
            continue
        if instant_value == latest_value and (
            (event.outcome == "SUCCESS") != (record.state == "RECOVERED")
        ):
            raise ValueError("conflicting issue events share one timestamp")
        record.source_event_ids.append(event.event_identity)
        record.source_event_count += 1
        record.latest_at = instant
        record.source_as_of = event.source_as_of
        record.expected_by = event.expected_by
        for evidence in event.evidence:
            if evidence not in record.evidence:
                record.evidence.append(evidence)
        if len(record.evidence) > 15:
            record.evidence[:] = record.evidence[:1] + record.evidence[-14:]
        record.importance = IMPORTANCE[
            max(IMPORTANCE.index(record.importance), IMPORTANCE.index(event.importance))
        ]
        if event.outcome == "SUCCESS":
            record.last_success_at = instant
            if record.state != "RECOVERED":
                record.state = "RECOVERED"
                record.recovered_at = instant
                record.recovery_count += 1
            record.freshness = event.freshness
        else:
            if record.state == "RECOVERED":
                prior = {
                    "epoch": record.epoch, "opened_at": record.opened_at,
                    "recovered_at": record.recovered_at,
                    "occurrence_count": _epoch_occurrences(record),
                }
                record.previous_epochs.append(prior)
                if len(record.previous_epochs) > 8:
                    expired = record.previous_epochs.pop(0)
                    record.historical_epoch_count += 1
                    record.historical_occurrence_count += int(expired["occurrence_count"])
                    if record.historical_first_at is None:
                        record.historical_first_at = str(expired["opened_at"])
                    record.historical_latest_at = str(expired["recovered_at"])
                record.epoch += 1
                record.opened_at = instant
            record.state = "ACTIVE"
            record.recovered_at = None
            record.occurrence_count += 1
            record.severity = _severity_max(record.severity, event.severity)
            record.retryability = event.retryability
            record.freshness = event.freshness
        record.validate()
    return tuple(sorted(indexed.values(), key=lambda item: item.fingerprint))
