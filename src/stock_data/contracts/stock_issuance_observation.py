"""Immutable DATA.GO.KR stock-issuance source-observation contract."""

from stock_data.contracts.base import ColumnContract, DatasetContract


KR_EQUITY_STOCK_ISSUANCE_SOURCE_OBSERVATION = DatasetContract(
    name="kr_equity_stock_issuance_source_observation",
    version=1,
    status="active",
    description=(
        "Immutable rows from a captured getStocIssuInfo_V3 snapshot. Source reference "
        "date and event-effective date remain distinct; historical publication timing "
        "is unknown, so predictive use is blocked."
    ),
    source="data_go_kr:GetStocIssuInfoService_V3/getStocIssuInfo_V3",
    layer="normalized",
    storage_format="parquet",
    frequency="event",
    timezone="Asia/Seoul",
    primary_key=("capture_id", "source_item_ordinal"),
    sort_key=("source_snapshot_date", "source_item_ordinal"),
    partition_by=("year",),
    columns=(
        ColumnContract("source_snapshot_date", "date32", False),
        ColumnContract("capture_id", "string", False),
        ColumnContract("captured_at_utc", "timestamp[ns, UTC]", False),
        ColumnContract("landing_response_sha256", "string", False),
        ColumnContract("source_page_no", "int64", False),
        ColumnContract("source_page_item_ordinal", "int64", False),
        ColumnContract("source_item_ordinal", "int64", False),
        ColumnContract("source_record_sha256", "string", False),
        ColumnContract("corporate_number", "string", False),
        ColumnContract("isin", "string", True),
        ColumnContract("security_name", "string", True),
        ColumnContract("issuer_name", "string", False),
        ColumnContract("securities_classification_code", "string", True),
        ColumnContract("issuance_sequence_no", "string", True),
        ColumnContract("issue_effective_date", "date32", True),
        ColumnContract("issuance_round_no", "string", True),
        ColumnContract("security_type_code", "string", True),
        ColumnContract("security_type_name", "string", True),
        ColumnContract("issuance_reason_code", "string", True),
        ColumnContract("issuance_reason_name", "string", True),
        ColumnContract("issued_shares", "int64", True, "shares"),
        ColumnContract("listing_date", "date32", True),
        ColumnContract("availability_status", "string", False),
    ),
)


STOCK_ISSUANCE_OBSERVATION_CONTRACTS = (
    KR_EQUITY_STOCK_ISSUANCE_SOURCE_OBSERVATION,
)
