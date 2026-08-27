from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
import re
from typing import Any

from stock_data.contracts.kbsec_account_snapshot import (
    KB_ACCOUNT_ECONOMIC_ATTRIBUTION_SCOPE,
    KB_ACCOUNT_REGISTERED_HOLDER_SCOPE,
    KB_ACCOUNT_SNAPSHOT_SCHEMA_VERSION,
    KB_ACCOUNT_SOURCE,
    KB_ACCOUNT_SOURCE_EVIDENCE_VERSION,
    KB_ACCOUNT_SOURCE_MODE,
    KB_ACCOUNT_SOURCE_OPERATION,
    KB_ACCOUNT_UNSUPPORTED_FIELDS,
)


class KBSecAccountContractError(ValueError):
    """The retained read-only response does not satisfy the account contract."""


_DECIMAL = re.compile(r"^[+-]?\d+(?:\.\d+)?$")
_BODY_KEYS = frozenset({
    "grid_cnt1",
    "tl_data_cnt",
    "nt_asts_val_amt",
    "scrts_nt_val_amt",
    "byng_amt_sum",
    "val_amt_sum",
    "val_pl_sum",
    "Record1",
})
_POSITION_KEYS = frozenset({
    "is_cd",
    "is_nm",
    "clsf",
    "ec_q_p6",
    "ordr_psbl_q_p6",
    "byng_avr_prc",
    "now_prc",
    "byng_amt",
    "val_amt",
    "val_pl",
})


def _decimal(value: Any, field: str) -> Decimal:
    if not isinstance(value, str) or _DECIMAL.fullmatch(value.strip()) is None:
        raise KBSecAccountContractError(f"{field} must be a provider decimal string")
    try:
        number = Decimal(value.strip())
    except InvalidOperation:
        raise KBSecAccountContractError(f"{field} is not decimal") from None
    if not number.is_finite():
        raise KBSecAccountContractError(f"{field} must be finite")
    return number


def _count(value: Any, field: str) -> int:
    number = _decimal(value, field)
    if number != number.to_integral_value() or number < 0:
        raise KBSecAccountContractError(f"{field} must be a non-negative integer")
    return int(number)


def _iso_datetime(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise KBSecAccountContractError("collected_at must be timezone-aware")
    return value.isoformat()


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise KBSecAccountContractError(f"{field} must be non-empty text")
    return value.strip()


def normalize_domestic_balance_payload(
    payload: dict[str, Any], *, collected_at: datetime,
) -> dict[str, Any]:
    """Project verified SSQM2952 fields into an identifier-free local contract.

    This parser deliberately has no HTTP or credential behavior.  The retained
    official SPQM2226 sample is not accepted here because its overseas position
    rows are structurally corrupted; overseas holdings therefore remain an
    explicit unsupported field.
    """

    if not isinstance(payload, dict) or set(payload) != {"dataHeader", "dataBody"}:
        raise KBSecAccountContractError("KB response envelope is invalid")
    header = payload["dataHeader"]
    body = payload["dataBody"]
    if not isinstance(header, dict) or not isinstance(body, dict):
        raise KBSecAccountContractError("KB response envelope members must be objects")
    if header.get("resultCode") != "200" or header.get("processCode") != "0011":
        raise KBSecAccountContractError("KB account response is not successful")
    if not isinstance(header.get("processTime"), str) or not re.fullmatch(
        r"\d{17}", header["processTime"]
    ):
        raise KBSecAccountContractError("KB processTime is invalid")
    if not _BODY_KEYS.issubset(body):
        raise KBSecAccountContractError("KB domestic balance response is partial")

    rows = body["Record1"]
    if not isinstance(rows, list):
        raise KBSecAccountContractError("KB positions must be a list")
    if _count(body["grid_cnt1"], "grid_cnt1") != len(rows):
        raise KBSecAccountContractError("KB position count does not reconcile")
    if _count(body["tl_data_cnt"], "tl_data_cnt") != len(rows):
        raise KBSecAccountContractError("KB total position count does not reconcile")

    positions: list[dict[str, str]] = []
    seen: set[str] = set()
    purchase_sum = Decimal(0)
    value_sum = Decimal(0)
    pnl_sum = Decimal(0)
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or not _POSITION_KEYS.issubset(row):
            raise KBSecAccountContractError(f"KB position {index} is partial")
        symbol = _text(row["is_cd"], f"positions[{index}].is_cd")
        name = _text(row["is_nm"], f"positions[{index}].is_nm")
        classification = _text(row["clsf"], f"positions[{index}].clsf")
        position_key = f"{symbol}|{classification}"
        if position_key in seen:
            raise KBSecAccountContractError("KB position identity is duplicated")
        seen.add(position_key)

        quantity = _decimal(row["ec_q_p6"], f"positions[{index}].ec_q_p6")
        orderable_quantity = _decimal(
            row["ordr_psbl_q_p6"], f"positions[{index}].ordr_psbl_q_p6"
        )
        average_purchase_price = _decimal(
            row["byng_avr_prc"], f"positions[{index}].byng_avr_prc"
        )
        current_price = _decimal(row["now_prc"], f"positions[{index}].now_prc")
        purchase_amount = _decimal(row["byng_amt"], f"positions[{index}].byng_amt")
        market_value = _decimal(row["val_amt"], f"positions[{index}].val_amt")
        unrealized_pnl = _decimal(row["val_pl"], f"positions[{index}].val_pl")
        if quantity < 0 or orderable_quantity < 0:
            raise KBSecAccountContractError("KB position quantity cannot be negative")
        purchase_sum += purchase_amount
        value_sum += market_value
        pnl_sum += unrealized_pnl
        positions.append({
            "position_key": position_key,
            "symbol": symbol,
            "name": name,
            "classification": classification,
            "currency": "KRW",
            "quantity": str(quantity),
            "orderable_quantity": str(orderable_quantity),
            "average_purchase_price": str(average_purchase_price),
            "current_price": str(current_price),
            "purchase_amount": str(purchase_amount),
            "market_value": str(market_value),
            "unrealized_pnl": str(unrealized_pnl),
        })

    expected_purchase = _decimal(body["byng_amt_sum"], "byng_amt_sum")
    expected_value = _decimal(body["val_amt_sum"], "val_amt_sum")
    expected_pnl = _decimal(body["val_pl_sum"], "val_pl_sum")
    if (purchase_sum, value_sum, pnl_sum) != (
        expected_purchase, expected_value, expected_pnl,
    ):
        raise KBSecAccountContractError("KB account aggregates do not reconcile")

    return {
        "schema_version": KB_ACCOUNT_SNAPSHOT_SCHEMA_VERSION,
        "provider": KB_ACCOUNT_SOURCE,
        "source_operation": KB_ACCOUNT_SOURCE_OPERATION,
        "source_evidence_version": KB_ACCOUNT_SOURCE_EVIDENCE_VERSION,
        "source_mode": KB_ACCOUNT_SOURCE_MODE,
        "collected_at": _iso_datetime(collected_at),
        "registered_holder_scope": KB_ACCOUNT_REGISTERED_HOLDER_SCOPE,
        "economic_attribution_scope": KB_ACCOUNT_ECONOMIC_ATTRIBUTION_SCOPE,
        "currency": "KRW",
        "total_assets": str(_decimal(body["nt_asts_val_amt"], "nt_asts_val_amt")),
        "securities_value": str(
            _decimal(body["scrts_nt_val_amt"], "scrts_nt_val_amt")
        ),
        "purchase_amount": str(expected_purchase),
        "unrealized_pnl": str(expected_pnl),
        "cash_balance": None,
        "buying_power": None,
        "realized_pnl": None,
        "unsupported_fields": sorted(KB_ACCOUNT_UNSUPPORTED_FIELDS),
        "positions": positions,
    }
