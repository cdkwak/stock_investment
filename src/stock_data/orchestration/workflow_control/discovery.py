"""Lead-validated discovery projection into non-executable Queue New state."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from typing import Callable, Mapping, Protocol


_TASK_ID = re.compile(r"^RQ-\d{8}T\d{6}-[A-Z0-9]{4}$")
_OWNER = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_FINGERPRINT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$")


class DiscoveryError(ValueError):
    pass


def _overlaps(left: str, right: str) -> bool:
    left_parts = PurePosixPath(left).parts
    right_parts = PurePosixPath(right).parts
    return left_parts[: min(len(left_parts), len(right_parts))] == right_parts[: min(len(left_parts), len(right_parts))]


@dataclass(frozen=True, slots=True)
class ReportedFinding:
    source_task: str
    reported_by_role: str
    lead_owner: str
    lead_generation: str
    title: str
    fingerprint: str
    symptom: str
    evidence: str
    impact: str
    suspected_scope: str
    reproduce: str
    domain: str = "infra"
    priority_hint: str = "P2"

    def __post_init__(self) -> None:
        if _TASK_ID.fullmatch(self.source_task) is None:
            raise DiscoveryError("source_task must be an exact Queue task id")
        if self.reported_by_role not in {"worker", "reviewer", "lead"}:
            raise DiscoveryError("only Worker, Reviewer, or Lead findings may use this path")
        if _OWNER.fullmatch(self.lead_owner) is None:
            raise DiscoveryError("lead_owner must be a bounded owner")
        if not self.lead_generation or len(self.lead_generation) > 128:
            raise DiscoveryError("lead generation is not bounded")
        if _FINGERPRINT.fullmatch(self.fingerprint) is None:
            raise DiscoveryError("fingerprint is not a bounded stable identifier")
        if self.priority_hint not in {"P0", "P1", "P2"}:
            raise DiscoveryError("priority hint is invalid")
        if self.domain not in {"data", "backtest", "gui", "infra", "broker", "research", "integration", "shared"}:
            raise DiscoveryError("domain is invalid")
        for name in ("title", "symptom", "evidence", "impact", "reproduce"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or len(value) > 1000:
                raise DiscoveryError(f"{name} must be bounded non-empty text")
        parsed = PurePosixPath(self.suspected_scope)
        if parsed.is_absolute() or ".." in parsed.parts or "\\" in self.suspected_scope:
            raise DiscoveryError("suspected_scope must be repository-relative POSIX")


@dataclass(frozen=True, slots=True)
class NewDiscoveryCandidate:
    finding: ReportedFinding
    validated_by: str
    intake_role: str = "lead"
    state: str = "new"
    executable: bool = False


def validate_finding(
    finding: ReportedFinding,
    *,
    validated_by: str,
    expected_generation: str,
    active_write_scope: tuple[str, ...],
) -> NewDiscoveryCandidate:
    if validated_by != finding.lead_owner:
        raise DiscoveryError("only the routed Lead may validate the finding")
    if expected_generation != finding.lead_generation:
        raise DiscoveryError("Lead validation generation is stale")
    if any(_overlaps(finding.suspected_scope, owned) for owned in active_write_scope):
        raise DiscoveryError("in-scope findings belong to rework, not New discovery")
    return NewDiscoveryCandidate(finding=finding, validated_by=validated_by)


class NewDiscoverySink(Protocol):
    def create_new(self, candidate: NewDiscoveryCandidate) -> Mapping[str, str]: ...


@dataclass(frozen=True, slots=True)
class DiscoveryReceipt:
    fingerprint: str
    reported_by_role: str
    state: str
    executable: bool
    sink_reference: str
    duplicate: bool
    receipt_digest: str


class LocalNewDiscoverySink:
    """Idempotent fake that models only the Queue ``New`` result."""

    def __init__(self) -> None:
        self._seen: dict[str, str] = {}

    def create_new(self, candidate: NewDiscoveryCandidate) -> Mapping[str, str]:
        finding = candidate.finding
        material = json.dumps(asdict(finding), sort_keys=True, separators=(",", ":"))
        existing = self._seen.get(finding.fingerprint)
        if existing is not None and existing != material:
            raise DiscoveryError("discovery fingerprint conflicts with prior evidence")
        duplicate = existing is not None
        self._seen[finding.fingerprint] = material
        return {
            "state": "new",
            "reference": "local-new-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:20],
            "duplicate": str(duplicate).lower(),
        }


Run = Callable[..., subprocess.CompletedProcess[str]]


class RequestQueueNewDiscoverySink:
    """Call only the canonical manager's ``discover`` transition."""

    def __init__(self, repository_root: Path, *, run: Run = subprocess.run) -> None:
        self.repository_root = Path(repository_root).resolve()
        self.script = self.repository_root / "scripts" / "request_queue.py"
        self.run = run

    def create_new(self, candidate: NewDiscoveryCandidate) -> Mapping[str, str]:
        finding = candidate.finding
        if self.script.is_symlink() or not self.script.is_file():
            raise DiscoveryError("canonical request_queue.py was not found")
        command = [
            sys.executable, str(self.script), "discover",
            "--title", finding.title,
            "--discovered-by", candidate.validated_by,
            "--source-task", finding.source_task,
            "--fingerprint", finding.fingerprint,
            "--symptom", finding.symptom,
            "--evidence", finding.evidence,
            "--impact", finding.impact,
            "--suspected-scope", finding.suspected_scope,
            "--reproduce", finding.reproduce,
            "--priority-hint", finding.priority_hint,
            "--domain", finding.domain,
            "--lead-owner", finding.lead_owner,
            "--intake-role", "lead",
            "--reported-by-role", finding.reported_by_role,
        ]
        completed = self.run(
            command, cwd=self.repository_root, capture_output=True, text=True,
            encoding="utf-8", check=False,
        )
        if completed.returncode != 0:
            raise DiscoveryError(f"Queue discover failed with exit code {completed.returncode}")
        reference = completed.stdout.strip().splitlines()[-1] if completed.stdout.strip() else "queue-new"
        return {"state": "new", "reference": reference, "duplicate": "false"}


class DiscoveryRegistrar:
    def __init__(self, sink: NewDiscoverySink) -> None:
        self.sink = sink

    def register(self, candidate: NewDiscoveryCandidate) -> DiscoveryReceipt:
        if candidate.state != "new" or candidate.executable:
            raise DiscoveryError("validated discoveries must remain non-executable New")
        result = dict(self.sink.create_new(candidate))
        if set(result) != {"state", "reference", "duplicate"} or result["state"] != "new":
            raise DiscoveryError("discovery sink did not return Queue New")
        duplicate = result["duplicate"] == "true"
        material = {
            "executable": False,
            "fingerprint": candidate.finding.fingerprint,
            "reported_by_role": candidate.finding.reported_by_role,
            "sink_reference": result["reference"],
            "state": "new",
        }
        digest = hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return DiscoveryReceipt(
            candidate.finding.fingerprint,
            candidate.finding.reported_by_role,
            "new", False, result["reference"], duplicate, digest,
        )
