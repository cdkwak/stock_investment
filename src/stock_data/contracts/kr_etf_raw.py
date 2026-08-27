"""Contract-only schemas for the shared KRX ETF full-market Raw response.

These contracts describe verified provider fields and their lossless logical
projections.  They are intentionally absent from the runtime registry: the
source publication clock, correction freeze, and delisting row-presence
semantics are not closed, so this module grants no collection, normalization,
promotion, predictive-use, or scheduler authority.
"""

from stock_data.contracts.base import ColumnContract, DatasetContract


_STATUS = "contract_only_raw_finality_revision_delisting_review_required"
_SOURCE = "krx_mdcstat04301_via_pykrx"
_STORAGE = "shared_landing_json_reference"
_TIMESTAMP = "timestamp[us, UTC]"


_SHARED_EVIDENCE_COLUMNS = (
    ColumnContract(
        "source_row_ordinal", "int32", False, "1_based_ordinal",
        "Lossless row identity inside the retained provider response.",
    ),
    ColumnContract("landing_path", "string", False),
    ColumnContract("landing_sha256", "string", False, "sha256_hex"),
    ColumnContract("captured_at_utc", _TIMESTAMP, False),
    ColumnContract(
        "finality_status", "string", False, None,
        "OFFICIAL_AFTER_CLOSE_NO_ENDPOINT_PUBLICATION_CLOCK until reviewed.",
    ),
    ColumnContract(
        "revision_status", "string", False, None,
        "UNRESOLVED_NO_OFFICIAL_CORRECTION_FREEZE until reviewed.",
    ),
    ColumnContract(
        "delisting_status", "string", False, None,
        "DATE_SPECIFIC_ROW_ONLY_NO_EFFECTIVE_DATE_INFERENCE until reviewed.",
    ),
)


KR_ETF_UNIVERSE_DAILY_RAW = DatasetContract(
    name="kr_etf_universe_daily",
    version=1,
    status=_STATUS,
    description=(
        "Date-specific full-market ETF identity and size fields logically projected "
        "from one immutable KRX MDCSTAT04301 response. Row presence is evidence only "
        "for that date and is never back-projected across listing or delisting events."
    ),
    source=_SOURCE,
    layer="raw",
    storage_format=_STORAGE,
    frequency="daily",
    timezone="Asia/Seoul",
    primary_key=("market_date", "issue_short_code"),
    sort_key=("market_date", "issue_short_code"),
    partition_by=("market_date",),
    columns=(
        ColumnContract("market_date", "date32", False, "XKRX_session_date"),
        ColumnContract("issue_short_code", "string", False),
        ColumnContract("issue_code", "string", False),
        ColumnContract("security_group_code", "string", False),
        ColumnContract("issue_name", "string", False),
        ColumnContract("close_raw", "string", False, "KRW_per_ETF_security"),
        ColumnContract("nav_per_security_raw", "string", False, "KRW_per_ETF_security"),
        ColumnContract("market_cap_raw", "string", False, "KRW"),
        ColumnContract("total_net_assets_raw", "string", False, "KRW"),
        ColumnContract("listed_securities_raw", "string", False, "ETF_securities"),
        *_SHARED_EVIDENCE_COLUMNS,
    ),
)


KR_ETF_OHLCV_DAILY_RAW = DatasetContract(
    name="kr_etf_ohlcv_daily",
    version=1,
    status=_STATUS,
    description=(
        "Provider-native, unadjusted daily ETF OHLCV, trading value, and per-security "
        "NAV logically projected from the same immutable MDCSTAT04301 bytes as the "
        "dated ETF universe. It is not a second provider observation."
    ),
    source=_SOURCE,
    layer="raw",
    storage_format=_STORAGE,
    frequency="daily",
    timezone="Asia/Seoul",
    primary_key=("market_date", "issue_short_code"),
    sort_key=("market_date", "issue_short_code"),
    partition_by=("market_date",),
    columns=(
        ColumnContract("market_date", "date32", False, "XKRX_session_date"),
        ColumnContract("issue_short_code", "string", False),
        ColumnContract("open_raw", "string", False, "KRW_per_ETF_security"),
        ColumnContract("high_raw", "string", False, "KRW_per_ETF_security"),
        ColumnContract("low_raw", "string", False, "KRW_per_ETF_security"),
        ColumnContract("close_raw", "string", False, "KRW_per_ETF_security"),
        ColumnContract("nav_per_security_raw", "string", False, "KRW_per_ETF_security"),
        ColumnContract("accumulated_volume_raw", "string", False, "ETF_securities"),
        ColumnContract("accumulated_trading_value_raw", "string", False, "KRW"),
        *_SHARED_EVIDENCE_COLUMNS,
    ),
)


KR_ETF_RAW_CONTRACTS = (KR_ETF_UNIVERSE_DAILY_RAW, KR_ETF_OHLCV_DAILY_RAW)


__all__ = [
    "KR_ETF_OHLCV_DAILY_RAW",
    "KR_ETF_RAW_CONTRACTS",
    "KR_ETF_UNIVERSE_DAILY_RAW",
]
