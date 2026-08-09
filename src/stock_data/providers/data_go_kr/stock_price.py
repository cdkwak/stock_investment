from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import pandas as pd

from stock_data.contracts.kr_equity import (
    KR_EQUITY_MARKET_CAP_DAILY, KR_EQUITY_PRICE_DAILY,
)
from stock_data.validation.kr_equity import (
    validate_equity_market_cap, validate_equity_price,
)


BASE_URL = "https://apis.data.go.kr/1160100/service/GetStockSecuritiesInfoService"
STOCK_PRICE_ENDPOINT = BASE_URL + "/getStockPriceInfo"
SOURCE_FIELDS = {
    "basDt", "srtnCd", "isinCd", "itmsNm", "mrktCtg", "clpr", "vs",
    "fltRt", "mkp", "hipr", "lopr", "trqu", "trPrc", "lstgStCnt",
    "mrktTotAmt",
}


@dataclass(frozen=True)
class NormalizedStockPrice:
    price: pd.DataFrame
    market_cap: pd.DataFrame


def _integer(item: Mapping[str, object], field: str) -> int:
    value = item.get(field)
    if isinstance(value, bool) or value is None or str(value).strip() == "":
        raise ValueError(f"data.go.kr field {field} is missing")
    try:
        numeric = int(str(value).strip())
    except ValueError:
        raise ValueError(f"data.go.kr field {field} is not an integer") from None
    if numeric < 0:
        raise ValueError(f"data.go.kr field {field} must be nonnegative")
    return numeric


def normalize_stock_price_items(
    items: Sequence[Mapping[str, object]],
) -> NormalizedStockPrice:
    price_rows = []
    cap_rows = []
    for item in items:
        missing = SOURCE_FIELDS - set(item)
        if missing:
            raise ValueError(f"data.go.kr stock item fields missing: {sorted(missing)}")
        parsed_date = pd.to_datetime(str(item["basDt"]), format="%Y%m%d", errors="coerce")
        if pd.isna(parsed_date):
            raise ValueError("data.go.kr basDt is invalid")
        market = str(item["mrktCtg"]).strip()
        symbol = str(item["srtnCd"]).strip()
        if market not in {"KOSPI", "KOSDAQ"}:
            raise ValueError(f"unsupported data.go.kr market: {market}")
        if not symbol:
            raise ValueError("data.go.kr srtnCd is empty")
        common = {"date": parsed_date.strftime("%Y-%m-%d"), "market": market, "symbol": symbol}
        price_rows.append({
            **common, "open": _integer(item, "mkp"), "high": _integer(item, "hipr"),
            "low": _integer(item, "lopr"), "close": _integer(item, "clpr"),
            "volume": _integer(item, "trqu"),
            "trading_value": _integer(item, "trPrc"),
        })
        cap_rows.append({
            **common, "market_cap": _integer(item, "mrktTotAmt"),
            "shares_outstanding": _integer(item, "lstgStCnt"),
        })
    price = pd.DataFrame(price_rows, columns=KR_EQUITY_PRICE_DAILY.column_names)
    cap = pd.DataFrame(cap_rows, columns=KR_EQUITY_MARKET_CAP_DAILY.column_names)
    price = price.sort_values(list(KR_EQUITY_PRICE_DAILY.sort_key), kind="stable").reset_index(drop=True)
    cap = cap.sort_values(list(KR_EQUITY_MARKET_CAP_DAILY.sort_key), kind="stable").reset_index(drop=True)
    validate_equity_price(price, allow_empty=True)
    validate_equity_market_cap(cap, allow_empty=True)
    return NormalizedStockPrice(price, cap)
