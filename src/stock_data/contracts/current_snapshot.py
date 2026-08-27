"""Shared metadata columns for provider-specific current snapshots.

These columns standardize observation identity without standardizing values or
coercing unrelated provider/slice dates into one market date.
"""

from stock_data.contracts.base import ColumnContract


CURRENT_SNAPSHOT_METADATA = (
    ColumnContract("capture_date", "date32", False, description="KST capture partition date; not a market date."),
    ColumnContract("collected_at", "timestamp[us, UTC]", False),
    ColumnContract("provider", "string", False),
    ColumnContract("source_operation", "string", False),
    ColumnContract("market_date", "date32", True, description="Slice-specific trading date when verified."),
    ColumnContract("reference_date", "date32", True, description="Provider-reported reference date, if distinct or not yet mapped."),
    ColumnContract("is_provisional", "bool", False),
    ColumnContract("availability_status", "string", False),
    ColumnContract("value_status", "string", False),
)
