"""Display-only adapter for retained LS t8412 native 15-minute Raw rows.

This module has no provider transport or credential handling.  It preserves the
source label as an Asia/Seoul timestamp without interpreting it as a bar start
or end, and it never upgrades the retained Raw/PIT-blocked evidence to EOD.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date

import pandas as pd

from stock_data.contracts.kospi200_intraday_pilot import (
    LS_T8412_KOSPI200_CONSTITUENT_15M_PILOT,
    RAW_BAR_TIME_POLICY,
    RAW_REVISION_POLICY,
)
from stock_data.orchestration.automatic_fallback import (
    AttemptFailure,
    FailureKind,
    SourceObservation,
    SourceProvenance,
)
from stock_data.orchestration.current_observation import (
    CurrentObservation,
    CurrentObservationRoute,
    ObservationFinality,
    ObservationInterval,
)
from stock_data.providers.ls_t8412 import (
    CURRENT_SESSION_FINALITY_STATUS,
    FINALITY_STATUS,
    INTERVAL_MINUTES,
    PIT_STATUS,
    PROVIDER,
    SOURCE_OPERATION,
)


class LST8412CurrentObservationError(ValueError):
    """A retained t8412 Raw frame cannot safely form a display observation."""


def _utc_iso(value: object, *, field_name: str) -> str:
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError) as error:
        raise LST8412CurrentObservationError(f"invalid {field_name}") from error
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise LST8412CurrentObservationError(f"{field_name} must be timezone-aware")
    return timestamp.tz_convert("UTC").isoformat()


def _provider_label_timestamp(market_date: object, provider_time: object) -> str:
    time_label = str(provider_time).strip()
    if len(time_label) != 6 or not time_label.isdigit():
        raise LST8412CurrentObservationError("invalid provider_time label")
    try:
        timestamp = pd.Timestamp(
            f"{pd.Timestamp(market_date).date().isoformat()} {time_label}",
            tz="Asia/Seoul",
        )
    except (TypeError, ValueError) as error:
        raise LST8412CurrentObservationError("invalid market date/provider time") from error
    return timestamp.tz_convert("UTC").isoformat()


def adapt_retained_t8412_raw(
    raw: pd.DataFrame,
    *,
    route: CurrentObservationRoute,
    market_date: date,
) -> SourceObservation[CurrentObservation]:
    """Select the latest retained source label for one exact route identity.

    The selected timestamp is only the provider's native label converted from
    Asia/Seoul; it does not assert whether that label marks a bar start or end.
    """
    required = set(LS_T8412_KOSPI200_CONSTITUENT_15M_PILOT.column_names)
    if not required.issubset(raw.columns):
        raise LST8412CurrentObservationError("retained t8412 Raw schema is incomplete")
    if route.identity.market not in {"KOSPI", "XKRX"}:
        raise LST8412CurrentObservationError("t8412 Raw market identity must be KOSPI or XKRX")
    symbol = route.identity.symbol
    if len(symbol) != 6 or not symbol.isdigit():
        raise LST8412CurrentObservationError("t8412 Raw symbol identity must be six digits")

    selected = raw.loc[
        (pd.to_datetime(raw["market_date"], errors="coerce").dt.date == market_date)
        & (raw["market"].astype(str) == "KOSPI")
        & (raw["symbol"].astype(str) == symbol)
    ].copy()
    if selected.empty:
        raise LST8412CurrentObservationError("no retained t8412 Raw rows for exact identity/date")
    if selected["provider_time"].astype(str).duplicated().any():
        raise LST8412CurrentObservationError("duplicate retained t8412 provider label")
    if not selected["provider_symbol"].astype(str).eq(symbol).all():
        raise LST8412CurrentObservationError("retained t8412 provider symbol differs")
    if not selected["provider"].astype(str).eq(PROVIDER).all():
        raise LST8412CurrentObservationError("retained t8412 provider differs")
    if not selected["source_operation"].astype(str).eq(SOURCE_OPERATION).all():
        raise LST8412CurrentObservationError("retained t8412 source operation differs")
    if not selected["bar_time_policy"].astype(str).eq(RAW_BAR_TIME_POLICY).all():
        raise LST8412CurrentObservationError("retained t8412 bar-time policy differs")
    if not selected["revision_policy"].astype(str).eq(RAW_REVISION_POLICY).all():
        raise LST8412CurrentObservationError("retained t8412 revision policy differs")
    if not selected["finality_status"].astype(str).isin({FINALITY_STATUS, CURRENT_SESSION_FINALITY_STATUS}).all():
        raise LST8412CurrentObservationError("retained t8412 finality status differs")
    if not selected["pit_status"].astype(str).eq(PIT_STATUS).all():
        raise LST8412CurrentObservationError("retained t8412 PIT status differs")
    if not selected["interval_minutes"].eq(INTERVAL_MINUTES).all():
        raise LST8412CurrentObservationError("retained t8412 interval is not native 15m")

    selected = selected.sort_values("provider_time", kind="stable")
    row = selected.iloc[-1]
    provider_timestamp_utc = _provider_label_timestamp(row["market_date"], row["provider_time"])
    retrieved_at_utc = _utc_iso(row["captured_at"], field_name="captured_at")
    if pd.Timestamp(provider_timestamp_utc) > pd.Timestamp(retrieved_at_utc):
        raise LST8412CurrentObservationError("provider label is after retained capture")

    observation = CurrentObservation(
        route_id=route.route_id,
        identity=route.identity,
        interval=ObservationInterval.MINUTES_15,
        value=float(row["close"]),
        unit="provider_native_price",
        provider=PROVIDER,
        upstream_provider=PROVIDER,
        source_route=SOURCE_OPERATION,
        provider_timestamp_utc=provider_timestamp_utc,
        retrieved_at_utc=retrieved_at_utc,
        finality=ObservationFinality.AS_RETRIEVED,
    )
    observation.validate()
    return SourceObservation(
        observation,
        SourceProvenance(
            provider=PROVIDER,
            upstream_provider=PROVIDER,
            source_route=SOURCE_OPERATION,
            retrieved_at_utc=retrieved_at_utc,
            request_count=1,
        ),
    )


def retained_t8412_current_attempt(
    raw: pd.DataFrame,
    *,
    route: CurrentObservationRoute,
    market_date: date,
) -> Callable[[], SourceObservation[CurrentObservation]]:
    """Return a local adapter attempt with a typed numeric-free failure path.

    An accepted provenance record retains the original Raw capture's one-source
    request count; invoking this adapter itself performs no provider request.
    """
    def attempt() -> SourceObservation[CurrentObservation]:
        try:
            return adapt_retained_t8412_raw(raw, route=route, market_date=market_date)
        except LST8412CurrentObservationError as error:
            raise AttemptFailure(
                FailureKind.SCHEMA_ERROR,
                safe_code="LS_T8412_RETAINED_RAW_INVALID",
                request_count=0,
            ) from error
    return attempt


__all__ = [
    "LST8412CurrentObservationError",
    "adapt_retained_t8412_raw",
    "retained_t8412_current_attempt",
]
