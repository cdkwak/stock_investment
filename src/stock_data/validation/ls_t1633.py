from __future__ import annotations

from datetime import datetime
import re
from typing import Mapping

import pandas as pd

from stock_data.contracts.ls_t1633 import (
    LS_T1633_AMOUNT_MULTIPLIER,
    LS_T1633_FINALITY_POLICY,
    LS_T1633_PROGRAM_TRADING_DAILY,
    LS_T1633_QUANTITY_MULTIPLIER,
)


MARKET_CODES = {"KOSPI": "0", "KOSDAQ": "1"}
SOURCE_FIELDS = {
    "total": ("tot1", "tot2", "tot3"),
    "arbitrage": ("cha1", "cha2", "cha3"),
    "non_arbitrage": ("bcha1", "bcha2", "bcha3"),
}
_SHA256 = re.compile(r"[0-9a-f]{64}")


def _source_integer(row: Mapping[str, object], field: str) -> int:
    value = row.get(field)
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError(f"invalid LS t1633 integer: {field}")
    text = str(value).strip().replace(",", "")
    if not text or not text.lstrip("+-").isdigit():
        raise ValueError(f"invalid LS t1633 integer: {field}")
    return int(text)


def _source_date(row: Mapping[str, object]) -> str:
    value = str(row.get("date", ""))
    parsed = pd.to_datetime(value, format="%Y%m%d", errors="coerce")
    if pd.isna(parsed):
        raise ValueError("invalid LS t1633 source date")
    return parsed.date().isoformat()


def normalize_ls_t1633_market_pair(
    *, amount_row: Mapping[str, object], quantity_row: Mapping[str, object],
    market: str, collected_at: datetime, amount_landing_sha256: str,
    quantity_landing_sha256: str,
) -> pd.DataFrame:
    """Normalize the two exact LS selector rows for one market/date."""
    if market not in MARKET_CODES:
        raise ValueError("unsupported LS t1633 market")
    amount_date = _source_date(amount_row)
    if _source_date(quantity_row) != amount_date:
        raise ValueError("LS t1633 amount and quantity dates differ")
    observed = pd.Timestamp(collected_at)
    if observed.tzinfo is None:
        raise ValueError("collected_at must be timezone-aware")
    if not _SHA256.fullmatch(amount_landing_sha256) or not _SHA256.fullmatch(
        quantity_landing_sha256
    ):
        raise ValueError("Landing SHA-256 is invalid")
    output: dict[str, object] = {"date": amount_date, "market": market}
    for group, fields in SOURCE_FIELDS.items():
        for side, field in zip(("buy", "sell", "net"), fields):
            output[f"{group}_{side}_amount"] = (
                _source_integer(amount_row, field) * LS_T1633_AMOUNT_MULTIPLIER
            )
            output[f"{group}_{side}_volume"] = (
                _source_integer(quantity_row, field) * LS_T1633_QUANTITY_MULTIPLIER
            )
    output.update({
        "source": "ls_open_api",
        "source_operation": "t1633",
        "source_market_code": MARKET_CODES[market],
        "source_amount_selector": "0",
        "source_quantity_selector": "1",
        "source_session": "REGULAR",
        "source_exchange_scope": "K",
        "source_date": amount_date,
        "collected_at": observed.tz_convert("UTC"),
        "amount_landing_sha256": amount_landing_sha256,
        "quantity_landing_sha256": quantity_landing_sha256,
        "unit_evidence": "CONFIRMED_EMPIRICAL_MULTI_DATE",
        "finality_status": LS_T1633_FINALITY_POLICY,
    })
    frame = pd.DataFrame([output], columns=LS_T1633_PROGRAM_TRADING_DAILY.column_names)
    validate_ls_t1633_program_trading(frame)
    return frame


def validate_ls_t1633_program_trading(dataframe: pd.DataFrame) -> None:
    contract = LS_T1633_PROGRAM_TRADING_DAILY
    if dataframe.empty:
        raise ValueError("LS t1633 normalized data is empty")
    if list(dataframe.columns) != list(contract.column_names):
        raise ValueError("LS t1633 normalized schema differs")
    if dataframe.duplicated(list(contract.primary_key)).any():
        raise ValueError("duplicate LS t1633 date/market")
    expected = dataframe.sort_values(list(contract.sort_key), kind="stable").reset_index(drop=True)
    if not dataframe.reset_index(drop=True).equals(expected):
        raise ValueError("LS t1633 rows are not sorted")
    if not set(dataframe["market"].astype(str)) <= set(MARKET_CODES):
        raise ValueError("invalid LS t1633 market")
    expected_codes = dataframe["market"].map(MARKET_CODES)
    if not dataframe["source_market_code"].astype(str).eq(expected_codes).all():
        raise ValueError("LS t1633 market code differs")
    parsed_dates = pd.to_datetime(dataframe["date"], errors="coerce")
    source_dates = pd.to_datetime(dataframe["source_date"], format="%Y-%m-%d", errors="coerce")
    if parsed_dates.isna().any() or source_dates.isna().any():
        raise ValueError("invalid LS t1633 normalized date")
    if not parsed_dates.dt.strftime("%Y-%m-%d").eq(dataframe["source_date"].astype(str)).all():
        raise ValueError("LS t1633 source date differs")
    collected = pd.to_datetime(dataframe["collected_at"], utc=True, errors="coerce")
    if collected.isna().any():
        raise ValueError("invalid LS t1633 collected_at")
    fixed = {
        "source": "ls_open_api", "source_operation": "t1633",
        "source_amount_selector": "0", "source_quantity_selector": "1",
        "source_session": "REGULAR", "source_exchange_scope": "K",
        "unit_evidence": "CONFIRMED_EMPIRICAL_MULTI_DATE",
        "finality_status": LS_T1633_FINALITY_POLICY,
    }
    for field, value in fixed.items():
        if not dataframe[field].astype(str).eq(value).all():
            raise ValueError(f"invalid LS t1633 provenance: {field}")
    for field in ("amount_landing_sha256", "quantity_landing_sha256"):
        if not dataframe[field].astype(str).str.fullmatch(_SHA256).all():
            raise ValueError("invalid LS t1633 Landing SHA-256")
    for suffix, multiplier in (
        ("amount", LS_T1633_AMOUNT_MULTIPLIER),
        ("volume", LS_T1633_QUANTITY_MULTIPLIER),
    ):
        names = [name for name in contract.column_names if name.endswith("_" + suffix)]
        for name in names:
            numeric = pd.to_numeric(dataframe[name], errors="coerce")
            if numeric.isna().any() or not numeric.mod(multiplier).eq(0).all():
                raise ValueError(f"invalid LS t1633 {suffix} multiplier")
        for group in ("total", "arbitrage", "non_arbitrage"):
            residual = (
                dataframe[f"{group}_buy_{suffix}"]
                - dataframe[f"{group}_sell_{suffix}"]
                - dataframe[f"{group}_net_{suffix}"]
            ).abs()
            if residual.gt(multiplier).any():
                raise ValueError(f"LS t1633 {group} identity differs")
        components = (
            dataframe[f"arbitrage_net_{suffix}"]
            + dataframe[f"non_arbitrage_net_{suffix}"]
            - dataframe[f"total_net_{suffix}"]
        ).abs()
        if components.gt(multiplier).any():
            raise ValueError(f"LS t1633 component identity differs for {suffix}")


def validate_ls_t1633_exact_date_pair(dataframe: pd.DataFrame, market_date: str) -> None:
    validate_ls_t1633_program_trading(dataframe)
    exact = dataframe.loc[dataframe["date"].astype(str).eq(market_date)]
    if len(exact) != 2 or set(exact["market"].astype(str)) != set(MARKET_CODES):
        raise ValueError("LS t1633 exact date requires KOSPI and KOSDAQ together")


__all__ = [
    "MARKET_CODES", "normalize_ls_t1633_market_pair",
    "validate_ls_t1633_exact_date_pair", "validate_ls_t1633_program_trading",
]
