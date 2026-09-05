from datetime import datetime, timezone

import pandas as pd
import pytest

from stock_data.contracts.bok_ecos_market_rates import (
    BOK_ECOS_KR_MARKET_RATE_DAILY,
)
from stock_data.contracts.registry import CONTRACTS
from stock_data.validation.data_v1 import DataV1ValidationError, validate_data_v1


def _frame() -> pd.DataFrame:
    return pd.DataFrame({
        "date": ["2001-02-03", "2001-02-03"],
        "series": ["CALL_RATE_OVERNIGHT", "CORP_BOND_3Y_AA_MINUS"],
        "rate_percent": [2.5, 7.25],
        "item_code": ["010101000", "010300000"],
        "stat_code": ["817Y002", "817Y002"],
        "unit": ["연%", "연%"],
        "source": ["BOK_ECOS", "BOK_ECOS"],
        "source_operation": ["StatisticSearch", "StatisticSearch"],
        "retrieved_at": [
            datetime(2026, 9, 6, tzinfo=timezone.utc),
            datetime(2026, 9, 6, tzinfo=timezone.utc),
        ],
    })


def test_market_rate_contract_is_registered_and_series_keyed() -> None:
    contract = BOK_ECOS_KR_MARKET_RATE_DAILY
    assert CONTRACTS[contract.name] is contract
    assert contract.primary_key == ("date", "series")
    assert contract.partition_by == ("year",)
    assert contract.column_names == (
        "date", "series", "rate_percent", "item_code", "stat_code", "unit",
        "source", "source_operation", "retrieved_at",
    )
    validate_data_v1(_frame(), contract, allow_empty=False)


def test_market_rate_contract_rejects_duplicate_series_date() -> None:
    frame = _frame()
    frame.loc[1, "series"] = frame.loc[0, "series"]
    with pytest.raises(DataV1ValidationError, match="duplicate primary key"):
        validate_data_v1(frame, BOK_ECOS_KR_MARKET_RATE_DAILY, allow_empty=False)
