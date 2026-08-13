"""Draft source-observation contract for FRED/ALFRED real-time intervals.

This is intentionally not registered yet. Activation requires a bounded live
pilot that demonstrates more than one historical value version or otherwise
establishes that retaining the source real-time interval adds useful evidence.
"""
from stock_data.contracts.base import ColumnContract, DatasetContract


FRED_ALFRED_SERIES_SOURCE_OBSERVATION = DatasetContract(
    name="fred_alfred_series_source_observation",
    version=1,
    status="draft",
    description=(
        "Lossless-normalized FRED/ALFRED observation rows with the source's "
        "inclusive date-level real-time validity interval. This does not replace "
        "the current-value wide FRED datasets and is not intraday PIT evidence."
    ),
    source="fred_api_v1:series/observations",
    layer="normalized",
    storage_format="parquet",
    frequency="source_series",
    timezone=None,
    primary_key=("capture_id", "source_row_ordinal"),
    sort_key=(
        "series_id", "observation_date", "realtime_start", "realtime_end",
        "captured_at_utc", "source_row_ordinal",
    ),
    partition_by=("series_id",),
    columns=(
        ColumnContract("series_id", "string", False),
        ColumnContract("observation_date", "date32", False),
        ColumnContract("realtime_start", "date32", False, description="Inclusive source knowledge-validity start date"),
        ColumnContract("realtime_end", "date32", True, description="Inclusive source knowledge-validity end date; null means source open-ended"),
        ColumnContract("source_realtime_end", "string", False, description="Exact source end token, including open-ended 9999-12-31"),
        ColumnContract("value", "float64", True, description="Untransformed source value; null preserves source '.'"),
        ColumnContract("source_value", "string", False, description="Exact source value token, including '.'"),
        ColumnContract("units", "string", False),
        ColumnContract("frequency", "string", False),
        ColumnContract("seasonal_adjustment", "string", False),
        ColumnContract("source_output_type", "int64", False),
        ColumnContract("capture_id", "string", False),
        ColumnContract("captured_at_utc", "timestamp[ns, UTC]", False),
        ColumnContract("landing_response_sha256", "string", False),
        ColumnContract("source_row_ordinal", "int64", False),
        ColumnContract("availability_precision", "string", False, description="Always source_date_only; no intraday release time is inferred"),
    ),
)
