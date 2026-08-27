"""Local-only, fail-closed Dashboard adapter for an unaccepted VIX futures route."""

from __future__ import annotations

from dataclasses import dataclass
import math

import pandas as pd

from stock_data.contracts.vix_futures import (
    VIXFuturesRouteContract,
    VIXFuturesRouteStatus,
    VIXFuturesSeriesKind,
    YAHOO_CFE_VIX_FUTURES_15M,
    validate_vix_futures_route_contract,
)
from stock_data.gui.services import DashboardDisplayState, DashboardMetricView


@dataclass(frozen=True)
class VIXFuturesLocalObservation:
    provider_symbol: str
    exchange: str
    exchange_product_root: str
    series_kind: VIXFuturesSeriesKind
    contract_symbol: str | None
    expiry: str | None
    roll_policy: str | None
    bar_start: object
    bar_end: object
    retrieved_at: object
    value: float
    freshness: str


@dataclass(frozen=True)
class VIXFuturesDashboardView:
    metric: DashboardMetricView
    route_status: VIXFuturesRouteStatus
    exchange: str
    exchange_product_root: str
    provider_symbol: str | None
    series_kind: VIXFuturesSeriesKind | None
    contract_symbol: str | None
    expiry: str | None
    roll_policy: str | None
    source_timezone: str
    unresolved_rules: tuple[str, ...]


@dataclass(frozen=True)
class VIXSpotFuturesComparison:
    label: str
    value: float
    unit: str
    aligned_timestamp: str
    sign_definition: str


def _unavailable_metric(reason: str) -> DashboardMetricView:
    return DashboardMetricView(
        dataset_id=None, series_id="VIX_FUTURES", label="VIX futures",
        value=None, unit="index points", as_of=None, expected_as_of=None,
        source="No accepted Yahoo-to-CFE VX provider route",
        freshness="NOT_APPLICABLE", pit_status="PIT_BLOCKED",
        pit_label="prediction prohibited", automation_policy="NOT_ELIGIBLE",
        automation_enabled=False, display_state=DashboardDisplayState.PROHIBITED,
        unavailable_reason=reason, route="UNAVAILABLE_YAHOO_CFE_VX",
        source_timestamp=None, delay_status=None, completed_bar=None,
    )


def build_vix_futures_dashboard_view(
    contract: VIXFuturesRouteContract = YAHOO_CFE_VIX_FUTURES_15M,
    observation: VIXFuturesLocalObservation | None = None,
) -> VIXFuturesDashboardView:
    """Build a value only after exact identity, expiry/roll, and completed-bar proof."""
    validate_vix_futures_route_contract(contract)
    if contract.status is VIXFuturesRouteStatus.UNAVAILABLE_IDENTITY_UNVERIFIED:
        metric = _unavailable_metric(
            "Yahoo symbol, returned CFE VX identity, listed expiry or provider roll "
            "policy, and completed CFE session mapping are not evidenced."
        )
    elif observation is None:
        metric = _unavailable_metric("No contract-valid local VIX futures observation is retained.")
    else:
        start = pd.Timestamp(observation.bar_start)
        end = pd.Timestamp(observation.bar_end)
        retrieved = pd.Timestamp(observation.retrieved_at)
        if any(value.tzinfo is None or value.utcoffset() is None for value in (start, end, retrieved)):
            raise ValueError("VIX futures timestamps must be timezone-aware")
        if (
            observation.provider_symbol != contract.provider_symbol
            or observation.provider_symbol == "^VIX"
            or observation.exchange != contract.exchange
            or observation.exchange_product_root != contract.exchange_product_root
            or observation.series_kind is not contract.series_kind
        ):
            raise ValueError("local observation does not match the accepted VX identity")
        if contract.series_kind is VIXFuturesSeriesKind.LISTED_CONTRACT:
            if (
                observation.contract_symbol != contract.contract_symbol
                or observation.expiry != contract.expiry
                or observation.roll_policy is not None
            ):
                raise ValueError("listed VX contract symbol or expiry differs")
        elif (
            observation.contract_symbol is not None
            or observation.expiry is not None
            or observation.roll_policy != contract.roll_policy
        ):
            raise ValueError("provider-continuous VX roll identity differs")
        start, end, retrieved = (
            value.tz_convert("UTC") for value in (start, end, retrieved)
        )
        if (end - start).total_seconds() != 15 * 60 or end > retrieved:
            raise ValueError("VIX futures bar is not one completed native 15-minute bar")
        value = float(observation.value)
        if not math.isfinite(value) or value < 0:
            raise ValueError("VIX futures value is invalid")
        if observation.freshness != "CURRENT":
            metric = _unavailable_metric("The retained VIX futures observation is not current.")
        else:
            identity = (
                f"{contract.contract_symbol} exp {contract.expiry}"
                if contract.series_kind is VIXFuturesSeriesKind.LISTED_CONTRACT
                else f"{contract.provider_symbol} provider-continuous; {contract.roll_policy}"
            )
            metric = DashboardMetricView(
                dataset_id="market_price_15m_observation",
                series_id="VIX_FUTURES", label="VIX futures", value=value,
                unit="index points",
                as_of=end.tz_convert("Asia/Seoul").strftime("%Y-%m-%d %H:%M KST"),
                expected_as_of=None, source=f"Yahoo / CFE VX; {identity}",
                freshness="CURRENT", pit_status="PIT_BLOCKED",
                pit_label="prediction prohibited",
                automation_policy="REVIEWED_NATIVE_15M", automation_enabled=False,
                display_state=DashboardDisplayState.VALUE, unavailable_reason=None,
                route="NORMALIZED_NATIVE_15M_CFE_VX",
                source_timestamp=end.isoformat(),
                delay_status="INDICATIVE_DELAYED_NOT_LICENSED_REALTIME",
                completed_bar=True,
            )
    return VIXFuturesDashboardView(
        metric=metric, route_status=contract.status, exchange=contract.exchange,
        exchange_product_root=contract.exchange_product_root,
        provider_symbol=contract.provider_symbol, series_kind=contract.series_kind,
        contract_symbol=contract.contract_symbol, expiry=contract.expiry,
        roll_policy=contract.roll_policy, source_timezone=contract.source_timezone,
        unresolved_rules=contract.unresolved_rules,
    )


def compare_aligned_spot_and_vix_future(
    spot: DashboardMetricView,
    future: VIXFuturesDashboardView,
) -> VIXSpotFuturesComparison | None:
    """Return future-minus-spot only for exact aligned eligible source observations."""
    future_metric = future.metric
    if (
        spot.series_id != "^VIX"
        or spot.route != "NORMALIZED_NATIVE_15M_CBOE_VIX"
        or not spot.displays_value
        or not future_metric.displays_value
        or spot.freshness != "CURRENT"
        or future_metric.freshness != "CURRENT"
        or spot.completed_bar is not True
        or future_metric.completed_bar is not True
        or not spot.source_timestamp
        or not future_metric.source_timestamp
        or future.route_status is VIXFuturesRouteStatus.UNAVAILABLE_IDENTITY_UNVERIFIED
    ):
        return None
    spot_time = pd.Timestamp(spot.source_timestamp)
    future_time = pd.Timestamp(future_metric.source_timestamp)
    if (
        spot_time.tzinfo is None
        or future_time.tzinfo is None
        or spot_time.tz_convert("UTC") != future_time.tz_convert("UTC")
    ):
        return None
    difference = float(future_metric.value) - float(spot.value)
    return VIXSpotFuturesComparison(
        label="VIX futures minus spot VIX",
        value=difference,
        unit="index points",
        aligned_timestamp=future_time.tz_convert("UTC").isoformat(),
        sign_definition="positive means futures above spot; no term-structure claim",
    )


__all__ = [
    "VIXFuturesDashboardView", "VIXFuturesLocalObservation",
    "VIXSpotFuturesComparison", "build_vix_futures_dashboard_view",
    "compare_aligned_spot_and_vix_future",
]
