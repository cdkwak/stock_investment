from pathlib import Path
from decimal import Decimal
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from stock_data.contracts.base import ColumnContract, DatasetContract
from stock_data.contracts.global_market import GLOBAL_INDEX_PRICE_DAILY
from stock_data.storage import contract_parquet
from stock_data.validation.global_market import validate_global_index


def frame(close=105.0):
    return pd.DataFrame([["2026-08-07","SP500","^GSPC",100.,110.,90.,close,1000]],columns=GLOBAL_INDEX_PRICE_DAILY.column_names)


def physical_contract() -> DatasetContract:
    return DatasetContract(
        name="physical_fixture", version=1, status="active", description="fixture",
        source="fixture", layer="normalized", storage_format="parquet", frequency="daily",
        timezone="Asia/Seoul", primary_key=("date", "symbol"),
        sort_key=("date", "symbol"), partition_by=("year",),
        columns=(
            ColumnContract("date", "date32", False),
            ColumnContract("source_date", "date32", False),
            ColumnContract("symbol", "string", False),
            ColumnContract("optional_count", "int64", True),
            ColumnContract("optional_label", "string", True),
        ),
    )


def validate_physical(frame: pd.DataFrame) -> None:
    assert tuple(frame.columns) == physical_contract().column_names
    assert not frame.empty


def test_contract_storage_enforces_physical_arrow_schema(tmp_path: Path) -> None:
    contract = physical_contract()
    value = pd.DataFrame(
        [["2026-08-07", "2026-08-07", "A", 1, None],
         ["2026-08-08", "2026-08-08", "B", None, None]],
        columns=contract.column_names,
    )
    value.attrs = {"lineage": "fixture"}
    root = tmp_path / "physical"
    contract_parquet.write_dataset_atomic(value, root, contract, validate_physical)

    schema = pq.ParquetFile(next(root.rglob("data.parquet"))).schema_arrow
    assert schema == pa.schema([
        pa.field("date", pa.date32(), nullable=False),
        pa.field("source_date", pa.date32(), nullable=False),
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("optional_count", pa.int64(), nullable=True),
        pa.field("optional_label", pa.string(), nullable=True),
    ])
    restored = contract_parquet.read_dataset(root, contract, validate_physical)
    assert restored["date"].tolist() == ["2026-08-07", "2026-08-08"]
    assert restored["source_date"].tolist() == ["2026-08-07", "2026-08-08"]
    assert restored.attrs == {"lineage": "fixture"}


def rich_types_contract() -> DatasetContract:
    return DatasetContract(
        name="rich_types_fixture", version=1, status="active", description="fixture",
        source="fixture", layer="normalized", storage_format="parquet", frequency="event",
        timezone="UTC", primary_key=("observed_at",), sort_key=("observed_at",),
        partition_by=(), columns=(
            ColumnContract("observed_at", "timestamp[us, UTC]", False),
            ColumnContract("amount", "decimal(22,3)", True),
            ColumnContract("label", "string", True),
        ),
    )


def test_contract_arrow_rejects_unsafe_timestamp_decimal_and_string_coercion() -> None:
    from stock_data.storage.contract_arrow import dataframe_to_contract_table

    contract = rich_types_contract()
    valid = pd.DataFrame([{
        "observed_at": pd.Timestamp("2026-08-07T01:02:03.123456Z"),
        "amount": Decimal("1.234"), "label": None,
    }])
    table = dataframe_to_contract_table(valid, contract)
    assert table.schema.types == [
        pa.timestamp("us", tz="UTC"), pa.decimal128(22, 3), pa.string()
    ]
    assert table.column("amount")[0].as_py() == Decimal("1.234")

    naive = valid.copy()
    naive["observed_at"] = pd.Timestamp("2026-08-07T01:02:03")
    with pytest.raises(ValueError, match="timezone-naive"):
        dataframe_to_contract_table(naive, contract)
    unsafe_decimal = valid.copy()
    unsafe_decimal["amount"] = 1.234
    with pytest.raises(ValueError, match="unsafe decimal"):
        dataframe_to_contract_table(unsafe_decimal, contract)
    excess_scale = valid.copy()
    excess_scale["amount"] = Decimal("1.2345")
    with pytest.raises(pa.ArrowInvalid, match="data loss"):
        dataframe_to_contract_table(excess_scale, contract)
    unsafe_string = valid.copy()
    unsafe_string["label"] = 123
    with pytest.raises((pa.ArrowInvalid, pa.ArrowTypeError)):
        dataframe_to_contract_table(unsafe_string, contract)


def test_contract_storage_failure_preserves_existing(monkeypatch,tmp_path:Path) -> None:
    root=tmp_path/"global"
    contract_parquet.write_dataset_atomic(frame(),root,GLOBAL_INDEX_PRICE_DAILY,validate_global_index)
    target=next(root.rglob("data.parquet")); before=target.read_bytes()
    original=Path.replace
    calls=0
    def fail_replace(self,target_path):
        nonlocal calls
        if self.name.endswith(".parquet.tmp"):
            calls+=1
            if calls==1: raise OSError("simulated replace failure")
        return original(self,target_path)
    monkeypatch.setattr(Path,"replace",fail_replace)
    with pytest.raises(OSError,match="simulated"):
        contract_parquet.write_dataset_atomic(frame(106.),root,GLOBAL_INDEX_PRICE_DAILY,validate_global_index)
    assert target.read_bytes()==before
