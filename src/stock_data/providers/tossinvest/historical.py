from __future__ import annotations

from datetime import datetime
import math
import re
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pandas as pd

from stock_data.contracts.tossinvest_historical import (
    KR_EQUITY_CREDIT_TRADING_DAILY,
    KR_EQUITY_PROGRAM_TRADING_DAILY,
    KR_EQUITY_SECURITIES_LENDING_DAILY,
    KR_EQUITY_SHORT_SELLING_DAILY,
    KR_MARKET_INVESTOR_TRADING_DAILY,
    KR_TREASURY_YIELD_DAILY,
)
from stock_data.providers.tossinvest.client import TossInvestResponseError


SOURCE = "tossinvest_open_api"
KST = ZoneInfo("Asia/Seoul")
_INTEGER = re.compile(r"^[+-]?\d+$")
_DECIMAL = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")

MARKET_INVESTOR_OPERATION = "getMarketIndicatorInvestorTrading"
SHORT_SELLING_OPERATION = "getStockShortSelling"
PROGRAM_TRADING_OPERATION = "getStockProgramTrades"
SECURITIES_LENDING_OPERATION = "getStockSecuritiesLending"
CREDIT_TRADING_OPERATION = "getStockCreditTrades"
TREASURY_YIELD_OPERATION = "getMarketIndicatorCandles"

INSTITUTION_BREAKDOWN = {
    "financialInvestment": "institution_financial_investment",
    "insurance": "institution_insurance",
    "trust": "institution_trust",
    "privateEquityFund": "institution_private_equity_fund",
    "bank": "institution_bank",
    "otherFinancialInstitution": "institution_other_financial_institution",
    "pensionFund": "institution_pension_fund",
}


def _integer(value: Any, field: str) -> int | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool) or not _INTEGER.fullmatch(str(value).strip().replace(",", "")):
        raise TossInvestResponseError(f"invalid integer field: {field}")
    return int(str(value).strip().replace(",", ""))


def _decimal(value: Any, field: str) -> float | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise TossInvestResponseError(f"invalid decimal field: {field}")
    raw = str(value).strip().replace(",", "")
    if not _DECIMAL.fullmatch(raw):
        raise TossInvestResponseError(f"invalid decimal field: {field}")
    result = float(raw)
    if not math.isfinite(result):
        raise TossInvestResponseError(f"non-finite decimal field: {field}")
    return result


def _date(value: Any, field: str = "date") -> str:
    try:
        parsed = pd.Timestamp(str(value))
    except (TypeError, ValueError):
        raise TossInvestResponseError(f"invalid date field: {field}") from None
    if pd.isna(parsed):
        raise TossInvestResponseError(f"invalid date field: {field}")
    return parsed.date().isoformat()


def _timestamp(value: Any, field: str) -> pd.Timestamp | None:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        parsed = pd.Timestamp(value)
    except (TypeError, ValueError):
        raise TossInvestResponseError(f"invalid timestamp field: {field}") from None
    if pd.isna(parsed) or parsed.tzinfo is None:
        raise TossInvestResponseError(f"invalid timestamp field: {field}")
    return parsed.tz_convert("UTC")


def _provenance(record: dict[str, Any], *, collected_at: datetime, operation: str,
                source_date: str, updated_field: str | None = "updatedAt") -> dict[str, Any]:
    if collected_at.tzinfo is None:
        raise ValueError("collected_at must be timezone-aware")
    updated = _timestamp(record.get(updated_field), updated_field) if updated_field else None
    availability = (
        updated.tz_convert(KST).date().isoformat()
        if updated is not None
        else None
    )
    return {
        "source": SOURCE,
        "source_operation": operation,
        "source_date": source_date,
        "collected_at": pd.Timestamp(collected_at).tz_convert("UTC"),
        "updated_at": updated,
        "availability_date": availability,
    }


def _object(value: Any, field: str, *, nullable: bool = True) -> dict[str, Any] | None:
    if value is None and nullable:
        return None
    if not isinstance(value, dict):
        raise TossInvestResponseError(f"{field} must be an object")
    return value


def _amount_pair(value: Any, field: str) -> tuple[int | None, int | None]:
    item = _object(value, field)
    if item is None:
        return None, None
    return _integer(item.get("buyAmount"), field + ".buyAmount"), _integer(
        item.get("sellAmount"), field + ".sellAmount"
    )


def _frame(rows: list[dict[str, Any]], contract) -> pd.DataFrame:
    result = pd.DataFrame(rows, columns=contract.column_names)
    if result.empty:
        return result
    return result.sort_values(list(contract.sort_key), kind="stable").reset_index(drop=True)


def normalize_market_investor(records: list[dict[str, Any]], *, market: str,
                              collected_at: datetime) -> pd.DataFrame:
    if market not in {"KOSPI", "KOSDAQ"}:
        raise ValueError("unsupported market")
    rows = []
    for record in records:
        if not isinstance(record, dict):
            raise TossInvestResponseError("investor record must be an object")
        source_date = _date(record.get("date"))
        row: dict[str, Any] = {"date": source_date, "market": market}
        for source_name, target_name in (
            ("individual", "individual"),
            ("foreigner", "foreigner"),
            ("institution", "institution"),
            ("otherCorporation", "other_corporation"),
        ):
            buy, sell = _amount_pair(record.get(source_name), source_name)
            row[f"{target_name}_buy_amount"] = buy
            row[f"{target_name}_sell_amount"] = sell
        institution = _object(record.get("institution"), "institution")
        breakdown = _object(institution.get("breakdown") if institution else None,
                            "institution.breakdown")
        for source_name, target_name in INSTITUTION_BREAKDOWN.items():
            buy, sell = _amount_pair(breakdown.get(source_name) if breakdown else None,
                                     "institution.breakdown." + source_name)
            row[f"{target_name}_buy_amount"] = buy
            row[f"{target_name}_sell_amount"] = sell
        row.update(_provenance(record, collected_at=collected_at,
                               operation=MARKET_INVESTOR_OPERATION, source_date=source_date))
        rows.append(row)
    return _frame(rows, KR_MARKET_INVESTOR_TRADING_DAILY)


def _stock_frame(records: list[dict[str, Any]], *, symbol: str, collected_at: datetime,
                 contract, operation: str, values: Callable[[dict[str, Any]], dict[str, Any]],
                 updated_field: str | None = "updatedAt") -> pd.DataFrame:
    if not re.fullmatch(r"\d{6}", symbol):
        raise ValueError("Toss Korean stock symbol must be six digits")
    rows = []
    for record in records:
        if not isinstance(record, dict):
            raise TossInvestResponseError("stock record must be an object")
        source_date = _date(record.get("date"))
        row = {"date": source_date, "symbol": symbol, **values(record)}
        row.update(_provenance(record, collected_at=collected_at, operation=operation,
                               source_date=source_date, updated_field=updated_field))
        rows.append(row)
    return _frame(rows, contract)


def normalize_short_selling(records, *, symbol: str, collected_at: datetime) -> pd.DataFrame:
    return _stock_frame(records, symbol=symbol, collected_at=collected_at,
        contract=KR_EQUITY_SHORT_SELLING_DAILY, operation=SHORT_SELLING_OPERATION,
        values=lambda r: {
            "short_selling_volume": _integer(r.get("shortSellingVolume"), "shortSellingVolume"),
            "short_selling_amount": _integer(r.get("shortSellingAmount"), "shortSellingAmount"),
            "short_selling_volume_rate": _decimal(r.get("shortSellingVolumeRate"), "shortSellingVolumeRate"),
            "short_selling_amount_rate": _decimal(r.get("shortSellingAmountRate"), "shortSellingAmountRate"),
        })


def normalize_program_trading(records, *, symbol: str, collected_at: datetime) -> pd.DataFrame:
    def values(record):
        arbitrage = _object(record.get("arbitrage"), "arbitrage")
        non_arbitrage = _object(record.get("nonArbitrage"), "nonArbitrage")
        return {
            "arbitrage_buy_volume": _integer(arbitrage.get("buyVolume") if arbitrage else None, "arbitrage.buyVolume"),
            "arbitrage_sell_volume": _integer(arbitrage.get("sellVolume") if arbitrage else None, "arbitrage.sellVolume"),
            "non_arbitrage_buy_volume": _integer(non_arbitrage.get("buyVolume") if non_arbitrage else None, "nonArbitrage.buyVolume"),
            "non_arbitrage_sell_volume": _integer(non_arbitrage.get("sellVolume") if non_arbitrage else None, "nonArbitrage.sellVolume"),
        }
    return _stock_frame(records, symbol=symbol, collected_at=collected_at,
        contract=KR_EQUITY_PROGRAM_TRADING_DAILY, operation=PROGRAM_TRADING_OPERATION,
        values=values, updated_field=None)


def normalize_securities_lending(records, *, symbol: str, collected_at: datetime) -> pd.DataFrame:
    return _stock_frame(records, symbol=symbol, collected_at=collected_at,
        contract=KR_EQUITY_SECURITIES_LENDING_DAILY, operation=SECURITIES_LENDING_OPERATION,
        values=lambda r: {
            "execution_quantity": _integer(r.get("executionQuantity"), "executionQuantity"),
            "repayment_quantity": _integer(r.get("repaymentQuantity"), "repaymentQuantity"),
            "balance_quantity": _integer(r.get("balanceQuantity"), "balanceQuantity"),
            "balance_amount": _integer(r.get("balanceAmount"), "balanceAmount"),
        })


def normalize_credit_trading(records, *, symbol: str, collected_at: datetime) -> pd.DataFrame:
    def values(record):
        row = {}
        for source_name, target_name in (("marginLoan", "margin_loan"), ("stockLoan", "stock_loan")):
            item = _object(record.get(source_name), source_name)
            for source_field, target_field, parser in (
                ("newQuantity", "new_quantity", _integer),
                ("returnQuantity", "return_quantity", _integer),
                ("balanceQuantity", "balance_quantity", _integer),
                ("balanceRate", "balance_rate", _decimal),
                ("tradingRate", "trading_rate", _decimal),
            ):
                row[f"{target_name}_{target_field}"] = parser(
                    item.get(source_field) if item else None, source_name + "." + source_field
                )
        return row
    return _stock_frame(records, symbol=symbol, collected_at=collected_at,
        contract=KR_EQUITY_CREDIT_TRADING_DAILY, operation=CREDIT_TRADING_OPERATION,
        values=values)


def normalize_treasury_yield(records, *, instrument: str, collected_at: datetime) -> pd.DataFrame:
    match = re.fullmatch(r"KR_BOND_(2|3|5|10|20|30)Y", instrument)
    if not match:
        raise ValueError("unsupported Korean treasury instrument")
    rows = []
    for record in records:
        if not isinstance(record, dict):
            raise TossInvestResponseError("treasury candle must be an object")
        timestamp = _timestamp(record.get("timestamp"), "timestamp")
        if timestamp is None:
            raise TossInvestResponseError("treasury timestamp is required")
        source_date = timestamp.tz_convert(KST).date().isoformat()
        row = {
            "date": source_date,
            "instrument": instrument,
            "maturity_years": int(match.group(1)),
            "open": _decimal(record.get("openPrice"), "openPrice"),
            "high": _decimal(record.get("highPrice"), "highPrice"),
            "low": _decimal(record.get("lowPrice"), "lowPrice"),
            "close": _decimal(record.get("closePrice"), "closePrice"),
            "volume": _integer(record.get("volume"), "volume"),
        }
        row.update(_provenance(record, collected_at=collected_at,
                               operation=TREASURY_YIELD_OPERATION,
                               source_date=source_date, updated_field=None))
        rows.append(row)
    return _frame(rows, KR_TREASURY_YIELD_DAILY)
