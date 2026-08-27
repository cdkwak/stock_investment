from dataclasses import dataclass

from stock_data.contracts.base import ColumnContract, DatasetContract


@dataclass(frozen=True)
class Market15mSeriesPolicy:
    series_id: str
    lane_id: str
    source_timezone: str
    observation_calendar: str
    expected_start_policy: str
    session_boundary_policy: str
    market_closed_policy: str
    completed_bar_rule: str = "BAR_END_LE_RETRIEVED_AT"


MARKET_15M_SERIES_POLICIES = {
    series_id: Market15mSeriesPolicy(
        series_id=series_id,
        lane_id="XNYS_MARKET_INDEX",
        source_timezone="America/New_York",
        observation_calendar="XNYS",
        expected_start_policy="XNYS_REGULAR_15M_OPEN_INCLUSIVE_CLOSE_EXCLUSIVE",
        session_boundary_policy=(
            "PROVIDER_CLOSE_BOUNDARY_EXCLUDED" if series_id == "NQ=F"
            else "XNYS_REGULAR_CLOSE_EXCLUSIVE"
        ),
        market_closed_policy="XNYS_CLOSED_API_ZERO",
    )
    for series_id in ("NQ=F", "^IXIC", "^GSPC")
}
MARKET_15M_SERIES_POLICIES["^VIX"] = Market15mSeriesPolicy(
    series_id="^VIX",
    lane_id="CBOE_VIX",
    source_timezone="America/Chicago",
    observation_calendar="CBOE_XNYS_ALIGNED_REGULAR",
    expected_start_policy="XNYS_ALIGNED_15M_OPEN_INCLUSIVE_CLOSE_EXCLUSIVE",
    session_boundary_policy="CBOE_CLOSE_BOUNDARY_OBSERVATION_EXCLUDED",
    market_closed_policy="XNYS_SHARED_HOLIDAY_CLOSED_API_ZERO",
)
for _series_id in ("^FVX", "^TNX", "^TYX"):
    MARKET_15M_SERIES_POLICIES[_series_id] = Market15mSeriesPolicy(
        series_id=_series_id,
        lane_id="YAHOO_TREASURY_QUOTE",
        source_timezone="America/Chicago",
        observation_calendar="PROVIDER_NATIVE_XNYS_BUSINESS_SESSION",
        expected_start_policy="AMERICA_CHICAGO_0820_TO_1350_EVERY_15M",
        session_boundary_policy="PROVIDER_NATIVE_0820_INCLUSIVE_1405_EXCLUSIVE",
        market_closed_policy="XNYS_CLOSED_API_ZERO_EARLY_CLOSE_FAIL_CLOSED",
    )

MARKET_15M_LANE_SERIES = {
    lane_id: tuple(
        series_id
        for series_id, policy in MARKET_15M_SERIES_POLICIES.items()
        if policy.lane_id == lane_id
    )
    for lane_id in ("XNYS_MARKET_INDEX", "CBOE_VIX", "YAHOO_TREASURY_QUOTE")
}


MARKET_PRICE_15M_OBSERVATION = DatasetContract(
    name="market_price_15m_observation",
    version=2,
    status="active",
    description=(
        "Provider-specific completed native 15-minute Yahoo observations for an exact "
        "seven-symbol allowlist split into evidence-backed provider-native lanes. "
        "Values are indicative/delayed provider quotes, not licensed realtime, "
        "official settlement, or official Treasury yields."
    ),
    source="yahoo_chart_api_exact_allowlist",
    layer="normalized",
    storage_format="parquet",
    frequency="intraday",
    timezone="UTC_with_explicit_source_and_display_timezones",
    primary_key=("provider", "series_id", "bar_start"),
    sort_key=("market_date", "series_id", "bar_start"),
    partition_by=("market", "series_id", "year"),
    columns=(
        ColumnContract("market_date", "date32", False),
        ColumnContract("market", "string", False),
        ColumnContract("series_id", "string", False),
        ColumnContract("provider_symbol", "string", False),
        ColumnContract("instrument_type", "string", False),
        ColumnContract("bar_start", "timestamp[us, UTC]", False),
        ColumnContract("bar_end", "timestamp[us, UTC]", False),
        ColumnContract("source_timezone", "string", False),
        ColumnContract("display_timezone", "string", False),
        ColumnContract("session", "string", False),
        ColumnContract("interval", "string", False),
        ColumnContract("open", "float64", False),
        ColumnContract("high", "float64", False),
        ColumnContract("low", "float64", False),
        ColumnContract("close", "float64", False),
        ColumnContract("volume", "int64", True),
        ColumnContract("provider", "string", False),
        ColumnContract("data_availability", "string", False),
        ColumnContract("retrieved_at", "timestamp[us, UTC]", False),
    ),
)


__all__ = [
    "MARKET_15M_LANE_SERIES",
    "MARKET_15M_SERIES_POLICIES",
    "MARKET_PRICE_15M_OBSERVATION",
    "Market15mSeriesPolicy",
]
