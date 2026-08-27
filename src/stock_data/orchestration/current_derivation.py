"""Transport-free, atomic derivation gate for current observations."""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Callable
from zoneinfo import ZoneInfo

from stock_data.orchestration.automatic_fallback import (
    CircuitRecord,
    DecisionOutcome,
    ExecutionKind,
    FallbackDecision,
    ProviderRole,
    RoutePolicy,
    SourceObservation,
    SourceProvenance,
)
from stock_data.orchestration.current_observation import (
    CurrentObservation,
    CurrentObservationCoordinator,
    CurrentObservationFileStore,
    CurrentObservationRoute,
    ObservationFinality,
    ObservationIdentity,
    ObservationInterval,
)


KST = ZoneInfo("Asia/Seoul")
MAX_AGE = timedelta(minutes=60)
MAX_SKEW = timedelta(minutes=5)
MAX_PROVENANCE_DEPENDENCIES = 8
MAX_PROVENANCE_BYTES = 4096


class DerivationAvailability(StrEnum):
    CURRENT_ALLOWED = "CURRENT_ALLOWED"
    DAILY_OR_FINALITY_FORBIDDEN = "DAILY_OR_FINALITY_FORBIDDEN"


class DerivationGateError(ValueError):
    pass


@dataclass(frozen=True)
class Dependency:
    identity: ObservationIdentity
    unit: str


@dataclass(frozen=True)
class DerivationPolicy:
    derived_id: str
    dependencies: tuple[Dependency, ...]
    availability: DerivationAvailability
    operation: str
    required_finalities: frozenset[ObservationFinality] = frozenset(
        {
            ObservationFinality.PROVISIONAL,
            ObservationFinality.POST_CLOSE_SNAPSHOT,
            ObservationFinality.AS_RETRIEVED,
            ObservationFinality.FINAL,
        }
    )


@dataclass(frozen=True)
class DerivationResult:
    observation: CurrentObservation | None
    error: str | None
    api_calls: int
    dependency_provenance: tuple[dict[str, object], ...] = ()


def _dependency(dataset: str, market: str, symbol: str, unit: str) -> Dependency:
    return Dependency(ObservationIdentity(dataset, market, symbol), unit)


FORBIDDEN_POLICIES = tuple(
    DerivationPolicy(*row)
    for row in (
        ("vix_percentile_gauge", (_dependency("VIX_CURRENT", "CBOE", "VIX", "index points"),), DerivationAvailability.DAILY_OR_FINALITY_FORBIDDEN, "PERCENTILE"),
        ("vkospi_percentile_gauge", (_dependency("VKOSPI_CURRENT", "XKRX", "1300", "index points"),), DerivationAvailability.DAILY_OR_FINALITY_FORBIDDEN, "PERCENTILE"),
        ("ust10_2_spread", (_dependency("UST_YIELD_CURRENT", "US", "DGS10", "percent"), _dependency("UST_YIELD_CURRENT", "US", "DGS2", "percent")), DerivationAvailability.DAILY_OR_FINALITY_FORBIDDEN, "SUBTRACT"),
        ("kospi200_basis", (_dependency("KR_INDEX_CURRENT", "XKRX", "KOSPI200", "index points"), _dependency("KR_FUTURES_CURRENT", "XKRX", "KOSPI200_NEAREST", "source-native basis")), DerivationAvailability.DAILY_OR_FINALITY_FORBIDDEN, "SUBTRACT"),
        ("kospi200_volume_pc", (_dependency("KR_OPTION_CURRENT", "XKRX", "KOSPI200_CALL_VOLUME", "contracts"), _dependency("KR_OPTION_CURRENT", "XKRX", "KOSPI200_PUT_VOLUME", "contracts")), DerivationAvailability.DAILY_OR_FINALITY_FORBIDDEN, "RATIO"),
        ("kospi200_oi_pc", (_dependency("KR_OPTION_CURRENT", "XKRX", "KOSPI200_CALL_OI", "contracts"), _dependency("KR_OPTION_CURRENT", "XKRX", "KOSPI200_PUT_OI", "contracts")), DerivationAvailability.DAILY_OR_FINALITY_FORBIDDEN, "RATIO"),
        ("call_put_wall", (_dependency("KR_OPTION_CURRENT", "XKRX", "KOSPI200_CALL_OI", "contracts"), _dependency("KR_OPTION_CURRENT", "XKRX", "KOSPI200_PUT_OI", "contracts")), DerivationAvailability.DAILY_OR_FINALITY_FORBIDDEN, "WALL"),
        *((f"kospi200_{name}", (_dependency("KR_CONSTITUENT_CURRENT", "XKRX", "KOSPI200", "membership+close"),), DerivationAvailability.DAILY_OR_FINALITY_FORBIDDEN, "BREADTH") for name in ("advancing", "declining", "unchanged")),
        *((f"kospi_{name}_headline", (_dependency("KR_MARKET_FLOW_FINAL", "XKRX", f"KOSPI_{name.upper()}", "KRW"),), DerivationAvailability.DAILY_OR_FINALITY_FORBIDDEN, "IDENTITY") for name in ("foreign", "institution", "individual")),
    )
)
POLICIES = {policy.derived_id: policy for policy in FORBIDDEN_POLICIES}


def validate_dependencies(
    policy: DerivationPolicy,
    observations: tuple[CurrentObservation, ...],
    *,
    now: datetime,
) -> tuple[datetime, tuple[CurrentObservation, ...]]:
    if policy.availability is not DerivationAvailability.CURRENT_ALLOWED:
        raise DerivationGateError("DAILY_OR_FINALITY_DERIVATION_FORBIDDEN")
    if len(policy.dependencies) > MAX_PROVENANCE_DEPENDENCIES:
        raise DerivationGateError("DEPENDENCY_PROVENANCE_OVERSIZE")
    if now.tzinfo is None or now.utcoffset() is None:
        raise DerivationGateError("CLOCK_TIMEZONE_REQUIRED")
    indexed = {(row.identity, row.unit): row for row in observations}
    if len(indexed) != len(observations):
        raise DerivationGateError("DUPLICATE_DEPENDENCY")
    selected: list[CurrentObservation] = []
    timestamps: list[datetime] = []
    for dependency in policy.dependencies:
        row = indexed.get((dependency.identity, dependency.unit))
        if row is None:
            raise DerivationGateError("DEPENDENCY_IDENTITY_OR_UNIT_MISSING")
        if row.finality not in policy.required_finalities:
            raise DerivationGateError("DEPENDENCY_FINALITY_UNSATISFIED")
        timestamp = datetime.fromisoformat(row.provider_timestamp_utc)
        if (
            timestamp.tzinfo is None
            or timestamp.astimezone(KST).date() != now.astimezone(KST).date()
            or timestamp > now
            or now - timestamp > MAX_AGE
        ):
            raise DerivationGateError("DEPENDENCY_STALE")
        selected.append(row)
        timestamps.append(timestamp)
    if max(timestamps) - min(timestamps) > MAX_SKEW:
        raise DerivationGateError("DEPENDENCY_SKEW_EXCEEDED")
    return min(timestamps), tuple(selected)


def dependency_provenance(
    policy: DerivationPolicy,
    selected: tuple[CurrentObservation, ...],
) -> tuple[dict[str, object], ...]:
    """Build bounded, policy-ordered provenance without truncation."""
    if len(selected) != len(policy.dependencies):
        raise DerivationGateError("DEPENDENCY_PROVENANCE_LENGTH_MISMATCH")
    rows = tuple(
        {
            "identity": asdict(row.identity),
            "unit": row.unit,
            "provider": row.provider,
            "upstream_provider": row.upstream_provider,
            "source_route": row.source_route,
            "provider_timestamp_utc": row.provider_timestamp_utc,
        }
        for row in selected
    )
    encoded = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_PROVENANCE_BYTES:
        raise DerivationGateError("DEPENDENCY_PROVENANCE_OVERSIZE")
    return rows


class CurrentDerivationCoordinator:
    """Atomically commits a locally calculated display-only observation."""

    def __init__(self, store: CurrentObservationFileStore) -> None:
        self._store = store

    @staticmethod
    def route_for(policy: DerivationPolicy, identity: ObservationIdentity) -> CurrentObservationRoute:
        return CurrentObservationRoute(
            RoutePolicy(
                route_id=f"derived-current:{policy.derived_id}",
                primary_provider="DERIVED_LOCAL",
                primary_route=f"DERIVED:{policy.derived_id}",
                fallback_provider="UNAVAILABLE",
                fallback_upstream_provider="UNAVAILABLE",
                fallback_route="UNAVAILABLE",
                fallback_enabled=False,
            ),
            identity,
            (ObservationInterval.SNAPSHOT,),
        )

    def derive(
        self,
        *,
        policy: DerivationPolicy,
        identity: ObservationIdentity,
        unit: str,
        observations: tuple[CurrentObservation, ...],
        now: datetime,
        calculate: Callable[[tuple[CurrentObservation, ...]], float],
    ) -> DerivationResult:
        route = self.route_for(policy, identity)
        prior = self._store.select(route)
        try:
            provider_at, selected = validate_dependencies(policy, observations, now=now)
            provenance = dependency_provenance(policy, selected)
            value = float(calculate(selected))
            if not math.isfinite(value):
                raise DerivationGateError("DERIVED_VALUE_INVALID")
            finality = (
                ObservationFinality.FINAL
                if policy.required_finalities == frozenset({ObservationFinality.FINAL})
                else ObservationFinality.PROVISIONAL
            )
            observation = CurrentObservation(
                route.route_id, identity, ObservationInterval.SNAPSHOT, value, unit,
                "DERIVED_LOCAL", "DERIVED_LOCAL", f"DERIVED:{policy.derived_id}",
                provider_at.astimezone(timezone.utc).isoformat(), now.astimezone(timezone.utc).isoformat(), finality,
            )
            observation.validate()
            source = SourceObservation(
                observation,
                SourceProvenance("DERIVED_LOCAL", "DERIVED_LOCAL", observation.source_route, observation.retrieved_at_utc, 1),
            )
            decision = FallbackDecision(
                route.route_id, ExecutionKind.NORMAL_SCHEDULE, DecisionOutcome.PRIMARY_ACCEPTED,
                ProviderRole.PRIMARY, observation, False, 0, 0, 0, 0,
                CircuitRecord(), CircuitRecord(), (),
            )
            boundary = self._store.promotion_boundary(route)
            snapshot = boundary.snapshot()
            try:
                staged = boundary.stage(source, decision)
                decisions = dict(staged["decisions"])
                decision_payload = dict(decisions[route.route_id])
                decision_payload["derived_dependencies"] = list(provenance)
                decisions[route.route_id] = decision_payload
                staged["decisions"] = decisions
                boundary.commit(staged)
                boundary.verify_readback(source, decision)
                persisted = self._store._read_state()["decisions"].get(route.route_id, {})
                if persisted.get("derived_dependencies") != list(provenance):
                    raise DerivationGateError("DEPENDENCY_PROVENANCE_READBACK_MISMATCH")
            except Exception as error:
                boundary.rollback(snapshot)
                raise DerivationGateError("ATOMIC_DERIVATION_ROLLED_BACK") from error
            return DerivationResult(observation, None, 0, provenance)
        except DerivationGateError as error:
            return DerivationResult(prior, str(error), 0)

    def replay(self, policy: DerivationPolicy, identity: ObservationIdentity) -> DerivationResult:
        route = self.route_for(policy, identity)
        result = CurrentObservationCoordinator(self._store).replay(route)
        if result.observation is None:
            return DerivationResult(None, None, result.api_calls)
        state = self._store._read_state()
        raw = state["decisions"].get(route.route_id, {}).get("derived_dependencies")
        if not isinstance(raw, list):
            return DerivationResult(None, "DEPENDENCY_PROVENANCE_REPLAY_MISSING", result.api_calls)
        return DerivationResult(result.observation, None, result.api_calls, tuple(raw))
