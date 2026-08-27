"""KB IVSA0070 adapter for the display-only current-observation boundary.

This adapter deliberately consumes already-normalized KB snapshot frames.  It
does not authenticate, call a provider, read environment variables, or decide
that a capture date is a market date.  A route may use the aware retrieval
instant as its explicit display timestamp when provider event time is absent;
that basis is persisted and never represented as provider event time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import pandas as pd

from stock_data.orchestration.automatic_fallback import SourceObservation, SourceProvenance
from stock_data.orchestration.current_observation import (
    CurrentObservation,
    ObservationFinality,
    ObservationIdentity,
    ObservationInterval,
    ObservationTimestampBasis,
)


KB_PROVIDER = "KB_SECURITIES"
KB_UPSTREAM = "KB_SECURITIES_OPEN_API"
KB_ROUTE = "KBSEC:IVSA0070"
_DISPLAYABLE_DATE_STATUSES = frozenset({
    "CURRENT_DAY_CLOSE", "PREVIOUS_DAY_CLOSE", "INTRADAY_NIGHT", "LAGGED_SOURCE_DATE",
})


@dataclass(frozen=True)
class KBNumericFreeField:
    dataset_id: str
    market: str
    symbol: str
    field: str
    reason: str


@dataclass(frozen=True)
class KBSnapshotAdaptation:
    observations: tuple[SourceObservation[CurrentObservation], ...]
    numeric_free: tuple[KBNumericFreeField, ...]


def adapt_kb_snapshot_frames(
    frames: Mapping[str, pd.DataFrame],
    *,
    provider_timestamp_utc: str | None,
    retrieved_at_utc: str,
    request_count: int,
) -> KBSnapshotAdaptation:
    """Map all seven KB slices to provider-labelled scalar display identities.

    ``provider_timestamp_utc`` is intentionally caller-supplied: IVSA0070's
    retained date token is not a timestamp and must not be converted into one.
    If it is absent, the route uses ``retrieved_at_utc`` with an explicit
    ``RETRIEVAL_TIMESTAMP`` basis for display-only freshness.
    """
    expected = {
        "kb_market_breadth_snapshot", "kb_program_trading_snapshot",
        "kb_investor_flow_snapshot", "kb_market_liquidity_snapshot",
        "kb_derivatives_summary_snapshot", "kb_domestic_index_snapshot",
        "kb_global_symbol_snapshot",
    }
    if set(frames) != expected:
        raise ValueError("KB snapshot frame set does not match seven-slice contract")
    if request_count < 1:
        raise ValueError("KB accepted snapshot requires a positive request count")

    observations: list[SourceObservation[CurrentObservation]] = []
    numeric_free: list[KBNumericFreeField] = []
    for dataset_id, frame in frames.items():
        if not isinstance(frame, pd.DataFrame):
            raise ValueError("KB snapshot slice must be a dataframe")
        for _, row in frame.iterrows():
            market, symbol = _identity_parts(dataset_id, row)
            for field, unit in _numeric_fields(dataset_id):
                value = row.get(field)
                reason = _numeric_free_reason(row, value)
                if reason is not None:
                    numeric_free.append(KBNumericFreeField(dataset_id, market, symbol, field, reason))
                    continue
                route_id = f"kbsec:{dataset_id}:{market}:{symbol}:{field}"
                observation = CurrentObservation(
                    route_id=route_id,
                    identity=ObservationIdentity(dataset_id.upper(), market, symbol),
                    interval=ObservationInterval.SNAPSHOT,
                    value=float(value),
                    unit=unit,
                    provider=KB_PROVIDER,
                    upstream_provider=KB_UPSTREAM,
                    source_route=KB_ROUTE,
                    provider_timestamp_utc=(provider_timestamp_utc or retrieved_at_utc),
                    retrieved_at_utc=retrieved_at_utc,
                    finality=ObservationFinality.PROVISIONAL,
                    timestamp_basis=(
                        ObservationTimestampBasis.PROVIDER_TIMESTAMP
                        if provider_timestamp_utc else
                        ObservationTimestampBasis.RETRIEVAL_TIMESTAMP
                    ),
                )
                observations.append(SourceObservation(
                    observation,
                    SourceProvenance(
                        provider=KB_PROVIDER,
                        upstream_provider=KB_UPSTREAM,
                        source_route=KB_ROUTE,
                        retrieved_at_utc=retrieved_at_utc,
                        request_count=request_count,
                    ),
                ))
    return KBSnapshotAdaptation(tuple(observations), tuple(numeric_free))


def _identity_parts(dataset_id: str, row: pd.Series) -> tuple[str, str]:
    if dataset_id == "kb_market_breadth_snapshot":
        return str(row["market"]), str(row["market"])
    if dataset_id == "kb_investor_flow_snapshot":
        return "XKRX", str(row["investor_code"])
    if dataset_id == "kb_derivatives_summary_snapshot":
        return "XKRX", str(row["instrument_code"])
    if dataset_id == "kb_domestic_index_snapshot":
        return "XKRX", str(row["index_code"])
    if dataset_id == "kb_global_symbol_snapshot":
        return "GLOBAL", _safe_symbol(str(row["symbol_code"]))
    return "XKRX", "MARKET"


def _safe_symbol(value: str) -> str:
    # Snapshot identifiers can contain provider punctuation not admitted by the
    # typed identity.  This is an exact reversible display identity, not a ticker
    # substitution.
    return value.replace("@", ".").replace("/", ".")


def _numeric_fields(dataset_id: str) -> tuple[tuple[str, str], ...]:
    mapping = {
        "kb_market_breadth_snapshot": (("upper_limit", "count"), ("advancing", "count"), ("unchanged", "count"), ("declining", "count"), ("lower_limit", "count")),
        "kb_program_trading_snapshot": (("arbitrage_net_buy", "provider-native"), ("non_arbitrage_net_buy", "provider-native")),
        "kb_investor_flow_snapshot": (("kospi_net_buy", "provider-native"), ("kosdaq_net_buy", "provider-native"), ("futures_net_buy", "provider-native"), ("call_option_net_buy", "provider-native"), ("put_option_net_buy", "provider-native"), ("star_futures_net_buy", "provider-native"), ("stock_futures_net_buy", "provider-native")),
        "kb_market_liquidity_snapshot": tuple((field, "provider-native") for field in ("customer_deposit", "customer_deposit_change", "receivables", "receivables_change", "credit_balance", "credit_balance_change", "futures_deposit", "futures_deposit_change")),
        "kb_derivatives_summary_snapshot": (("current_price", "provider-native"), ("volume", "provider-native"), ("open_interest", "provider-native")),
        "kb_domestic_index_snapshot": (("current_index", "index-points"), ("volume", "provider-native"), ("trading_value", "provider-native")),
        "kb_global_symbol_snapshot": (("current_price", "provider-native"),),
    }
    return mapping[dataset_id]


def _numeric_free_reason(row: pd.Series, value: object) -> str | None:
    if value is None or pd.isna(value):
        return "SOURCE_VALUE_UNAVAILABLE"
    if row.get("availability_status") not in _DISPLAYABLE_DATE_STATUSES:
        return "SLICE_DATE_UNRESOLVED"
    if row.get("value_status") not in {"SOURCE_VALUE", "PARTIAL_UNAVAILABLE"}:
        return "VALUE_STATUS_NOT_DISPLAYABLE"
    return None
