from dataclasses import replace

import pytest

from stock_data.contracts.vix_futures import (
    VIXFuturesRouteStatus,
    VIXFuturesSeriesKind,
    YAHOO_CFE_VIX_FUTURES_15M,
)
from stock_data.gui.services import DashboardDisplayState, DashboardMetricView
from stock_data.gui.vix_futures_adapter import (
    VIXFuturesLocalObservation,
    build_vix_futures_dashboard_view,
    compare_aligned_spot_and_vix_future,
)


def _accepted_listed_contract():
    return replace(
        YAHOO_CFE_VIX_FUTURES_15M,
        status=VIXFuturesRouteStatus.ACCEPTED_LISTED_CONTRACT,
        provider_symbol="PROVIDER_LISTED_EXAMPLE",
        series_kind=VIXFuturesSeriesKind.LISTED_CONTRACT,
        contract_symbol="VX/U6",
        expiry="2026-09-16",
        front_month_selection_policy="EXACT_AS_OF_2026_08_20",
        session_contract_status="ACTIVE",
        unresolved_rules=(),
    )


def _observation(**overrides):
    values = {
        "provider_symbol": "PROVIDER_LISTED_EXAMPLE",
        "exchange": "CFE", "exchange_product_root": "VX",
        "series_kind": VIXFuturesSeriesKind.LISTED_CONTRACT,
        "contract_symbol": "VX/U6", "expiry": "2026-09-16",
        "roll_policy": None, "bar_start": "2026-08-20T15:00:00Z",
        "bar_end": "2026-08-20T15:15:00Z",
        "retrieved_at": "2026-08-20T15:30:00Z", "value": 17.0,
        "freshness": "CURRENT",
    }
    values.update(overrides)
    return VIXFuturesLocalObservation(**values)


def _spot(**overrides):
    values = {
        "dataset_id": "market_price_15m_observation", "series_id": "^VIX",
        "label": "VIX intraday (Yahoo ^VIX)", "value": 15.0,
        "unit": "index points", "as_of": "2026-08-21 00:15 KST",
        "expected_as_of": "2026-08-20", "source": "Yahoo ^VIX provider subset",
        "freshness": "CURRENT", "pit_status": "PIT_BLOCKED",
        "pit_label": "prediction prohibited", "automation_policy": "DAILY_NATIVE_15M",
        "automation_enabled": True, "display_state": DashboardDisplayState.VALUE,
        "unavailable_reason": None, "route": "NORMALIZED_NATIVE_15M_CBOE_VIX",
        "source_timestamp": "2026-08-20T15:15:00+00:00",
        "delay_status": "INDICATIVE_DELAYED_NOT_LICENSED_REALTIME",
        "completed_bar": True,
    }
    values.update(overrides)
    return DashboardMetricView(**values)


def test_current_route_is_numeric_free_even_if_a_lookalike_observation_is_supplied() -> None:
    view = build_vix_futures_dashboard_view(
        YAHOO_CFE_VIX_FUTURES_15M, _observation(provider_symbol="^VIX")
    )
    assert view.metric.value is None
    assert not view.metric.displays_value
    assert view.metric.display_state is DashboardDisplayState.PROHIBITED
    assert view.metric.freshness == "NOT_APPLICABLE"
    assert view.provider_symbol is None
    assert "Yahoo symbol" in (view.metric.unavailable_reason or "")


def test_exact_listed_identity_and_completed_native_bar_are_required() -> None:
    contract = _accepted_listed_contract()
    with pytest.raises(ValueError, match="accepted VX identity"):
        build_vix_futures_dashboard_view(
            contract, _observation(exchange="CME")
        )
    with pytest.raises(ValueError, match="completed native 15-minute"):
        build_vix_futures_dashboard_view(
            contract, _observation(retrieved_at="2026-08-20T15:10:00Z")
        )


def test_aligned_comparison_uses_future_minus_spot_without_term_structure_claim() -> None:
    future = build_vix_futures_dashboard_view(
        _accepted_listed_contract(), _observation()
    )
    comparison = compare_aligned_spot_and_vix_future(_spot(), future)
    assert comparison is not None
    assert comparison.value == pytest.approx(2.0)
    assert comparison.label == "VIX futures minus spot VIX"
    assert comparison.sign_definition == (
        "positive means futures above spot; no term-structure claim"
    )


@pytest.mark.parametrize(
    "spot",
    [
        _spot(source_timestamp="2026-08-20T15:00:00Z"),
        _spot(series_id="VIX"),
        _spot(completed_bar=False),
        _spot(value=None, display_state=DashboardDisplayState.UNAVAILABLE),
    ],
)
def test_misaligned_or_ineligible_spot_future_comparison_is_suppressed(spot) -> None:
    future = build_vix_futures_dashboard_view(
        _accepted_listed_contract(), _observation()
    )
    assert compare_aligned_spot_and_vix_future(spot, future) is None


def test_unavailable_route_cannot_be_compared_to_spot_vix() -> None:
    future = build_vix_futures_dashboard_view()
    assert compare_aligned_spot_and_vix_future(_spot(), future) is None
