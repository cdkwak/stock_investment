from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import pandas as pd

from stock_data.contracts.kr_equity import (
    KR_EQUITY_MARKET_CAP_DAILY, KR_EQUITY_PRICE_DAILY, KR_EQUITY_UNIVERSE_DAILY,
)
from stock_data.validation.data_v1 import validate_data_v1
from stock_data.validation.kr_equity import validate_equity_market_cap, validate_equity_price


TRADE_FIELDS = {
    "BAS_DD", "ISU_CD", "ISU_NM", "MKT_NM", "TDD_OPNPRC", "TDD_HGPRC",
    "TDD_LWPRC", "TDD_CLSPRC", "ACC_TRDVOL", "ACC_TRDVAL", "MKTCAP", "LIST_SHRS",
}
BASIC_FIELDS = {
    "ISU_CD", "ISU_SRT_CD", "ISU_NM", "ISU_ABBRV", "ISU_ENG_NM", "LIST_DD",
    "MKT_TP_NM", "SECUGRP_NM", "SECT_TP_NM", "KIND_STKCERT_TP_NM", "PARVAL", "LIST_SHRS",
}


@dataclass(frozen=True)
class NormalizedKrxTrade:
    price: pd.DataFrame
    market_cap: pd.DataFrame


def _integer(value, field):
    try:
        return int(str(value).replace(",", ""))
    except (TypeError, ValueError):
        raise ValueError(f"KRX field {field} is not an integer") from None


def _date(value):
    parsed = pd.to_datetime(str(value), errors="coerce")
    if pd.isna(parsed):
        raise ValueError("KRX date is invalid")
    return parsed.strftime("%Y-%m-%d")


def normalize_daily_trade(rows: Sequence[Mapping[str, object]], market: str) -> NormalizedKrxTrade:
    if market not in {"KOSPI", "KOSDAQ"}:
        raise ValueError("market must be KOSPI or KOSDAQ")
    prices, caps = [], []
    for row in rows:
        missing = TRADE_FIELDS - set(row)
        if missing:
            raise ValueError(f"KRX trade fields missing: {sorted(missing)}")
        source_date = _date(row["BAS_DD"])
        operation = "stk_bydd_trd" if market == "KOSPI" else "ksq_bydd_trd"
        key = {"date": source_date, "market": market,
               "symbol": str(row["ISU_CD"]).strip().removeprefix("A").zfill(6)}
        provenance = {"source": "krx_open_api", "source_operation": operation,
                      "source_date": source_date}
        prices.append({**key, "open": _integer(row["TDD_OPNPRC"], "TDD_OPNPRC"),
                       "high": _integer(row["TDD_HGPRC"], "TDD_HGPRC"),
                       "low": _integer(row["TDD_LWPRC"], "TDD_LWPRC"),
                       "close": _integer(row["TDD_CLSPRC"], "TDD_CLSPRC"),
                       "volume": _integer(row["ACC_TRDVOL"], "ACC_TRDVOL"),
                       "trading_value": _integer(row["ACC_TRDVAL"], "ACC_TRDVAL"), **provenance})
        caps.append({**key, "market_cap": _integer(row["MKTCAP"], "MKTCAP"),
                     "shares_outstanding": _integer(row["LIST_SHRS"], "LIST_SHRS"), **provenance})
    price = pd.DataFrame(prices, columns=KR_EQUITY_PRICE_DAILY.column_names).sort_values(
        list(KR_EQUITY_PRICE_DAILY.sort_key), kind="stable").reset_index(drop=True)
    cap = pd.DataFrame(caps, columns=KR_EQUITY_MARKET_CAP_DAILY.column_names).sort_values(
        list(KR_EQUITY_MARKET_CAP_DAILY.sort_key), kind="stable").reset_index(drop=True)
    validate_equity_price(price)
    validate_equity_market_cap(cap)
    return NormalizedKrxTrade(price, cap)


def normalize_basic_info(rows: Sequence[Mapping[str, object]], market: str, base_date: str) -> pd.DataFrame:
    date = _date(base_date)
    operation = "stk_isu_base_info" if market == "KOSPI" else "ksq_isu_base_info"
    output = []
    for row in rows:
        missing = BASIC_FIELDS - set(row)
        if missing:
            raise ValueError(f"KRX basic-info fields missing: {sorted(missing)}")
        output.append({
            "date": date, "market": market,
            "symbol": str(row["ISU_SRT_CD"]).strip().removeprefix("A").zfill(6),
            "isin": str(row["ISU_CD"]).strip(), "name": str(row["ISU_NM"]).strip(),
            "short_name": str(row["ISU_ABBRV"]).strip(),
            "english_name": str(row["ISU_ENG_NM"]).strip(), "listing_date": _date(row["LIST_DD"]),
            "security_group": str(row["SECUGRP_NM"]).strip(),
            "security_type": str(row["KIND_STKCERT_TP_NM"]).strip(),
            "par_value": str(row["PARVAL"]).strip(),
            "listed_shares": _integer(row["LIST_SHRS"], "LIST_SHRS"),
            "corporate_number": None, "corporate_name": None,
            "source": "krx_open_api", "source_operation": operation, "source_date": date,
        })
    frame = pd.DataFrame(output, columns=KR_EQUITY_UNIVERSE_DAILY.column_names).sort_values(
        list(KR_EQUITY_UNIVERSE_DAILY.sort_key), kind="stable").reset_index(drop=True)
    validate_data_v1(frame, KR_EQUITY_UNIVERSE_DAILY)
    return frame
