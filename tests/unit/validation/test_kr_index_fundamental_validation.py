from __future__ import annotations

import pandas as pd
import pytest

from stock_data.contracts.kr_index_fundamental_daily import (
    KR_INDEX_FUNDAMENTAL_DAILY,
)
from stock_data.validation.kr_index_fundamental_daily import (
    IndexFundamentalValidationError,
    validate_kr_index_fundamental_daily,
)


def _rows() -> pd.DataFrame:
    return pd.DataFrame(
        [
            ["2026-08-25", "1001", "KOSPI", 3200.0, 15.0, 1.3, 1.1,
             "KRX_MDCSTAT00702", "a" * 64],
            ["2026-08-25", "2001", "KOSDAQ", 900.0, None, None, 0.0,
             "KRX_MDCSTAT00702", "b" * 64],
        ],
        columns=KR_INDEX_FUNDAMENTAL_DAILY.column_names,
    )


def test_valid_rows_preserve_provider_missing_values_as_null() -> None:
    dataframe = _rows()
    validate_kr_index_fundamental_daily(dataframe)
    assert pd.isna(dataframe.loc[1, "weighted_per"])
    assert pd.isna(dataframe.loc[1, "weighted_pbr"])


@pytest.mark.parametrize(
    "mutation,message",
    [
        (lambda df: df.assign(index_code="1028"), "1001 or KOSDAQ 2001"),
        (lambda df: df.assign(market="KOSPI"), "inconsistent"),
        (lambda df: df.assign(weighted_per=0.0), "positive"),
        (lambda df: df.assign(weighted_pbr=float("inf")), "finite"),
        (lambda df: df.assign(dividend_yield=-0.1), "nonnegative"),
        (lambda df: df.assign(source_response_sha256="BAD"), "SHA-256"),
    ],
)
def test_invalid_identity_ratio_or_provenance_is_rejected(mutation, message) -> None:
    with pytest.raises(IndexFundamentalValidationError, match=message):
        validate_kr_index_fundamental_daily(mutation(_rows()))


def test_duplicate_or_conflicting_date_is_rejected() -> None:
    duplicate = pd.concat([_rows(), _rows().iloc[[0]]], ignore_index=True)
    with pytest.raises(IndexFundamentalValidationError, match="duplicate or conflicting"):
        validate_kr_index_fundamental_daily(duplicate)
