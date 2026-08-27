"""Contract-only observations for the bounded OpenDART action intake.

These contracts retain source identity and knowledge-time facts.  They do not
define a canonical action, adjustment factor, or continuous security history.
"""

from stock_data.contracts.base import ColumnContract, DatasetContract


_STATUS = "contract_only_source_observation_pilot"
_UTC = "timestamp[us, UTC]"


KR_OPENDART_SECURITY_IDENTITY_OBSERVATION = DatasetContract(
    name="kr_opendart_security_identity_observation",
    version=1,
    status=_STATUS,
    description=(
        "Effective-date-capable issuer/security identity observations. Unknown "
        "effective dates, ISINs, classes, and predecessor/successor edges stay null."
    ),
    source="OpenDART list plus separately verified KRX/KIND evidence",
    layer="raw",
    storage_format="parquet",
    frequency="event",
    timezone="UTC",
    primary_key=("identity_observation_id",),
    sort_key=("corp_code", "observed_at_utc", "identity_observation_id"),
    partition_by=("observation_year",),
    columns=(
        ColumnContract("identity_observation_id", "string", False),
        ColumnContract("corp_code", "string", False),
        ColumnContract("stock_code", "string", True),
        ColumnContract("market", "string", True),
        ColumnContract("corp_cls", "string", True),
        ColumnContract("security_class", "string", True),
        ColumnContract("isin", "string", True),
        ColumnContract("valid_from", "date32", True),
        ColumnContract("valid_to", "date32", True),
        ColumnContract("effective_date_basis", "string", False),
        ColumnContract("predecessor_security_id", "string", True),
        ColumnContract("successor_security_id", "string", True),
        ColumnContract("relationship_type", "string", True),
        ColumnContract("official_event_id", "string", True),
        ColumnContract("source", "string", False),
        ColumnContract("source_receipt_no", "string", True),
        ColumnContract("landing_response_body_sha256", "string", False),
        ColumnContract("source_item_ordinal", "int32", False),
        ColumnContract("observed_at_utc", _UTC, False),
        ColumnContract("identity_status", "string", False),
        ColumnContract("observation_year", "int32", False),
    ),
)


KR_OPENDART_CORPORATE_ACTION_FILING_VERSION_OBSERVATION = DatasetContract(
    name="kr_opendart_corporate_action_filing_version_observation",
    version=1,
    status=_STATUS,
    description=(
        "Append-only OpenDART filing/version observations with conservative "
        "knowledge time and no inferred correction parent or event identity."
    ),
    source="OpenDART list and event-family decision endpoints",
    layer="raw",
    storage_format="parquet",
    frequency="event",
    timezone="UTC",
    primary_key=(
        "source_operation", "landing_response_body_sha256", "source_item_ordinal",
    ),
    sort_key=("receipt_date", "receipt_no", "source_operation", "source_item_ordinal"),
    partition_by=("receipt_year",),
    columns=(
        ColumnContract("source_operation", "string", False),
        ColumnContract("landing_response_body_sha256", "string", False),
        ColumnContract("source_item_ordinal", "int32", False),
        ColumnContract("receipt_no", "string", False),
        ColumnContract("receipt_date", "date32", False),
        ColumnContract("receipt_timestamp_utc", _UTC, True),
        ColumnContract("corp_code", "string", False),
        ColumnContract("stock_code", "string", True),
        ColumnContract("corp_cls", "string", True),
        ColumnContract("report_name", "string", True),
        ColumnContract("revision_indicator", "string", True),
        ColumnContract("event_family", "string", False),
        ColumnContract("original_receipt_no", "string", True),
        ColumnContract("revises_receipt_no", "string", True),
        ColumnContract("revision_parent_status", "string", False),
        ColumnContract("observation_time_utc", _UTC, False),
        ColumnContract("available_at_utc", _UTC, False),
        ColumnContract("usable_from", "date32", False),
        ColumnContract("availability_basis", "string", False),
        ColumnContract("source_version", "string", False),
        ColumnContract("source_status", "string", False),
        ColumnContract("receipt_year", "int32", False),
    ),
)


__all__ = [
    "KR_OPENDART_CORPORATE_ACTION_FILING_VERSION_OBSERVATION",
    "KR_OPENDART_SECURITY_IDENTITY_OBSERVATION",
]
