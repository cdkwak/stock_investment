from datetime import date

import pandas as pd
import pytest

from stock_data.derived.market_breadth import calculate_market_breadth
from stock_data.providers.pykrx.kr_equity_daily import _fetch_once
from stock_data.providers.pykrx.kr_investor_flow import fetch_investor_flow
from stock_data.validation.kr_market import validate_investor_flow


class FakeStock:
    def get_market_ohlcv(self, text, market):
        return pd.DataFrame(
            {
                "시가": [100, 0], "고가": [110, 0], "저가": [90, 0],
                "종가": [105, 0], "거래량": [1000, 0], "거래대금": [100000, 0],
            },
            index=["000001", "000002"],
        )

    def get_market_cap(self, text, market):
        return pd.DataFrame(
            {"시가총액": [1_000_000, 2_000_000], "상장주식수": [10_000, 20_000]},
            index=["000001", "000002"],
        )

    def get_market_price_change(self, start, end, market, adjusted, delist):
        return pd.DataFrame({"종목명": ["Alpha", "Beta"]}, index=["000001", "000002"])


class InvestorStock:
    def get_market_trading_value_by_date(self, *args, **kwargs):
        return pd.DataFrame(
            {
                "기관합계": [10], "기타법인": [-2], "개인": [-3],
                "외국인합계": [-5], "전체": [0],
            }, index=pd.to_datetime(["2026-08-07"]),
        )


def test_investor_flow_normalization_and_total() -> None:
    result = fetch_investor_flow(
        date(2026, 8, 7), date(2026, 8, 7), "KOSPI", stock_module=InvestorStock()
    )
    validate_investor_flow(result)
    assert result.loc[0, "institution_net_buy"] == 10
    assert result.loc[0, "total_net_buy"] == 0


def test_investor_total_mismatch_is_rejected() -> None:
    result = fetch_investor_flow(
        date(2026, 8, 7), date(2026, 8, 7), "KOSPI", stock_module=InvestorStock()
    )
    result.loc[0, "total_net_buy"] = 1
    with pytest.raises(ValueError, match="sum"):
        validate_investor_flow(result)


def test_breadth_uses_previous_close_not_same_day_open() -> None:
    first = _fetch_once(FakeStock(), date(2026, 8, 6), "KOSPI").price
    second = _fetch_once(FakeStock(), date(2026, 8, 7), "KOSPI").price
    second.loc[second["symbol"].eq("000001"), "close"] = 106
    result = calculate_market_breadth(
        pd.concat([first, second], ignore_index=True).sort_values(
            ["date", "market", "symbol"], kind="stable"
        ).reset_index(drop=True)
    )
    assert result.iloc[0][["advancing", "declining", "unchanged", "total"]].tolist() == [1, 0, 1, 2]


def test_breadth_uses_point_in_time_canonical_membership() -> None:
    first = _fetch_once(FakeStock(), date(2026, 8, 6), "KOSPI").price
    second = _fetch_once(FakeStock(), date(2026, 8, 7), "KOSPI").price
    prices = pd.concat([first, second], ignore_index=True).sort_values(
        ["date", "market", "symbol"], kind="stable").reset_index(drop=True)
    universe = pd.DataFrame([{"date":"2026-08-07","market":"KOSPI","symbol":"000001"}])
    result = calculate_market_breadth(prices, universe)
    assert result.iloc[0]["total"] == 1
