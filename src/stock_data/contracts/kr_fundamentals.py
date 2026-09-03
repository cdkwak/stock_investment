from __future__ import annotations

from stock_data.contracts.base import ColumnContract, DatasetContract


OPEN_DART_TERMS_URL = "https://opendart.fss.or.kr/intro/terms.do"


KR_CORP_CODE_MAP = DatasetContract(
    name="kr_corp_code_map",
    version=1,
    status="active_manual_weekly",
    description=(
        "Current OpenDART corporation-code identity map for exact stock-code to "
        "corporation-code joins; unlisted corporations retain a null stock code."
    ),
    source="OpenDART corpCode.xml",
    layer="normalized",
    storage_format="parquet",
    frequency="weekly",
    timezone="Asia/Seoul",
    primary_key=("corp_code",),
    sort_key=("corp_code",),
    partition_by=(),
    columns=(
        ColumnContract("corp_code", "string", False),
        ColumnContract("corp_name", "string", False),
        ColumnContract("stock_code", "string", True),
        ColumnContract("modify_date", "date32", False),
    ),
)


KR_FUNDAMENTALS_QUARTERLY = DatasetContract(
    name="kr_fundamentals_quarterly",
    version=1,
    status="active_manual_display_only_pit_review_required",
    description=(
        "Revision-preserving OpenDART quarterly financial-health observations. "
        "Each filing receipt remains a separate vintage."
    ),
    source="OpenDART fnlttSinglAcntAll.json",
    layer="normalized",
    storage_format="parquet",
    frequency="quarterly",
    timezone="Asia/Seoul",
    primary_key=("symbol", "bsns_year", "reprt_code", "fs_div", "rcept_no"),
    sort_key=("symbol", "period_end", "fs_div", "retrieved_at", "rcept_no"),
    partition_by=("bsns_year",),
    columns=(
        ColumnContract("symbol", "string", False),
        ColumnContract("corp_code", "string", False),
        ColumnContract("bsns_year", "int64", False),
        ColumnContract("reprt_code", "string", False),
        ColumnContract("fs_div", "string", False),
        ColumnContract("period_end", "date32", False),
        ColumnContract("revenue", "int64", True, "reported_currency"),
        ColumnContract("operating_income", "int64", True, "reported_currency"),
        ColumnContract("net_income", "int64", True, "reported_currency"),
        ColumnContract("total_liabilities", "int64", True, "reported_currency"),
        ColumnContract("total_equity", "int64", True, "reported_currency"),
        ColumnContract("debt_ratio_pct", "float64", True, "percent"),
        ColumnContract("rcept_no", "string", False),
        ColumnContract("retrieved_at", "timestamp[us, UTC]", False),
        ColumnContract("source_terms_ref", "string", False),
    ),
)


KR_FUNDAMENTALS_CONTRACTS = (KR_CORP_CODE_MAP, KR_FUNDAMENTALS_QUARTERLY)


__all__ = [
    "KR_CORP_CODE_MAP",
    "KR_FUNDAMENTALS_CONTRACTS",
    "KR_FUNDAMENTALS_QUARTERLY",
    "OPEN_DART_TERMS_URL",
]
