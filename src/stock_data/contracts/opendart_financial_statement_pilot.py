"""Raw-only contract for the approval-gated OpenDART financial-statement pilot.

This is deliberately not a canonical financial-statement schema.  Amounts stay
as provider strings and no row is usable by the GUI, automation, or Backtest.
"""

from stock_data.contracts.base import ColumnContract, DatasetContract


_STATUS = "raw_only_approval_gated_pilot"
_UTC = "timestamp[us, UTC]"


KR_OPENDART_FINANCIAL_STATEMENT_FILING_OBSERVATION = DatasetContract(
    name="kr_opendart_financial_statement_filing_observation",
    version=1,
    status=_STATUS,
    description=(
        "One immutable as-retrieved OpenDART single-account response. It is a "
        "source observation, not normalized financial data or a PIT-safe fact set."
    ),
    source="OpenDART fnlttSinglAcnt official endpoint via isolated OpenDartReader",
    layer="raw",
    storage_format="parquet",
    frequency="event",
    timezone="UTC",
    primary_key=(
        "source_operation", "landing_response_body_sha256", "source_item_ordinal",
    ),
    sort_key=("corp_code", "business_year", "report_code", "source_item_ordinal"),
    partition_by=("capture_year",),
    columns=(
        ColumnContract("source_operation", "string", False),
        ColumnContract("landing_response_body_sha256", "string", False),
        ColumnContract("source_item_ordinal", "int32", False),
        ColumnContract("corp_code", "string", False),
        ColumnContract("business_year", "int32", False),
        ColumnContract("report_code", "string", False),
        ColumnContract("financial_statement_division", "string", False),
        ColumnContract("receipt_no", "string", True),
        ColumnContract("statement_division", "string", False),
        ColumnContract("account_id", "string", True),
        ColumnContract("account_name", "string", False),
        ColumnContract("account_detail", "string", True),
        ColumnContract("current_term_name", "string", True),
        ColumnContract("current_term_amount_raw", "string", True),
        ColumnContract("current_term_add_amount_raw", "string", True),
        ColumnContract("prior_term_name", "string", True),
        ColumnContract("prior_term_amount_raw", "string", True),
        ColumnContract("currency", "string", True),
        ColumnContract("display_order", "string", True),
        ColumnContract("revision_parent_receipt_no", "string", True),
        ColumnContract("revision_status", "string", False),
        ColumnContract("observation_time_utc", _UTC, False),
        ColumnContract("provider_published_at_utc", _UTC, True),
        ColumnContract("available_at_utc", _UTC, True),
        ColumnContract("usable_from", "date32", True),
        ColumnContract("pit_status", "string", False),
        ColumnContract("redistribution_status", "string", False),
        ColumnContract("capture_year", "int32", False),
    ),
)


__all__ = ["KR_OPENDART_FINANCIAL_STATEMENT_FILING_OBSERVATION"]
