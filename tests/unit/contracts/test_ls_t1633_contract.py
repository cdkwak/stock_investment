from datetime import datetime, timezone

import pytest

from stock_data.contracts.ls_t1633 import (
    LS_T1633_AMOUNT_MULTIPLIER,
    LS_T1633_AUTHORITY,
    LS_T1633_PROGRAM_TRADING_DAILY,
    LS_T1633_QUANTITY_MULTIPLIER,
)
from stock_data.contracts.registry import CONTRACTS
from stock_data.validation.ls_t1633 import normalize_ls_t1633_market_pair


SHA_A = "a" * 64
SHA_B = "b" * 64


def _row(day: str = "20260819") -> dict[str, str]:
    return {
        "date": day,
        "tot1": "100", "tot2": "80", "tot3": "20",
        "cha1": "30", "cha2": "35", "cha3": "-5",
        "bcha1": "70", "bcha2": "45", "bcha3": "25",
    }


def test_ls_t1633_contract_is_provider_bounded_with_reviewed_empirical_finality() -> None:
    contract = LS_T1633_PROGRAM_TRADING_DAILY
    assert contract.name not in CONTRACTS
    assert contract.status == "operational_with_empirical_finality"
    assert contract.source == "ls_open_api:t1633"
    assert contract.primary_key == ("date", "market")
    assert LS_T1633_AUTHORITY.live_ready is True
    assert LS_T1633_AUTHORITY.amount_selector == "0"
    assert LS_T1633_AUTHORITY.quantity_selector == "1"


def test_normalizer_applies_accepted_amount_and_quantity_multipliers() -> None:
    frame = normalize_ls_t1633_market_pair(
        amount_row=_row(), quantity_row=_row(), market="KOSPI",
        collected_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
        amount_landing_sha256=SHA_A, quantity_landing_sha256=SHA_B,
    )
    row = frame.iloc[0]
    assert row["total_buy_amount"] == 100 * LS_T1633_AMOUNT_MULTIPLIER
    assert row["arbitrage_net_amount"] == -5 * LS_T1633_AMOUNT_MULTIPLIER
    assert row["total_buy_volume"] == 100 * LS_T1633_QUANTITY_MULTIPLIER
    assert row["source_market_code"] == "0"
    assert row["unit_evidence"] == "CONFIRMED_EMPIRICAL_MULTI_DATE"


def test_normalizer_rejects_cross_selector_date_mismatch() -> None:
    with pytest.raises(ValueError, match="dates differ"):
        normalize_ls_t1633_market_pair(
            amount_row=_row(), quantity_row=_row("20260818"), market="KOSDAQ",
            collected_at=datetime(2026, 8, 20, tzinfo=timezone.utc),
            amount_landing_sha256=SHA_A, quantity_landing_sha256=SHA_B,
        )
