from __future__ import annotations

from stock_data.contracts.base import ColumnContract, DatasetContract


KR_INDEX_FUNDAMENTAL_DAILY = DatasetContract(
    name="kr_index_fundamental_daily",
    version=1,
    status="active_descriptive_non_predictive_daily",
    description=(
        "Official KRX broad-index close, weighted PER/PBR, and dividend yield "
        "with every normalized row bound to its retained response digest."
    ),
    source="krx_mdcstat00702",
    layer="normalized",
    storage_format="parquet",
    frequency="daily",
    timezone="Asia/Seoul",
    primary_key=("date", "index_code"),
    sort_key=("date", "index_code"),
    partition_by=("market", "year"),
    columns=(
        ColumnContract("date", "date32", False, "XKRX_session_date"),
        ColumnContract("index_code", "string", False),
        ColumnContract("market", "string", False),
        ColumnContract("close", "float64", False, "index_point"),
        ColumnContract("weighted_per", "float64", True, "ratio"),
        ColumnContract("weighted_pbr", "float64", True, "ratio"),
        ColumnContract("dividend_yield", "float64", True, "percent"),
        ColumnContract("source", "string", False),
        ColumnContract("source_response_sha256", "string", False, "sha256_hex"),
    ),
)


__all__ = ["KR_INDEX_FUNDAMENTAL_DAILY"]
