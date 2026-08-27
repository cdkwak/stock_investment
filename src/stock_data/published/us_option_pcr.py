from __future__ import annotations

import numpy as np
import pandas as pd

from stock_data.contracts.us_option_pcr import DASHBOARD_US_OPTION_PCR_DAILY
from stock_data.derived.us_option_pcr import validate_us_option_pcr_derived


class USOptionPCRPublishedError(ValueError):
    """An explicit descriptive publication gate is invalid."""


PUBLISHED_COLUMNS = DASHBOARD_US_OPTION_PCR_DAILY.column_names


def _bool(value: object, name: str) -> bool:
    if not isinstance(value, bool):
        raise USOptionPCRPublishedError(f"{name} must be an explicit bool")
    return value


def project_us_option_pcr(
    derived: pd.DataFrame, *, finality_confirmed: bool, entitlement_confirmed: bool,
    root_scope_confirmed: bool,
) -> pd.DataFrame:
    """Create the exact Dashboard contract, hiding ratios until all gates pass."""
    try:
        validate_us_option_pcr_derived(derived)
    except (TypeError, ValueError) as error:
        raise USOptionPCRPublishedError("derived input failed validation") from error
    finality = _bool(finality_confirmed, "finality_confirmed")
    entitlement = _bool(entitlement_confirmed, "entitlement_confirmed")
    root_scope = _bool(root_scope_confirmed, "root_scope_confirmed")
    allowed = finality and entitlement and root_scope
    reasons = []
    if not finality:
        reasons.append("VOLUME_FINALITY_UNCONFIRMED")
    if not entitlement:
        reasons.append("ENTITLEMENT_UNCONFIRMED")
    if not root_scope:
        reasons.append("ROOT_SCOPE_UNCONFIRMED")
    projection = pd.DataFrame({
        "trade_date": derived["trade_date"].copy(),
        "underlying": derived["underlying"].copy(),
        "underlying_type": derived["underlying_type"].copy(),
        "scope": derived["scope"].copy(),
        "root_scope_status": "EXPLICITLY_CONFIRMED" if root_scope else "UNCONFIRMED",
        "session": derived["session"].copy(),
        "call_volume": derived["call_volume"].copy(), "put_volume": derived["put_volume"].copy(),
        "volume_pcr": derived["volume_pcr"].copy() if allowed else np.nan,
        "volume_finality_status": "EXPLICITLY_CONFIRMED" if finality else "UNCONFIRMED",
        "call_open_interest": derived["call_open_interest"].copy(),
        "put_open_interest": derived["put_open_interest"].copy(),
        "open_interest_pcr": derived["open_interest_pcr"].copy() if allowed else np.nan,
        "open_interest_timing_status": derived["open_interest_timing_status"].copy(),
        "available_at_utc": derived["available_at_utc"].copy(),
        "provider": derived["provider"].copy(), "revision_status": derived["revision_status"].copy(),
        "observation_status": derived["observation_status"].copy(),
        "pit_status": "PIT_BLOCKED_HISTORICAL_AVAILABILITY",
        "input_dataset": derived["input_dataset"].copy(),
        "landing_sha256": derived["landing_sha256"].copy(),
        "entitlement_status": "EXPLICITLY_CONFIRMED" if entitlement else "UNCONFIRMED",
        "display_status": "DESCRIPTIVE_ONLY" if allowed else "BLOCKED",
        "blocked_reason": None if allowed else ";".join(reasons),
        "projection_version": "DASHBOARD_US_OPTION_PCR_V1",
    }, columns=PUBLISHED_COLUMNS)
    return projection


__all__ = ["PUBLISHED_COLUMNS", "USOptionPCRPublishedError", "project_us_option_pcr"]
