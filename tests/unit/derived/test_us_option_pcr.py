from __future__ import annotations

from copy import deepcopy

import pandas as pd
import pytest

from stock_data.contracts.us_option_pcr import (
    DASHBOARD_US_OPTION_PCR_DAILY,
    ORATS_OPTION_CORE_OBSERVATION,
    US_UNDERLYING_OPTION_PCR_DAILY,
)
from stock_data.derived.us_option_pcr import USOptionPCRError, derive_us_option_pcr
from stock_data.providers.orats_options import parse_cores_payload
from stock_data.published.us_option_pcr import (
    USOptionPCRPublishedError,
    project_us_option_pcr,
)


_CAPTURED = "2026-08-19T02:00:00Z"
_HASH = "a" * 64


def _payload() -> dict[str, object]:
    values = {
        "SPX": (100, 120, 200, 240),
        "QQQ": (0, 75, 60, 90),
        "NDX": (40, 20, 0, 12),
    }
    return {"data": [{
        "ticker": ticker, "tradeDate": "2026-08-18",
        "updatedAt": "2026-08-19T01:15:00Z",
        "cVolu": counts[0], "pVolu": counts[1],
        "cOi": counts[2], "pOi": counts[3],
    } for ticker, counts in values.items()]}


def _normalized() -> pd.DataFrame:
    rows = parse_cores_payload(
        _payload(), captured_at_utc=_CAPTURED, landing_sha256=_HASH,
        provider_snapshot_at_utc="2026-08-19T01:30:00Z",
    )
    return pd.DataFrame(rows, columns=ORATS_OPTION_CORE_OBSERVATION.column_names)


def test_parser_to_derived_to_published_matches_all_three_contracts() -> None:
    payload = _payload()
    before = deepcopy(payload)
    normalized = _normalized()
    derived = derive_us_option_pcr(normalized)
    published = project_us_option_pcr(
        derived, finality_confirmed=True, entitlement_confirmed=True,
        root_scope_confirmed=True,
    )

    assert payload == before
    assert tuple(normalized.columns) == ORATS_OPTION_CORE_OBSERVATION.column_names
    assert tuple(derived.columns) == US_UNDERLYING_OPTION_PCR_DAILY.column_names
    assert tuple(published.columns) == DASHBOARD_US_OPTION_PCR_DAILY.column_names
    assert derived["underlying"].tolist() == ["SPX", "QQQ", "NDX"]
    assert derived["underlying_type"].tolist() == ["INDEX", "ETF", "INDEX"]
    assert derived["provider"].eq("ORATS").all()
    assert derived["scope"].eq("PROVIDER_ALL_LISTED_CHAIN").all()
    assert derived["root_scope_status"].eq("UNCONFIRMED").all()
    assert derived["session"].eq("US_OPTIONS_REGULAR").all()
    assert derived["volume_finality_status"].eq("UNCONFIRMED").all()
    assert derived["open_interest_timing_status"].eq("PROVIDER_DAILY_TAG_AT_CAPTURE").all()
    assert derived["selected_capture_at_utc"].eq(_CAPTURED).all()
    assert derived["available_at_utc"].eq(_CAPTURED).all()
    assert derived["input_dataset"].eq(ORATS_OPTION_CORE_OBSERVATION.name).all()
    assert derived["landing_sha256"].eq(_HASH).all()
    assert derived["revision_status"].eq("UNKNOWN_REVISION").all()
    assert derived["observation_status"].eq("OBSERVED").all()
    assert derived["pit_status"].eq("PIT_BLOCKED_HISTORICAL_AVAILABILITY").all()
    values = derived.set_index("underlying")
    assert values.loc["SPX", "volume_pcr"] == pytest.approx(1.2)
    assert values.loc["SPX", "open_interest_pcr"] == pytest.approx(1.2)
    assert pd.isna(values.loc["QQQ", "volume_pcr"])
    assert pd.isna(values.loc["NDX", "open_interest_pcr"])
    assert published["display_status"].eq("DESCRIPTIVE_ONLY").all()
    assert published["root_scope_status"].eq("EXPLICITLY_CONFIRMED").all()
    assert published["entitlement_status"].eq("EXPLICITLY_CONFIRMED").all()
    assert published["pit_status"].eq("PIT_BLOCKED_HISTORICAL_AVAILABILITY").all()
    pd.testing.assert_series_equal(
        published["volume_pcr"], derived["volume_pcr"], check_names=False,
    )
    pd.testing.assert_series_equal(
        published["open_interest_pcr"], derived["open_interest_pcr"], check_names=False,
    )


@pytest.mark.parametrize("mutation", ["missing", "extra", "duplicate"])
def test_three_ticker_capture_scope_is_atomic(mutation: str) -> None:
    source = _normalized()
    if mutation == "missing":
        source = source.iloc[:-1].copy()
    elif mutation == "extra":
        extra = source.iloc[[0]].copy()
        extra["provider_ticker"] = "IWM"
        source = pd.concat([source, extra], ignore_index=True)
    else:
        source = pd.concat([source, source.iloc[[0]]], ignore_index=True)
    with pytest.raises(USOptionPCRError):
        derive_us_option_pcr(source)


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("call_volume", None), ("put_volume", -1),
        ("call_open_interest", 1.5), ("put_open_interest", float("inf")),
        ("trade_date", "invalid"), ("captured_at_utc", "invalid"),
        ("landing_sha256", "bad"), ("observation_status", "ERROR"),
        ("source", "FALLBACK"), ("asset_type", "INDEX"),
    ],
)
def test_missing_error_and_invalid_inputs_fail_closed(column: str, value: object) -> None:
    source = _normalized()
    source[column] = source[column].astype("object")
    source.loc[source["provider_ticker"].eq("QQQ"), column] = value
    with pytest.raises(USOptionPCRError):
        derive_us_option_pcr(source)


@pytest.mark.parametrize("column", ["trade_date", "captured_at_utc", "landing_sha256"])
def test_atomic_capture_identity_must_match(column: str) -> None:
    source = _normalized()
    replacements = {
        "trade_date": "2026-08-17", "captured_at_utc": "2026-08-19T03:00:00Z",
        "landing_sha256": "b" * 64,
    }
    source.loc[source["provider_ticker"].eq("NDX"), column] = replacements[column]
    with pytest.raises(USOptionPCRError, match="same"):
        derive_us_option_pcr(source)


def test_exact_input_schema_is_required_and_input_is_not_mutated() -> None:
    source = _normalized()
    before = source.copy(deep=True)
    first = derive_us_option_pcr(source)
    second = derive_us_option_pcr(source)
    pd.testing.assert_frame_equal(source, before)
    pd.testing.assert_frame_equal(first, second)
    with pytest.raises(USOptionPCRError, match="columns"):
        derive_us_option_pcr(source.drop(columns="provider_snapshot_at_utc"))


@pytest.mark.parametrize(
    ("finality", "entitlement", "root_scope", "reason"),
    [
        (False, False, False, "VOLUME_FINALITY_UNCONFIRMED;ENTITLEMENT_UNCONFIRMED;ROOT_SCOPE_UNCONFIRMED"),
        (True, False, True, "ENTITLEMENT_UNCONFIRMED"),
        (False, True, True, "VOLUME_FINALITY_UNCONFIRMED"),
        (True, True, False, "ROOT_SCOPE_UNCONFIRMED"),
    ],
)
def test_published_projection_hides_ratios_until_both_gates(
    finality: bool, entitlement: bool, root_scope: bool, reason: str,
) -> None:
    published = project_us_option_pcr(
        derive_us_option_pcr(_normalized()),
        finality_confirmed=finality, entitlement_confirmed=entitlement,
        root_scope_confirmed=root_scope,
    )
    assert published[["volume_pcr", "open_interest_pcr"]].isna().all().all()
    assert published["display_status"].eq("BLOCKED").all()
    assert published["blocked_reason"].eq(reason).all()
    assert published["pit_status"].eq("PIT_BLOCKED_HISTORICAL_AVAILABILITY").all()


@pytest.mark.parametrize(
    "invalid_flags",
    [
        {"finality_confirmed": 1, "entitlement_confirmed": True, "root_scope_confirmed": True},
        {"finality_confirmed": True, "entitlement_confirmed": 1, "root_scope_confirmed": True},
        {"finality_confirmed": True, "entitlement_confirmed": True, "root_scope_confirmed": 1},
    ],
)
def test_published_projection_rejects_implicit_flags(
    invalid_flags: dict[str, object],
) -> None:
    derived = derive_us_option_pcr(_normalized())
    with pytest.raises(USOptionPCRPublishedError, match="explicit bool"):
        project_us_option_pcr(derived, **invalid_flags)


def test_published_projection_rejects_tampering() -> None:
    derived = derive_us_option_pcr(_normalized())
    tampered = derived.copy()
    tampered.loc[0, "volume_pcr"] = 999.0
    with pytest.raises(USOptionPCRPublishedError, match="failed validation"):
        project_us_option_pcr(
            tampered, finality_confirmed=True, entitlement_confirmed=True,
            root_scope_confirmed=True,
        )
