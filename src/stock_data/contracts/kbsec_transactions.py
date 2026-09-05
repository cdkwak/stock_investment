from __future__ import annotations

from enum import StrEnum

from stock_data.contracts.base import ColumnContract, DatasetContract


class KBSecTransactionDirection(StrEnum):
    IN = "IN"
    OUT = "OUT"


class KBSecTransactionCategory(StrEnum):
    DEPOSIT = "DEPOSIT"
    WITHDRAWAL = "WITHDRAWAL"
    DIVIDEND = "DIVIDEND"
    TAX = "TAX"
    FEE = "FEE"
    OTHER = "OTHER"


KBSEC_TRANSACTIONS_DAILY = DatasetContract(
    name="kbsec_transactions_daily",
    version=1,
    status="active",
    description=(
        "Identifier-free KB SWQA2301 transaction rows retained for the local "
        "cash-flow ledger; OTHER rows remain Landing/state evidence only."
    ),
    source="KB Securities SWQA2301",
    layer="local_user",
    storage_format="json",
    frequency="daily",
    timezone="Asia/Seoul",
    primary_key=("raw_row_sha256",),
    sort_key=("date", "raw_row_sha256"),
    partition_by=(),
    columns=(
        ColumnContract("date", "date32", False),
        ColumnContract("direction", "string", False),
        ColumnContract("category", "string", False),
        ColumnContract("amount_krw", "int64", False, unit="KRW"),
        ColumnContract("tax_krw", "int64", False, unit="KRW"),
        ColumnContract("summary_name", "string", False),
        ColumnContract("transaction_type_code", "string", False),
        ColumnContract("summary_type_code", "string", False),
        ColumnContract("raw_row_sha256", "string", False),
    ),
)


__all__ = [
    "KBSEC_TRANSACTIONS_DAILY",
    "KBSecTransactionCategory",
    "KBSecTransactionDirection",
]
