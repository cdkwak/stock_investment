from datetime import datetime, timezone

from stock_data.orchestration.current_derivation import (
    CurrentDerivationCoordinator,
    Dependency,
    DerivationAvailability,
    DerivationPolicy,
    FORBIDDEN_POLICIES,
)
from stock_data.orchestration.current_observation import (
    CurrentObservation,
    CurrentObservationFileStore,
    ObservationFinality,
    ObservationIdentity,
    ObservationInterval,
)


NOW = datetime(2026, 8, 21, 1, 0, tzinfo=timezone.utc)
OUT = ObservationIdentity("DERIVED_CURRENT", "X", "SPREAD")
A = ObservationIdentity("SOURCE_CURRENT", "X", "A")
B = ObservationIdentity("SOURCE_CURRENT", "X", "B")


def observation(identity, timestamp, finality=ObservationFinality.PROVISIONAL):
    return CurrentObservation(
        "source-route", identity, ObservationInterval.SNAPSHOT, 10.0, "points",
        "SOURCE", "SOURCE", "SOURCE:/", timestamp, NOW.isoformat(), finality,
    )


def policy(finalities=frozenset({ObservationFinality.PROVISIONAL})):
    return DerivationPolicy(
        "synthetic_spread", (Dependency(A, "points"), Dependency(B, "points")),
        DerivationAvailability.CURRENT_ALLOWED, "SUBTRACT", finalities,
    )


def test_all_13_ur182_rows_are_explicitly_forbidden():
    assert len(FORBIDDEN_POLICIES) == 13
    assert all(item.availability is DerivationAvailability.DAILY_OR_FINALITY_FORBIDDEN for item in FORBIDDEN_POLICIES)


def test_atomic_success_uses_oldest_timestamp_and_api_zero_replay(tmp_path):
    gate = CurrentDerivationCoordinator(CurrentObservationFileStore(tmp_path / "current.json"))
    result = gate.derive(
        policy=policy(), identity=OUT, unit="points",
        observations=(observation(A, "2026-08-21T00:58:00+00:00"), observation(B, "2026-08-21T01:00:00+00:00")),
        now=NOW, calculate=lambda rows: rows[0].value - rows[1].value,
    )
    assert result.error is None and result.observation.value == 0.0
    assert result.observation.provider_timestamp_utc == "2026-08-21T00:58:00+00:00"
    expected = (
        {"identity": {"dataset_id": "SOURCE_CURRENT", "market": "X", "symbol": "A"}, "unit": "points", "provider": "SOURCE", "upstream_provider": "SOURCE", "source_route": "SOURCE:/", "provider_timestamp_utc": "2026-08-21T00:58:00+00:00"},
        {"identity": {"dataset_id": "SOURCE_CURRENT", "market": "X", "symbol": "B"}, "unit": "points", "provider": "SOURCE", "upstream_provider": "SOURCE", "source_route": "SOURCE:/", "provider_timestamp_utc": "2026-08-21T01:00:00+00:00"},
    )
    assert result.dependency_provenance == expected
    replay = gate.replay(policy(), OUT)
    assert replay.api_calls == 0 and replay.dependency_provenance == expected


def test_finality_stale_and_skew_failures_preserve_prior(tmp_path):
    gate = CurrentDerivationCoordinator(CurrentObservationFileStore(tmp_path / "current.json"))
    prior = gate.derive(policy=policy(), identity=OUT, unit="points", observations=(observation(A, "2026-08-21T00:58:00+00:00"), observation(B, "2026-08-21T01:00:00+00:00")), now=NOW, calculate=lambda rows: 1.0).observation
    strict = policy(frozenset({ObservationFinality.FINAL}))
    for rows in ((observation(A, "2026-08-21T00:58:00+00:00"), observation(B, "2026-08-21T01:00:00+00:00")), (observation(A, "2026-08-20T23:00:00+00:00", ObservationFinality.FINAL), observation(B, "2026-08-21T01:00:00+00:00", ObservationFinality.FINAL)), (observation(A, "2026-08-21T00:50:00+00:00"), observation(B, "2026-08-21T01:00:00+00:00"))):
        result = gate.derive(policy=strict if rows[0].finality is ObservationFinality.PROVISIONAL else policy(), identity=OUT, unit="points", observations=rows, now=NOW, calculate=lambda _: 2.0)
        assert result.observation == prior and result.error is not None and result.api_calls == 0


def test_atomic_write_failure_rolls_back_prior(tmp_path, monkeypatch):
    store = CurrentObservationFileStore(tmp_path / "current.json")
    gate = CurrentDerivationCoordinator(store)
    prior = gate.derive(policy=policy(), identity=OUT, unit="points", observations=(observation(A, "2026-08-21T00:58:00+00:00"), observation(B, "2026-08-21T01:00:00+00:00")), now=NOW, calculate=lambda _: 1.0).observation
    monkeypatch.setattr(store, "_write_state", lambda _: (_ for _ in ()).throw(OSError("write")))
    result = gate.derive(policy=policy(), identity=OUT, unit="points", observations=(observation(A, "2026-08-21T00:58:00+00:00"), observation(B, "2026-08-21T01:00:00+00:00")), now=NOW, calculate=lambda _: 2.0)
    assert result.observation == prior and result.error == "ATOMIC_DERIVATION_ROLLED_BACK"


def test_readback_failure_rolls_back_prior(tmp_path, monkeypatch):
    store = CurrentObservationFileStore(tmp_path / "current.json")
    gate = CurrentDerivationCoordinator(store)
    prior = gate.derive(policy=policy(), identity=OUT, unit="points", observations=(observation(A, "2026-08-21T00:58:00+00:00"), observation(B, "2026-08-21T01:00:00+00:00")), now=NOW, calculate=lambda _: 1.0).observation
    boundary_type = type(store.promotion_boundary(gate.route_for(policy(), OUT)))
    monkeypatch.setattr(boundary_type, "verify_readback", lambda *_: (_ for _ in ()).throw(OSError("readback")))
    result = gate.derive(policy=policy(), identity=OUT, unit="points", observations=(observation(A, "2026-08-21T00:58:00+00:00"), observation(B, "2026-08-21T01:00:00+00:00")), now=NOW, calculate=lambda _: 2.0)
    assert result.observation == prior and result.error == "ATOMIC_DERIVATION_ROLLED_BACK"


def test_oversize_dependency_provenance_rejects_without_truncation(tmp_path):
    identities = tuple(ObservationIdentity("SOURCE_CURRENT", "X", f"S{i}") for i in range(9))
    oversized = DerivationPolicy("oversize", tuple(Dependency(item, "points") for item in identities), DerivationAvailability.CURRENT_ALLOWED, "SUM")
    gate = CurrentDerivationCoordinator(CurrentObservationFileStore(tmp_path / "current.json"))
    result = gate.derive(policy=oversized, identity=OUT, unit="points", observations=(), now=NOW, calculate=lambda _: 0.0)
    assert result.observation is None and result.error == "DEPENDENCY_PROVENANCE_OVERSIZE"
