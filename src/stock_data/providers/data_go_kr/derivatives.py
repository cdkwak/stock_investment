from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
import re
from typing import Mapping, Sequence

import pandas as pd

from stock_data.contracts.base import DatasetContract
from stock_data.contracts.kr_derivatives import (
    FUTURES_OPERATION,
    KR_KOSDAQ150_FUTURES_DAILY,
    KR_KOSDAQ150_OPTIONS_DAILY,
    KR_KOSPI200_FUTURES_DAILY,
    KR_KOSPI200_OPTIONS_DAILY,
    OPTIONS_OPERATION,
    SOURCE,
)
from stock_data.providers.data_go_kr.data_v1 import ENDPOINTS
from stock_data.validation.data_v1 import validate_data_v1


@dataclass(frozen=True)
class DerivativeProductSpec:
    key: str
    kind: str
    underlying: str
    product_category: str
    endpoint: str
    source_operation: str
    contract: DatasetContract


PRODUCT_SPECS = {
    "kospi200_futures": DerivativeProductSpec(
        key="kospi200_futures",
        kind="futures",
        underlying="KOSPI200",
        product_category="파생 선물 코스피200 (주간)",
        endpoint=ENDPOINTS["futures"],
        source_operation=FUTURES_OPERATION,
        contract=KR_KOSPI200_FUTURES_DAILY,
    ),
    "kospi200_options": DerivativeProductSpec(
        key="kospi200_options",
        kind="options",
        underlying="KOSPI200",
        product_category="파생 옵션 코스피200",
        endpoint=ENDPOINTS["options"],
        source_operation=OPTIONS_OPERATION,
        contract=KR_KOSPI200_OPTIONS_DAILY,
    ),
    "kosdaq150_futures": DerivativeProductSpec(
        key="kosdaq150_futures",
        kind="futures",
        underlying="KOSDAQ150",
        product_category="파생 선물 코스닥150",
        endpoint=ENDPOINTS["futures"],
        source_operation=FUTURES_OPERATION,
        contract=KR_KOSDAQ150_FUTURES_DAILY,
    ),
    "kosdaq150_options": DerivativeProductSpec(
        key="kosdaq150_options",
        kind="options",
        underlying="KOSDAQ150",
        product_category="파생 옵션 코스닥150",
        endpoint=ENDPOINTS["options"],
        source_operation=OPTIONS_OPERATION,
        contract=KR_KOSDAQ150_OPTIONS_DAILY,
    ),
}

_FUTURES_NAME = re.compile(r"^(코스피200|코스닥150)\s+F\s+(\d{6})$")
_OPTIONS_NAME = re.compile(
    r"^(코스피200|코스닥150)\s+([CP])\s+(\d{6})\s+([0-9][0-9,]*(?:\.[0-9]+)?)$"
)
_KOREAN_UNDERLYING = {"KOSPI200": "코스피200", "KOSDAQ150": "코스닥150"}


def request_filters(spec: DerivativeProductSpec, base_date: str) -> dict[str, str]:
    if len(base_date) != 8 or not base_date.isdigit():
        raise ValueError("base_date must be YYYYMMDD")
    return {"basDt": base_date, "prdCtg": spec.product_category}


def range_request_filters(
    spec: DerivativeProductSpec, start_date: str, end_date: str
) -> dict[str, str]:
    if any(len(value) != 8 or not value.isdigit() for value in (start_date, end_date)):
        raise ValueError("range dates must be YYYYMMDD")
    if start_date > end_date:
        raise ValueError("start_date must not be after end_date")
    # Live verification on 2020-01-03..2020-01-31 returned through 01-30,
    # while an exact 01-31 request returned data. Treat the portal's endBasDt
    # as an observed exclusive upper bound. Landing preserves the request and
    # any boundary row; normalized filtering remains tied to requested dates.
    exclusive_end = (
        datetime.strptime(end_date, "%Y%m%d") + timedelta(days=1)
    ).strftime("%Y%m%d")
    return {
        "beginBasDt": start_date,
        "endBasDt": exclusive_end,
        "prdCtg": spec.product_category,
    }


def _text(item: Mapping[str, object], field: str) -> str:
    value = item.get(field)
    if value is None or str(value).strip() in {"", "NULL"}:
        raise ValueError(f"field {field} is missing")
    return str(value).strip()


def _float(item: Mapping[str, object], field: str) -> float:
    try:
        return float(_text(item, field).replace(",", ""))
    except ValueError:
        raise ValueError(f"field {field} is not numeric") from None


def _int(item: Mapping[str, object], field: str) -> int:
    value = _text(item, field).replace(",", "")
    if not re.fullmatch(r"[+-]?\d+", value):
        raise ValueError(f"field {field} is not an integer")
    return int(value)


def _date(item: Mapping[str, object]) -> str:
    value = _text(item, "basDt")
    parsed = pd.to_datetime(value, format="%Y%m%d", errors="coerce")
    if pd.isna(parsed):
        raise ValueError("field basDt is not YYYYMMDD")
    return parsed.strftime("%Y-%m-%d")


def _common(item: Mapping[str, object], spec: DerivativeProductSpec, maturity: str) -> dict[str, object]:
    return {
        "date": _date(item),
        "underlying": spec.underlying,
        "contract": _text(item, "srtnCd"),
        "isin": _text(item, "isinCd"),
        "name": _text(item, "itmsNm"),
        "product_category": _text(item, "prdCtg"),
        "maturity_month": f"{maturity[:4]}-{maturity[4:]}",
    }


def _activity(item: Mapping[str, object], spec: DerivativeProductSpec) -> dict[str, object]:
    return {
        "volume": _int(item, "trqu"),
        "trading_value": _int(item, "trPrc"),
        "open_interest": _int(item, "opnint"),
        "source": SOURCE,
        "source_operation": spec.source_operation,
    }


def normalize_derivatives(
    items: Sequence[Mapping[str, object]], spec: DerivativeProductSpec
) -> pd.DataFrame:
    expected_name = _KOREAN_UNDERLYING[spec.underlying]
    rows: list[dict[str, object]] = []
    for item in items:
        category = _text(item, "prdCtg")
        if category != spec.product_category:
            # The public endpoint may apply prdCtg as a prefix filter (for
            # example regular KOSPI200 options can include weekly options).
            # Landing remains lossless; only the exact requested category is
            # promoted to this narrowly scoped normalized Dataset.
            continue
        name = _text(item, "itmsNm")
        if spec.kind == "futures":
            match = _FUTURES_NAME.fullmatch(name)
            if match is None:
                # The category also contains calendar-spread rows. Landing keeps them;
                # only outright contracts are promoted to normalized.
                if f"{expected_name} SP " in name:
                    continue
                raise ValueError(f"unexpected futures item name: {name}")
            if match.group(1) != expected_name:
                raise ValueError(f"unexpected futures underlying: {name}")
            row = {
                **_common(item, spec, match.group(2)),
                "open": _float(item, "mkp"),
                "high": _float(item, "hipr"),
                "low": _float(item, "lopr"),
                "close": _float(item, "clpr"),
                "underlying_value": _float(item, "sptPrc"),
                "settlement_price": _float(item, "stmPrc"),
                **_activity(item, spec),
            }
        else:
            match = _OPTIONS_NAME.fullmatch(name)
            if match is None or match.group(1) != expected_name:
                raise ValueError(f"unexpected options item name: {name}")
            row = {
                **_common(item, spec, match.group(3)),
                "call_put": {"C": "CALL", "P": "PUT"}[match.group(2)],
                "strike": float(match.group(4).replace(",", "")),
                "open": _float(item, "mkp"),
                "high": _float(item, "hipr"),
                "low": _float(item, "lopr"),
                "close": _float(item, "clpr"),
                "next_day_base_price": _float(item, "nxtDdBsPrc"),
                "implied_volatility": _float(item, "iptVlty"),
                **_activity(item, spec),
            }
        rows.append(row)
    frame = pd.DataFrame(rows, columns=spec.contract.column_names)
    frame = frame.sort_values(list(spec.contract.sort_key), kind="stable").reset_index(drop=True)
    validate_data_v1(frame, spec.contract)
    return frame
