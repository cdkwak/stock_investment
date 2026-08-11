"""Contract for an immutable, retained dividend-source observation snapshot.

This is deliberately separate from ``kr_equity_dividend``.  It identifies
what the retained data.go.kr landing file said at one source ``basDt``; it
does not turn that current-universe response into a point-in-time event feed.
"""

from stock_data.contracts.base import ColumnContract, DatasetContract


def _column(name: str, dtype: str, nullable: bool = False, unit: str | None = None):
    return ColumnContract(name, dtype, nullable, unit)


KR_EQUITY_DIVIDEND_SOURCE_OBSERVATION = DatasetContract(
    name="kr_equity_dividend_source_observation",
    version=1,
    status="active",
    description=(
        "Immutable observation of one retained data.go.kr dividend response "
        "snapshot. Not a historical point-in-time dividend-event feed and not "
        "an adjusted-return input."
    ),
    source="data_go_kr:GetStocDiviInfoService_V2/getDiviInfo_V2; retained_landing_snapshot",
    layer="normalized",
    storage_format="parquet",
    frequency="event",
    timezone="Asia/Seoul",
    primary_key=("landing_file_sha256", "source_item_ordinal"),
    sort_key=("landing_file_sha256", "source_item_ordinal"),
    partition_by=("year",),
    columns=(
        _column("source_snapshot_date", "date32"),
        _column("landing_file_sha256", "string"),
        _column("source_response_body_canonical_sha256", "string"),
        _column("source_item_ordinal", "int64"),
        _column("source_page_no", "int64"),
        _column("source_page_item_ordinal", "int64"),
        _column("source_record_canonical_sha256", "string"),
        _column("isin", "string"),
        _column("corp_no", "string", True),
        _column("company", "string"),
        _column("security_type", "string"),
        _column("event_type", "string"),
        _column("dividend_record_date", "string"),
        _column("cash_payment_date", "string", True),
        _column("stock_delivery_date", "string", True),
        _column("ordinary_dividend_amount", "float64", unit="KRW_per_share"),
        _column("ordinary_cash_dividend_ratio", "float64", unit="percent"),
        _column("ordinary_stock_dividend_ratio", "float64", unit="percent"),
        _column("differential_dividend_amount", "float64", unit="KRW_per_share"),
        _column("differential_cash_dividend_ratio", "float64", unit="percent"),
        _column("differential_stock_dividend_ratio", "float64", unit="percent"),
        _column("par_value", "float64", unit="KRW"),
    ),
)


DIVIDEND_OBSERVATION_CONTRACTS = (KR_EQUITY_DIVIDEND_SOURCE_OBSERVATION,)
