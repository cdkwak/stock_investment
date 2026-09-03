"""Contract for dated, research-only analyst target-price consensus."""

from stock_data.contracts.base import ColumnContract, DatasetContract


RESEARCH_TARGET_PRICE_CONSENSUS = DatasetContract(
    name="research_target_price_consensus",
    version=1,
    status="research_only_manual",
    description=(
        "As-retrieved analyst target-price consensus for watchlist securities; "
        "display-only reference data that is never a signal or Backtest input."
    ),
    source="yahoo_finance_quote_summary_or_no_compliant_korean_source",
    layer="normalized",
    storage_format="parquet",
    frequency="research_only",
    timezone="UTC",
    primary_key=("date", "symbol"),
    sort_key=("date", "market", "symbol"),
    partition_by=("year",),
    columns=(
        ColumnContract("date", "date32", False),
        ColumnContract("symbol", "string", False),
        ColumnContract("market", "string", False),
        ColumnContract("source", "string", False),
        ColumnContract("target_mean", "float64", True, "currency per share"),
        ColumnContract("target_high", "float64", True, "currency per share"),
        ColumnContract("target_low", "float64", True, "currency per share"),
        ColumnContract("analyst_count", "int64", True, "analysts"),
        ColumnContract("recommendation_mean", "float64", True, "Yahoo 1-5 scale"),
        ColumnContract("currency", "string", False),
        ColumnContract("retrieved_at", "timestamp[us, UTC]", False),
        ColumnContract("terms_ref", "string", False),
    ),
)


__all__ = ["RESEARCH_TARGET_PRICE_CONSENSUS"]
