"""Typed, PIT-safe boundary for explanatory stock candidate research.

The module is pure and provider-free. It validates already-computed evidence;
it never computes factors, ranks instruments, recommends trades, or fills an
incomplete axis with a neutral value.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as calendar_date
from datetime import datetime
import re
from typing import Iterable


STOCK_CANDIDATE_CONTRACT_VERSION = "stock-candidate-research/v1"
_TOKEN = re.compile(r"[A-Z][A-Z0-9_]{0,63}")
_IDENTITY = re.compile(r"[A-Z0-9][A-Z0-9._-]{0,31}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_ISIN = re.compile(r"[A-Z]{2}[A-Z0-9]{9}[0-9]")
_COMPLETE_AXIS_STATES = frozenset({"MATCH", "NO_MATCH"})
_INCOMPLETE_AXIS_STATES = frozenset({
    "INSUFFICIENT_HISTORY", "UNAVAILABLE", "PIT_BLOCKED", "INVALID",
})
_ALL_AXIS_STATES = _COMPLETE_AXIS_STATES | _INCOMPLETE_AXIS_STATES
_PIT_SAFE = "PIT_SAFE_AS_OF_DECISION"
_CURRENT = "CURRENT_AT_DECISION"
_UNIVERSE_DATASET = "kr_equity_canonical_universe_daily"
_ROLE_BINDINGS = {
    "oversold": (
        "kr_equity_adjusted_price_daily", "stock-oversold-axis/v1",
        "stock-oversold-definition/v1",
    ),
    "earnings_revision": (
        "kr_forward_earnings_vintage", "forward-earnings-revision-axis/v1",
        "forward-earnings-revision-definition/v1",
    ),
    "relative_value": (
        "kr_stock_relative_value_daily", "stock-relative-value-axis/v1",
        "stock-relative-value-definition/v1",
    ),
}


def _aware(value: object, *, field: str) -> datetime:
    if type(value) is not str or "T" not in value:
        raise ValueError(f"{field} must be timezone-aware ISO text")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{field} must be timezone-aware ISO text") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware ISO text")
    return parsed


def _date(value: object, *, field: str) -> str:
    if type(value) is not str:
        raise ValueError(f"{field} must be canonical YYYY-MM-DD")
    try:
        parsed = calendar_date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{field} must be canonical YYYY-MM-DD") from error
    if parsed.isoformat() != value:
        raise ValueError(f"{field} must be canonical YYYY-MM-DD")
    return value


def _token(value: object, *, field: str) -> str:
    if type(value) is not str or _TOKEN.fullmatch(value) is None:
        raise ValueError(f"{field} must be an uppercase typed token")
    return value


def _nonempty(value: object, *, field: str) -> str:
    if type(value) is not str or not value.strip() or value != value.strip():
        raise ValueError(f"{field} is invalid")
    return value


@dataclass(frozen=True, slots=True)
class CandidateAxisEvidence:
    evidence_id: str
    state: str
    reason_code: str | None
    source_dataset: str
    source_contract: str
    source_version: str
    input_digest: str
    observation_date: str
    provider_published_at_utc: str
    retrieved_at_utc: str
    available_at_utc: str
    usable_from: str
    pit_status: str
    freshness_state: str
    definition_id: str
    unit: str

    def validate_for(self, decision_at: datetime, *, role: str) -> None:
        for field, value in (
            ("evidence_id", self.evidence_id),
            ("source_dataset", self.source_dataset),
            ("source_contract", self.source_contract),
            ("source_version", self.source_version),
            ("definition_id", self.definition_id),
            ("unit", self.unit),
        ):
            _nonempty(value, field=f"{role} {field}")
        if self.state not in _ALL_AXIS_STATES:
            raise ValueError(f"{role} state is invalid")
        if type(self.input_digest) is not str or _DIGEST.fullmatch(self.input_digest) is None:
            raise ValueError(f"{role} input digest is invalid")
        _date(self.observation_date, field=f"{role} observation_date")
        published = _aware(
            self.provider_published_at_utc, field=f"{role} provider_published_at_utc"
        )
        retrieved = _aware(self.retrieved_at_utc, field=f"{role} retrieved_at_utc")
        available = _aware(self.available_at_utc, field=f"{role} available_at_utc")
        usable = _aware(self.usable_from, field=f"{role} usable_from")
        if not (published <= retrieved <= available <= usable <= decision_at):
            raise ValueError(f"{role} evidence clock is not decision-safe")
        if self.state in _COMPLETE_AXIS_STATES:
            if (
                self.reason_code is not None
                or self.pit_status != _PIT_SAFE
                or self.freshness_state != _CURRENT
            ):
                raise ValueError(f"{role} complete evidence is not PIT-safe and current")
        else:
            _token(self.reason_code, field=f"{role} reason_code")


@dataclass(frozen=True, slots=True)
class StockCandidateEvidence:
    symbol: str
    name: str
    market: str
    isin: str
    security_type: str
    decision_at: str
    decision_session: str
    universe_date: str
    universe_dataset: str
    universe_version: str
    universe_digest: str
    universe_pit_status: str
    oversold: CandidateAxisEvidence
    earnings_revision: CandidateAxisEvidence
    relative_value: CandidateAxisEvidence

    def validate(self) -> datetime:
        if type(self.symbol) is not str or _IDENTITY.fullmatch(self.symbol) is None:
            raise ValueError("stock candidate symbol is invalid")
        _nonempty(self.name, field="stock candidate name")
        if self.market not in {"KOSPI", "KOSDAQ"}:
            raise ValueError("stock candidate market is unsupported")
        if type(self.isin) is not str or _ISIN.fullmatch(self.isin) is None:
            raise ValueError("stock candidate ISIN is invalid")
        if self.security_type != "COMMON_STOCK":
            raise ValueError("stock candidate security type is unsupported")
        decision = _aware(self.decision_at, field="stock candidate decision_at")
        decision_session = _date(self.decision_session, field="decision_session")
        universe_date = _date(self.universe_date, field="universe_date")
        if universe_date > decision_session:
            raise ValueError("stock candidate universe date is future")
        for field, value in (
            ("universe_dataset", self.universe_dataset),
            ("universe_version", self.universe_version),
        ):
            _nonempty(value, field=field)
        if (
            type(self.universe_digest) is not str
            or _DIGEST.fullmatch(self.universe_digest) is None
            or self.universe_dataset != _UNIVERSE_DATASET
            or self.universe_pit_status != _PIT_SAFE
        ):
            raise ValueError("stock candidate universe binding is not PIT-safe")
        evidence_ids: set[str] = set()
        for role, evidence in self.axis_evidence:
            if type(evidence) is not CandidateAxisEvidence:
                raise ValueError(f"{role} evidence type is invalid")
            evidence.validate_for(decision, role=role)
            expected_dataset, expected_contract, expected_definition = _ROLE_BINDINGS[role]
            if (
                evidence.source_dataset != expected_dataset
                or evidence.source_contract != expected_contract
                or evidence.definition_id != expected_definition
                or evidence.observation_date > decision_session
                or evidence.evidence_id in evidence_ids
            ):
                raise ValueError(f"{role} evidence binding is invalid")
            evidence_ids.add(evidence.evidence_id)
        return decision

    @property
    def axis_evidence(self) -> tuple[tuple[str, CandidateAxisEvidence], ...]:
        return (
            ("oversold", self.oversold),
            ("earnings_revision", self.earnings_revision),
            ("relative_value", self.relative_value),
        )


@dataclass(frozen=True, slots=True)
class StockResearchCandidate:
    symbol: str
    name: str
    market: str
    isin: str
    decision_at: str
    oversold_state: str
    earnings_revision_state: str
    relative_value_state: str
    evidence_ids: tuple[str, ...]
    conclusion: str = "EXPLANATORY_CONDITIONS_MATCH"
    eligibility: str = "RESEARCH_ONLY"


@dataclass(frozen=True, slots=True)
class StockCandidateDiscoveryView:
    contract_version: str
    availability: str
    decision_at: str | None
    evaluated_instruments: int
    candidates: tuple[StockResearchCandidate, ...]
    excluded_reason_counts: tuple[tuple[str, int], ...]
    unavailable_reasons: tuple[str, ...]
    ranking_performed: bool = False
    recommendation_state: str = "NOT_A_RECOMMENDATION"


def validate_candidate_discovery_view(view: object) -> StockCandidateDiscoveryView:
    if (
        type(view) is not StockCandidateDiscoveryView
        or view.contract_version != STOCK_CANDIDATE_CONTRACT_VERSION
        or view.ranking_performed is not False
        or view.recommendation_state != "NOT_A_RECOMMENDATION"
        or view.availability not in {"COMPLETE", "DEPENDENCY_UNAVAILABLE"}
        or type(view.evaluated_instruments) is not int
        or view.evaluated_instruments < 0
    ):
        raise ValueError("stock candidate view envelope is invalid")
    if view.availability == "DEPENDENCY_UNAVAILABLE":
        if (
            view.decision_at is not None
            or view.evaluated_instruments != 0
            or view.candidates
            or not view.unavailable_reasons
        ):
            raise ValueError("unavailable stock candidate view is invalid")
        for reason in view.unavailable_reasons:
            _token(reason, field="unavailable reason")
        return view
    decision = _aware(view.decision_at, field="stock candidate view decision_at")
    if view.unavailable_reasons or view.evaluated_instruments < len(view.candidates):
        raise ValueError("complete stock candidate view is inconsistent")
    identities: set[tuple[str, str]] = set()
    for candidate in view.candidates:
        if (
            type(candidate) is not StockResearchCandidate
            or type(candidate.symbol) is not str
            or _IDENTITY.fullmatch(candidate.symbol) is None
            or type(candidate.name) is not str
            or not candidate.name.strip()
            or candidate.name != candidate.name.strip()
            or candidate.market not in {"KOSPI", "KOSDAQ"}
            or type(candidate.isin) is not str
            or _ISIN.fullmatch(candidate.isin) is None
            or candidate.eligibility != "RESEARCH_ONLY"
            or candidate.conclusion != "EXPLANATORY_CONDITIONS_MATCH"
            or candidate.oversold_state != "MATCH"
            or candidate.earnings_revision_state != "MATCH"
            or candidate.relative_value_state != "MATCH"
            or _aware(candidate.decision_at, field="candidate decision_at") != decision
            or len(candidate.evidence_ids) != 3
            or len(set(candidate.evidence_ids)) != 3
            or any(
                type(evidence_id) is not str or not evidence_id.strip()
                for evidence_id in candidate.evidence_ids
            )
        ):
            raise ValueError("stock research candidate row is invalid")
        identity = (candidate.market, candidate.symbol)
        if identity in identities:
            raise ValueError("duplicate stock research candidate row")
        identities.add(identity)
    return view


def build_unavailable_candidate_view(
    reasons: Iterable[str],
) -> StockCandidateDiscoveryView:
    exact = tuple(dict.fromkeys(_token(reason, field="unavailable reason") for reason in reasons))
    if not exact:
        raise ValueError("at least one unavailable reason is required")
    return StockCandidateDiscoveryView(
        contract_version=STOCK_CANDIDATE_CONTRACT_VERSION,
        availability="DEPENDENCY_UNAVAILABLE",
        decision_at=None,
        evaluated_instruments=0,
        candidates=(),
        excluded_reason_counts=(),
        unavailable_reasons=exact,
    )


def discover_stock_research_candidates(
    observations: Iterable[StockCandidateEvidence],
) -> StockCandidateDiscoveryView:
    """Apply the fixed three-axis conjunction without scores or ranking."""
    rows = tuple(observations)
    if not rows:
        return build_unavailable_candidate_view(("NO_VALIDATED_STOCK_EVIDENCE",))

    decisions: set[str] = set()
    universe_bindings: set[tuple[str, str, str, str]] = set()
    identities: set[tuple[str, str]] = set()
    candidates: list[StockResearchCandidate] = []
    excluded: dict[str, int] = {}
    unavailable: set[str] = set()
    for row in rows:
        if type(row) is not StockCandidateEvidence:
            raise ValueError("stock candidate evidence type is invalid")
        row.validate()
        decisions.add(row.decision_at)
        universe_bindings.add((
            row.universe_date, row.universe_dataset,
            row.universe_version, row.universe_digest,
        ))
        identity = (row.market, row.symbol)
        if identity in identities:
            raise ValueError("duplicate stock candidate identity")
        identities.add(identity)
        incomplete = tuple(
            evidence for _, evidence in row.axis_evidence
            if evidence.state in _INCOMPLETE_AXIS_STATES
        )
        if incomplete:
            unavailable.update(evidence.reason_code or evidence.state for evidence in incomplete)
            continue
        no_match = tuple(
            role for role, evidence in row.axis_evidence if evidence.state == "NO_MATCH"
        )
        if no_match:
            for role in no_match:
                reason = f"{role.upper()}_NO_MATCH"
                excluded[reason] = excluded.get(reason, 0) + 1
            continue
        candidates.append(StockResearchCandidate(
            symbol=row.symbol,
            name=row.name,
            market=row.market,
            isin=row.isin,
            decision_at=row.decision_at,
            oversold_state=row.oversold.state,
            earnings_revision_state=row.earnings_revision.state,
            relative_value_state=row.relative_value.state,
            evidence_ids=tuple(evidence.evidence_id for _, evidence in row.axis_evidence),
        ))
    if len(decisions) != 1 or len(universe_bindings) != 1:
        raise ValueError("stock candidate observations must share decision and universe")
    if unavailable:
        return build_unavailable_candidate_view(tuple(sorted(unavailable)))
    candidates.sort(key=lambda item: (item.market, item.symbol))
    return StockCandidateDiscoveryView(
        contract_version=STOCK_CANDIDATE_CONTRACT_VERSION,
        availability="COMPLETE",
        decision_at=next(iter(decisions)),
        evaluated_instruments=len(rows),
        candidates=tuple(candidates),
        excluded_reason_counts=tuple(sorted(excluded.items())),
        unavailable_reasons=(),
    )
