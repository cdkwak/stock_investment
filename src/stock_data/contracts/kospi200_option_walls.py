from __future__ import annotations

from stock_data.contracts.base import ColumnContract, DatasetContract


def _column(
    name: str,
    dtype: str,
    nullable: bool = False,
    unit: str | None = None,
    description: str | None = None,
) -> ColumnContract:
    return ColumnContract(name, dtype, nullable, unit, description)


KR_KOSPI200_OPTION_WALLS_DAILY = DatasetContract(
    name="kr_kospi200_option_walls_daily",
    version=1,
    status="active",
    description=(
        "End-of-day KOSPI200 option open-interest walls by retained maturity month. "
        "This is not an exact-expiry or gamma-wall dataset."
    ),
    source="kr_kospi200_options_provider_bridge_daily",
    layer="derived",
    storage_format="parquet",
    frequency="daily",
    timezone="Asia/Seoul",
    primary_key=("date", "maturity_month"),
    sort_key=("date", "maturity_month"),
    partition_by=("year",),
    columns=(
        _column("date", "date32"),
        _column("maturity_month", "string"),
        _column("bridge_segment", "string"),
        _column("session", "string"),
        _column("source", "string"),
        _column("analysis_status", "string"),
        _column("expiry_status", "string"),
        _column("wall_selection_rule", "string"),
        _column("call_wall_strike", "float64", True, "index_points"),
        _column("call_wall_oi", "int64", True),
        _column("call_wall_volume", "int64", True),
        _column("call_wall_status", "string"),
        _column("call_wall_tie", "bool"),
        _column("call_wall_candidate_count", "int64", False, "strikes"),
        _column("call_wall_candidate_strikes", "string", True),
        _column("put_wall_strike", "float64", True, "index_points"),
        _column("put_wall_oi", "int64", True),
        _column("put_wall_volume", "int64", True),
        _column("put_wall_status", "string"),
        _column("put_wall_tie", "bool"),
        _column("put_wall_candidate_count", "int64", False, "strikes"),
        _column("put_wall_candidate_strikes", "string", True),
        _column("total_call_oi", "int64", True),
        _column("total_put_oi", "int64", True),
        _column("oi_put_call_ratio", "float64", True, "ratio"),
        _column("total_call_volume", "int64", True),
        _column("total_put_volume", "int64", True),
        _column("volume_put_call_ratio", "float64", True, "ratio"),
        _column("call_wall_oi_change_1d", "float64", True),
        _column("put_wall_oi_change_1d", "float64", True),
        _column("call_wall_strike_change_1d", "float64", True, "index_points"),
        _column("put_wall_strike_change_1d", "float64", True, "index_points"),
        _column("underlying_price", "float64", True, "index_points"),
        _column("underlying_dataset", "string", True),
        _column("underlying_source", "string", True),
        _column("underlying_pit_status", "string", True),
        _column("call_wall_distance", "float64", True, "index_points"),
        _column("put_wall_distance", "float64", True, "index_points"),
        _column("call_wall_distance_pct", "float64", True, "percent"),
        _column("put_wall_distance_pct", "float64", True, "percent"),
        _column("call_wall_warning", "string", True),
        _column("put_wall_warning", "string", True),
    ),
)
