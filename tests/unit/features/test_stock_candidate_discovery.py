from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from stock_research.candidate_discovery import (
    STOCK_CANDIDATE_CONTRACT_VERSION,
    CandidateAxisEvidence,
    StockCandidateEvidence,
    build_unavailable_candidate_view,
    discover_stock_research_candidates,
    validate_candidate_discovery_view,
)


DECISION = "2026-08-27T09:05:00+09:00"
DIGEST = "a" * 64


def axis(role: str, *, state: str = "MATCH", reason: str | None = None):
    binding = {
        "oversold": (
            "kr_equity_adjusted_price_daily", "stock-oversold-axis/v1",
            "stock-oversold-definition/v1",
        ),
        "earnings": (
            "kr_forward_earnings_vintage", "forward-earnings-revision-axis/v1",
            "forward-earnings-revision-definition/v1",
        ),
        "relative_value": (
            "kr_stock_relative_value_daily", "stock-relative-value-axis/v1",
            "stock-relative-value-definition/v1",
        ),
    }[role]
    return CandidateAxisEvidence(
        evidence_id=f"{role}-005930-20260826",
        state=state,
        reason_code=reason,
        source_dataset=binding[0],
        source_contract=binding[1],
        source_version="1",
        input_digest=DIGEST,
        observation_date="2026-08-26",
        provider_published_at_utc="2026-08-26T16:00:00+09:00",
        retrieved_at_utc="2026-08-26T16:01:00+09:00",
        available_at_utc="2026-08-26T16:01:00+09:00",
        usable_from="2026-08-27T09:00:00+09:00",
        pit_status="PIT_SAFE_AS_OF_DECISION",
        freshness_state="CURRENT_AT_DECISION",
        definition_id=binding[2],
        unit="typed_state",
    )


def evidence(symbol: str = "005930", *, market: str = "KOSPI"):
    return StockCandidateEvidence(
        symbol=symbol,
        name="삼성전자" if symbol == "005930" else "테스트",
        market=market,
        isin="KR7005930003" if symbol == "005930" else "KR7000660001",
        security_type="COMMON_STOCK",
        decision_at=DECISION,
        decision_session="2026-08-27",
        universe_date="2026-08-26",
        universe_dataset="kr_equity_canonical_universe_daily",
        universe_version="v1",
        universe_digest="b" * 64,
        universe_pit_status="PIT_SAFE_AS_OF_DECISION",
        oversold=axis("oversold"),
        earnings_revision=axis("earnings"),
        relative_value=axis("relative_value"),
    )


def test_empty_input_is_typed_numeric_free_and_non_recommendation():
    view = discover_stock_research_candidates(())
    assert view.contract_version == STOCK_CANDIDATE_CONTRACT_VERSION
    assert view.availability == "DEPENDENCY_UNAVAILABLE"
    assert view.evaluated_instruments == 0
    assert view.candidates == ()
    assert view.ranking_performed is False
    assert view.recommendation_state == "NOT_A_RECOMMENDATION"


def test_exact_three_axis_match_emits_research_only_candidate_without_rank():
    view = discover_stock_research_candidates((evidence(),))
    assert view.availability == "COMPLETE"
    assert view.evaluated_instruments == 1
    assert len(view.candidates) == 1
    candidate = view.candidates[0]
    assert candidate.symbol == "005930"
    assert candidate.eligibility == "RESEARCH_ONLY"
    assert candidate.conclusion == "EXPLANATORY_CONDITIONS_MATCH"
    assert not hasattr(candidate, "score")
    assert not hasattr(candidate, "rank")


def test_valid_no_match_is_excluded_without_promoting_other_axes():
    row = replace(evidence(), oversold=axis("oversold", state="NO_MATCH"))
    view = discover_stock_research_candidates((row,))
    assert view.availability == "COMPLETE"
    assert view.candidates == ()
    assert view.excluded_reason_counts == (("OVERSOLD_NO_MATCH", 1),)


def test_any_incomplete_axis_withholds_the_entire_current_view():
    incomplete = axis(
        "earnings", state="PIT_BLOCKED", reason="FORWARD_EARNINGS_RIGHTS_BLOCKED"
    )
    row = replace(evidence(), earnings_revision=incomplete)
    view = discover_stock_research_candidates((row,))
    assert view.availability == "DEPENDENCY_UNAVAILABLE"
    assert view.candidates == ()
    assert view.evaluated_instruments == 0
    assert view.unavailable_reasons == ("FORWARD_EARNINGS_RIGHTS_BLOCKED",)


def test_complete_axis_rejects_unsafe_pit_and_future_usable_clock():
    unsafe = replace(axis("earnings"), pit_status="PIT_BLOCKED")
    with pytest.raises(ValueError, match="not PIT-safe"):
        discover_stock_research_candidates((replace(evidence(), earnings_revision=unsafe),))

    future = replace(axis("earnings"), usable_from="2026-08-27T09:06:00+09:00")
    with pytest.raises(ValueError, match="clock is not decision-safe"):
        discover_stock_research_candidates((replace(evidence(), earnings_revision=future),))


def test_duplicates_and_mixed_universe_bindings_fail_closed():
    with pytest.raises(ValueError, match="duplicate"):
        discover_stock_research_candidates((evidence(), evidence()))

    second = replace(
        evidence("000660"), market="KOSPI", universe_digest="c" * 64,
    )
    with pytest.raises(ValueError, match="share decision and universe"):
        discover_stock_research_candidates((evidence(), second))


def test_role_substitution_reuse_and_future_observation_fail_closed():
    substituted = replace(
        axis("oversold"), source_dataset="kr_index_fundamental_daily",
    )
    with pytest.raises(ValueError, match="binding is invalid"):
        discover_stock_research_candidates((replace(evidence(), oversold=substituted),))

    shared = axis("oversold")
    with pytest.raises(ValueError, match="binding is invalid"):
        discover_stock_research_candidates((replace(
            evidence(), oversold=shared, earnings_revision=shared, relative_value=shared,
        ),))

    future = replace(axis("earnings"), observation_date="2099-01-01")
    with pytest.raises(ValueError, match="binding is invalid"):
        discover_stock_research_candidates((replace(evidence(), earnings_revision=future),))


def test_strict_view_rejects_forged_candidate_identity():
    view = discover_stock_research_candidates((evidence(),))
    forged = replace(
        view.candidates[0], symbol="../BAD", market="NYSE", isin="BAD",
    )
    with pytest.raises(ValueError, match="candidate row is invalid"):
        validate_candidate_discovery_view(replace(view, candidates=(forged,)))


def test_unavailable_reason_is_typed_and_views_are_frozen():
    with pytest.raises(ValueError, match="uppercase typed token"):
        build_unavailable_candidate_view(("not typed",))
    view = build_unavailable_candidate_view(("FORWARD_EARNINGS_RIGHTS_BLOCKED",))
    with pytest.raises(FrozenInstanceError):
        view.availability = "COMPLETE"  # type: ignore[misc]
