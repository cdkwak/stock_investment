from __future__ import annotations

import pandas as pd
import pytest

from stock_data.contracts.kr_index_daily import KR_INDEX_DAILY
from stock_data.validation.kr_index_daily import (
    DatasetValidationError,
    validate_kr_index_daily,
)


def rows() -> pd.DataFrame:
    return pd.DataFrame(
        [
            ["2026-08-03", "KOSDAQ", "KOSDAQ", 900.0, 920.0, 890.0, 910.0, 20, 200, 2000, "pykrx"],
            ["2026-08-03", "KOSPI", "KOSPI", 3000.0, 3020.0, 2990.0, 3010.0, 10, 100, 1000, "pykrx"],
        ],
        columns=KR_INDEX_DAILY.column_names,
    )


def test_valid_dataset_passes() -> None:
    validate_kr_index_daily(rows())


@pytest.mark.parametrize("mutation, message", [
    (lambda df: df.assign(date="20260803"), "YYYY-MM-DD"),
    (lambda df: pd.concat([df, df.iloc[[0]]], ignore_index=True), "duplicates"),
    (lambda df: df.assign(high=1.0), "OHLC"),
    (lambda df: df.assign(volume=-1), "nonnegative"),
])
def test_invalid_dataset_is_rejected(mutation, message: str) -> None:
    with pytest.raises(DatasetValidationError, match=message):
        validate_kr_index_daily(mutation(rows()))


def test_unsorted_dataset_is_rejected() -> None:
    with pytest.raises(DatasetValidationError, match="sorted"):
        validate_kr_index_daily(rows().iloc[::-1].reset_index(drop=True))


def test_historical_source_zero_ohlc_is_preserved() -> None:
    dataframe=rows().iloc[[0]].copy().reset_index(drop=True)
    dataframe.loc[0,["open","high","low"]]=0
    dataframe.loc[0,"close"]=100
    validate_kr_index_daily(dataframe)
