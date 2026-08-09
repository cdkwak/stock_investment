from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Mapping, Protocol, Sequence


@dataclass(frozen=True)
class KrxDatasetMapping:
    dataset: str
    api_names: tuple[str, ...]
    available_from: date | None
    api_ids: tuple[str, ...] | None
    approval_required: bool
    status: str
    note: str


KRX_DATASET_MAPPINGS: Mapping[str, KrxDatasetMapping] = {
    "kr_index_daily": KrxDatasetMapping(
        dataset="kr_index_daily",
        api_names=("KOSPI 시리즈 일별시세정보", "KOSDAQ 시리즈 일별시세정보"),
        available_from=date(2010, 1, 4), api_ids=None, approval_required=True,
        status="blocked",
        note="API IDs and response fields require approved official specifications.",
    ),
    "kr_equity_price_daily": KrxDatasetMapping(
        dataset="kr_equity_price_daily",
        api_names=("유가증권 일별매매정보", "코스닥 일별매매정보"),
        available_from=date(2010, 1, 4), api_ids=None, approval_required=True,
        status="blocked",
        note="Price-field mapping requires approved official specifications.",
    ),
    "kr_equity_market_cap_daily": KrxDatasetMapping(
        dataset="kr_equity_market_cap_daily", api_names=(), available_from=None,
        api_ids=None, approval_required=True, status="unconfirmed",
        note="No independent official API contract or verified output-field mapping yet.",
    ),
    "kr_equity_master": KrxDatasetMapping(
        dataset="kr_equity_master",
        api_names=("유가증권 종목기본정보", "코스닥 종목기본정보"),
        available_from=date(2010, 1, 4), api_ids=None, approval_required=True,
        status="blocked",
        note="Listing lifecycle fields require approved official specifications.",
    ),
    "kr_investor_flow_daily": KrxDatasetMapping(
        dataset="kr_investor_flow_daily", api_names=(), available_from=None,
        api_ids=None, approval_required=True, status="unconfirmed",
        note="No matching API was found in the official public service list.",
    ),
    "kr_derivatives_futures_daily": KrxDatasetMapping(
        dataset="kr_derivatives_futures_daily",
        api_names=("선물 일별매매정보 (주식선물外)",),
        available_from=date(2010, 1, 4), api_ids=("fut_bydd_trd",),
        approval_required=True, status="blocked",
        note="Verified contract; live access remains prohibited until restrictions clear.",
    ),
    "kr_derivatives_options_daily": KrxDatasetMapping(
        dataset="kr_derivatives_options_daily",
        api_names=("옵션 일별매매정보 (주식옵션外)",),
        available_from=date(2010, 1, 4), api_ids=("opt_bydd_trd",),
        approval_required=True, status="blocked",
        note="Verified contract; live access remains prohibited until restrictions clear.",
    ),
}


class KrxOpenApiProvider(Protocol):
    """Transport-independent interface. Implementations must validate before storage."""

    def fetch_daily(
        self, mapping: KrxDatasetMapping, base_date: date
    ) -> Sequence[Mapping[str, object]]:
        """Return source rows or raise; an empty result must be explicitly classified."""
        ...
