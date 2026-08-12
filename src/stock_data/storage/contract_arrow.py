from __future__ import annotations

from decimal import Decimal
from numbers import Integral
import re

import pandas as pd
import pyarrow as pa

from stock_data.contracts.base import DatasetContract


_TIMESTAMP = re.compile(r"timestamp\[(ns|us), UTC\]")
_DECIMAL = re.compile(r"decimal\((\d+),(\d+)\)")


def _arrow_type(dtype: str) -> pa.DataType:
    timestamp = _TIMESTAMP.fullmatch(dtype)
    if timestamp:
        return pa.timestamp(timestamp.group(1), tz="UTC")
    decimal = _DECIMAL.fullmatch(dtype)
    if decimal:
        return pa.decimal128(int(decimal.group(1)), int(decimal.group(2)))
    try:
        return pa.type_for_alias(dtype)
    except ValueError as error:
        raise ValueError(f"unsupported contract dtype: {dtype}") from error


def contract_arrow_schema(contract: DatasetContract) -> pa.Schema:
    return pa.schema(
        [
            pa.field(column.name, _arrow_type(column.dtype), nullable=column.nullable)
            for column in contract.columns
        ]
    )


def _decimal_value(value: object, *, dataset: str, column: str) -> Decimal | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, Integral):
        return Decimal(int(value))
    if isinstance(value, str):
        try:
            return Decimal(value)
        except Exception as error:
            raise ValueError(f"{dataset}: invalid decimal value in {column}") from error
    raise ValueError(f"{dataset}: unsafe decimal input type in {column}")


def dataframe_to_contract_table(
    dataframe: pd.DataFrame, contract: DatasetContract
) -> pa.Table:
    """Convert a validated dataframe to the contract's physical Arrow schema.

    Date columns are represented as ISO strings by the in-process data API and
    as date32 in Parquet. Other conversions are delegated to Arrow's safe cast,
    which rejects lossy or incompatible values.
    """
    stored = dataframe[list(contract.column_names)].copy()
    for column in contract.columns:
        if column.dtype == "date32":
            parsed = pd.to_datetime(stored[column.name], errors="coerce")
            invalid = stored[column.name].notna() & parsed.isna()
            if invalid.any():
                raise ValueError(f"{contract.name}: invalid date32 value in {column.name}")
            stored[column.name] = parsed.dt.date
        elif _TIMESTAMP.fullmatch(column.dtype):
            for value in stored[column.name].dropna():
                timestamp = pd.Timestamp(value)
                if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                    raise ValueError(
                        f"{contract.name}: timezone-naive timestamp in {column.name}"
                    )
            parsed = pd.to_datetime(stored[column.name], errors="coerce", utc=True)
            invalid = stored[column.name].notna() & parsed.isna()
            if invalid.any():
                raise ValueError(f"{contract.name}: invalid timestamp value in {column.name}")
            stored[column.name] = parsed
        elif _DECIMAL.fullmatch(column.dtype):
            stored[column.name] = stored[column.name].map(
                lambda value: _decimal_value(
                    value, dataset=contract.name, column=column.name
                )
            )
    return pa.Table.from_pandas(
        stored,
        schema=contract_arrow_schema(contract),
        preserve_index=False,
        safe=True,
    )


def restore_contract_dates(
    dataframe: pd.DataFrame, contract: DatasetContract
) -> pd.DataFrame:
    restored = dataframe.copy()
    for column in contract.columns:
        if column.dtype == "date32" and column.name in restored:
            parsed = pd.to_datetime(restored[column.name], errors="coerce")
            invalid = restored[column.name].notna() & parsed.isna()
            if invalid.any():
                raise ValueError(f"{contract.name}: invalid stored date32 value in {column.name}")
            restored[column.name] = parsed.dt.strftime("%Y-%m-%d")
    return restored
