from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from stock_data.contracts.corporate_actions import CORPORATE_ACTION_CONTRACTS
from stock_data.derived.corporate_action_adjustment import (
    ActionType,
    CorporateActionError,
    CorporateActionVersion,
    PROVIDER_NATIVE_UNADJUSTED,
    ProviderNativePrice,
    RevisionState,
    VERIFIED_FINAL,
    adjust_provider_native_prices,
    build_factor_chain,
)


UTC = timezone.utc


def _event(
    *,
    event_id: str = "event-1",
    version: int = 1,
    revises: str | None = None,
    action_type: ActionType = ActionType.SPLIT,
    effective: date = date(2024, 1, 10),
    sequence: int = 0,
    available: datetime = datetime(2024, 1, 5, tzinfo=UTC),
    price_factor: Decimal | None = Decimal("0.5"),
    volume_factor: Decimal | None = Decimal("2"),
    turnover_factor: Decimal | None = Decimal("1"),
    finality: str = VERIFIED_FINAL,
    revision_state: RevisionState = RevisionState.ACTIVE,
    **overrides: object,
) -> CorporateActionVersion:
    values: dict[str, object] = {
        "event_id": event_id,
        "event_version_id": f"{event_id}-v{version}",
        "version_number": version,
        "revises_event_version_id": revises,
        "revision_state": revision_state,
        "security_id": "KR7005930003",
        "security_id_scheme": "ISIN",
        "symbol": "005930",
        "action_type": action_type,
        "announcement_date": date(2024, 1, 2),
        "ex_date": effective,
        "effective_date": effective,
        "same_date_sequence": sequence,
        "price_factor": price_factor,
        "volume_factor": volume_factor,
        "turnover_factor": turnover_factor,
        "cash_amount": None,
        "currency": None,
        "factor_method": "OFFICIAL_VERIFIED_RATIO",
        "predecessor_security_id": None,
        "successor_security_id": None,
        "predecessor_symbol": None,
        "successor_symbol": None,
        "source": "OFFICIAL_TEST_SOURCE",
        "source_event_id": event_id,
        "source_observation_id": f"observation-{event_id}-v{version}",
        "available_at_utc": available,
        "retrieved_at_utc": available,
        "finality": finality,
        "source_filing_date": available.date(),
        "decision_date": date(2024, 1, 2),
        "record_date": effective,
        "payment_date": None,
        "listing_date": None,
        "availability_basis": "SOURCE_FILING_DATE_AND_CAPTURE_FLOOR",
        "source_revision_indicator": None,
        "revision_parent_status": "NOT_APPLICABLE" if version == 1 else "VERIFIED_EXPLICIT",
    }
    values.update(overrides)
    return CorporateActionVersion(**values)  # type: ignore[arg-type]


def _price(
    *,
    market_date: date = date(2024, 1, 9),
    adjustment_status: str = PROVIDER_NATIVE_UNADJUSTED,
) -> ProviderNativePrice:
    return ProviderNativePrice(
        market_date=market_date,
        security_id="KR7005930003",
        symbol="005930",
        open=Decimal("100"),
        high=Decimal("110"),
        low=Decimal("90"),
        close=Decimal("105"),
        volume=Decimal("1000"),
        turnover=Decimal("102000"),
        provider="TEST_NATIVE",
        provider_adjustment_status=adjustment_status,
    )


def _chain(*events: CorporateActionVersion, as_of: datetime | None = None):
    return build_factor_chain(
        events,
        security_id="KR7005930003",
        as_of_knowledge_at_utc=as_of or datetime(2024, 2, 1, tzinfo=UTC),
        through_date=date(2024, 2, 1),
    )


def test_contracts_are_separate_contract_only_event_factor_and_adjusted_views() -> None:
    assert [contract.layer for contract in CORPORATE_ACTION_CONTRACTS] == [
        "normalized", "derived", "derived"
    ]
    assert all(contract.status == "contract_only_canonical_identity_blocked" for contract in CORPORATE_ACTION_CONTRACTS)
    event, factor, adjusted = CORPORATE_ACTION_CONTRACTS
    assert {
        "source_filing_date", "decision_date", "record_date", "ex_date",
        "payment_date", "listing_date", "available_at_utc", "retrieved_at_utc",
        "availability_basis", "source_revision_indicator", "revision_parent_status",
        "revises_event_version_id",
    } <= set(event.column_names)
    assert {"factor_chain_id", "event_version_id", "pit_status"} <= set(factor.column_names)
    assert {"native_close", "adjusted_close", "applied_event_version_ids"} <= set(adjusted.column_names)
    assert "total-return" in adjusted.description


def test_multiple_actions_are_ordered_and_native_ohlcv_stays_value_intact() -> None:
    split = _event(event_id="split", sequence=0)
    rights = _event(
        event_id="rights",
        action_type=ActionType.RIGHTS_ISSUE,
        sequence=1,
        price_factor=Decimal("0.8"),
        volume_factor=Decimal("1.25"),
        turnover_factor=Decimal("1"),
        factor_method="OFFICIAL_THEORETICAL_EX_RIGHT_FACTOR",
    )
    prices = (_price(), _price(market_date=date(2024, 1, 10)))
    original = tuple(prices)
    chain = _chain(rights, split)

    adjusted = adjust_provider_native_prices(prices, chain)

    assert prices == original
    assert [step.event_id for step in chain.steps] == ["split", "rights"]
    assert adjusted[0].adjusted_close == Decimal("42.00")
    assert adjusted[0].adjusted_volume == Decimal("2500.00")
    assert adjusted[0].adjusted_turnover == Decimal("102000")
    assert adjusted[1].adjusted_close == Decimal("105")
    assert adjusted[0].native.close == Decimal("105")
    assert adjusted[0].adjustment_scope == "SPLIT_CAPITAL_ONLY_NOT_TOTAL_RETURN"


def test_same_date_actions_require_unique_explicit_sequence() -> None:
    first = _event(event_id="first", sequence=0)
    second = _event(event_id="second", sequence=0)
    with pytest.raises(CorporateActionError, match="unique explicit sequence"):
        _chain(first, second)


def test_revisions_are_selected_by_pit_availability_and_change_chain_digest() -> None:
    first = _event(event_id="split", version=1)
    correction = _event(
        event_id="split",
        version=2,
        revises="split-v1",
        available=datetime(2024, 1, 15, tzinfo=UTC),
        price_factor=Decimal("0.25"),
        volume_factor=Decimal("4"),
    )
    before = _chain(first, correction, as_of=datetime(2024, 1, 12, tzinfo=UTC))
    after = _chain(first, correction, as_of=datetime(2024, 1, 16, tzinfo=UTC))

    assert before.steps[0].event_version_id == "split-v1"
    assert after.steps[0].event_version_id == "split-v2"
    assert before.factor_chain_id != after.factor_chain_id
    assert adjust_provider_native_prices((_price(),), before)[0].adjusted_close == Decimal("52.5")
    assert adjust_provider_native_prices((_price(),), after)[0].adjusted_close == Decimal("26.25")


def test_provisional_latest_revision_fails_closed_instead_of_using_old_factor() -> None:
    first = _event(event_id="split", version=1)
    provisional = _event(
        event_id="split",
        version=2,
        revises="split-v1",
        available=datetime(2024, 1, 15, tzinfo=UTC),
        finality="PROVISIONAL",
    )
    with pytest.raises(CorporateActionError, match="not verified final"):
        _chain(first, provisional, as_of=datetime(2024, 1, 16, tzinfo=UTC))


def test_cancelled_revision_removes_event_from_as_of_chain() -> None:
    first = _event(event_id="split", version=1)
    cancelled = _event(
        event_id="split",
        version=2,
        revises="split-v1",
        available=datetime(2024, 1, 15, tzinfo=UTC),
        revision_state=RevisionState.CANCELLED,
    )
    chain = _chain(first, cancelled, as_of=datetime(2024, 1, 16, tzinfo=UTC))
    assert chain.selected_events == ()
    assert adjust_provider_native_prices((_price(),), chain)[0].adjusted_close == Decimal("105")


def test_missing_or_contradictory_factors_fail_closed() -> None:
    missing = _event(
        action_type=ActionType.RIGHTS_ISSUE,
        price_factor=None,
        volume_factor=None,
        turnover_factor=None,
    )
    with pytest.raises(CorporateActionError, match="price_factor"):
        _chain(missing)

    contradictory = _event(price_factor=Decimal("0.5"), volume_factor=Decimal("3"))
    with pytest.raises(CorporateActionError, match="reciprocal"):
        _chain(contradictory)


@pytest.mark.parametrize(
    ("action_type", "price_factor", "volume_factor", "method"),
    [
        (ActionType.REVERSE_SPLIT, Decimal("2"), Decimal("0.5"), "OFFICIAL_RATIO"),
        (ActionType.BONUS_ISSUE, Decimal("0.8"), Decimal("1.25"), "OFFICIAL_BONUS_RATIO"),
        (ActionType.CAPITAL_REDUCTION, Decimal("1.2"), Decimal("0.75"), "OFFICIAL_REDUCTION_TERMS"),
    ],
)
def test_remaining_factor_action_families_require_explicit_verified_terms(
    action_type: ActionType,
    price_factor: Decimal,
    volume_factor: Decimal,
    method: str,
) -> None:
    chain = _chain(_event(
        action_type=action_type,
        price_factor=price_factor,
        volume_factor=volume_factor,
        turnover_factor=Decimal("1"),
        factor_method=method,
    ))
    assert chain.steps[0].action_type is action_type
    assert chain.steps[0].factor_method == method


def test_factor_action_rejects_ambiguous_effective_and_ex_dates() -> None:
    with pytest.raises(CorporateActionError, match="ex_date equal to effective_date"):
        _chain(_event(ex_date=date(2024, 1, 9)))


def test_cash_dividend_is_retained_as_marker_but_not_used_as_price_factor() -> None:
    cash = _event(
        action_type=ActionType.CASH_DIVIDEND,
        price_factor=None,
        volume_factor=None,
        turnover_factor=None,
        cash_amount=Decimal("361"),
        currency="KRW",
        factor_method=None,
    )
    chain = _chain(cash)
    assert [event.action_type for event in chain.selected_events] == [ActionType.CASH_DIVIDEND]
    assert chain.steps == ()
    adjusted = adjust_provider_native_prices((_price(),), chain)[0]
    assert adjusted.adjusted_close == adjusted.native.close
    assert adjusted.applied_event_version_ids == ()


def test_provider_preadjusted_or_unknown_prices_are_rejected_to_prevent_double_adjustment() -> None:
    chain = _chain(_event())
    for status in ("PROVIDER_PRE_ADJUSTED", "UNKNOWN", "SOURCE_ADJUSTED_CLOSE_ONLY"):
        with pytest.raises(CorporateActionError, match="double-adjust"):
            adjust_provider_native_prices((_price(adjustment_status=status),), chain)


@pytest.mark.parametrize(
    ("action_type", "overrides"),
    [
        (ActionType.DELISTING, {"predecessor_security_id": "KR7005930003"}),
        (ActionType.RELISTING, {
            "predecessor_security_id": "OLD-ID", "successor_security_id": "KR7005930003"
        }),
        (ActionType.MERGER, {
            "predecessor_security_id": "KR7005930003", "successor_security_id": "NEW-ID"
        }),
        (ActionType.SPINOFF, {
            "predecessor_security_id": "KR7005930003", "successor_security_id": "SPIN-ID"
        }),
    ],
)
def test_identity_discontinuities_never_form_a_continuous_price_series(
    action_type: ActionType, overrides: dict[str, str],
) -> None:
    event = _event(
        action_type=action_type,
        price_factor=None,
        volume_factor=None,
        turnover_factor=None,
        factor_method=None,
        **overrides,
    )
    chain = _chain(event)
    assert chain.discontinuity_event_ids == ("event-1",)
    with pytest.raises(CorporateActionError, match="discontinuity"):
        adjust_provider_native_prices((_price(),), chain)


def test_ticker_change_can_retain_identity_but_never_creates_a_factor() -> None:
    ticker_change = _event(
        action_type=ActionType.TICKER_CHANGE,
        price_factor=None,
        volume_factor=None,
        turnover_factor=None,
        factor_method=None,
        predecessor_symbol="OLD",
        successor_symbol="005930",
    )
    chain = _chain(ticker_change)
    assert chain.steps == ()
    assert chain.discontinuity_event_ids == ()


def test_noncontiguous_or_cross_event_revision_lineage_is_rejected() -> None:
    first = _event(event_id="split", version=1)
    bad = _event(
        event_id="split", version=2, revises="some-other-version",
        available=datetime(2024, 1, 15, tzinfo=UTC),
    )
    with pytest.raises(CorporateActionError, match="revision lineage"):
        _chain(first, bad)


def test_revision_without_official_explicit_parent_edge_fails_closed() -> None:
    first = _event(event_id="split", version=1)
    unlinked = _event(
        event_id="split", version=2, revises="split-v1",
        available=datetime(2024, 1, 15, tzinfo=UTC),
        revision_parent_status="UNAVAILABLE",
    )
    with pytest.raises(CorporateActionError, match="explicit verified parent"):
        _chain(first, unlinked)


def test_same_selected_versions_produce_identical_chain_bytes_semantics() -> None:
    event = _event()
    first = _chain(event)
    second = _chain(replace(event))
    assert first.factor_chain_id == second.factor_chain_id
    assert first.steps == second.steps
