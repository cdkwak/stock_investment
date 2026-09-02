from datetime import date
import pandas as pd
import pytest

from stock_data.providers.yahoo import COMMODITY_CONFIG, fetch_commodity_future
from stock_data.validation.global_market import validate_global_commodity_futures


class Response:
    status_code = 200
    content = b'{"captured":true}'
    headers = {"Content-Type": "application/json"}
    def __init__(self, ticker="CL=F"):
        self.ticker = ticker
    def raise_for_status(self): pass
    def json(self):
        return {"chart": {"error": None, "result": [{"meta": {
            "symbol": self.ticker, "instrumentType": "FUTURE", "dataGranularity": "1d",
        }, "timestamp": [1593993600, 1594080000], "indicators": {"quote": [{"open": [1.0, -2.0], "high": [2.0, -1.0], "low": [0.5, -3.0], "close": [1.5, -0.5], "volume": [10, None]}]}}]}}


class Session:
    def get(self, url, *args, **kwargs):
        return Response(url.rsplit("/", 1)[-1].replace("%3D", "="))


class GapSession(Session):
    def __init__(self, gap_kind):
        self.gap_kind = gap_kind

    def get(self, url, *args, **kwargs):
        response = super().get(url, *args, **kwargs)
        payload = response.json()
        item = payload["chart"]["result"][0]
        item["timestamp"] = [
            int(pd.Timestamp(day, tz="UTC").timestamp())
            for day in ("2026-08-26 16:00", "2026-08-27 16:00", "2026-08-28 16:00")
        ]
        quote = item["indicators"]["quote"][0]
        quote.update({
            "open": [70.0, 71.0, 72.0], "high": [71.0, 72.0, 73.0],
            "low": [69.0, 70.0, 71.0], "close": [70.5, 71.5, 72.5],
            "volume": [1000, 1100, 1200],
        })
        if self.gap_kind == "middle":
            for column in ("open", "high", "low", "close"):
                quote[column][1] = None
        elif self.gap_kind == "partial":
            quote["open"][1] = None
        elif self.gap_kind == "all":
            for column in ("open", "high", "low", "close"):
                quote[column] = [None, None, None]
        response.json = lambda: payload
        return response


def test_bounded_symbols_and_explicit_daily_period_capture(tmp_path):
    assert [ticker for ticker, _ in COMMODITY_CONFIG.values()] == [
        "GC=F", "SI=F", "HG=F", "CL=F", "BZ=F", "NQ=F",
        "ES=F", "YM=F",
    ]
    frame = fetch_commodity_future("WTI_CRUDE_OIL", date(1900, 1, 1), date(2026, 8, 14), session=Session(), capture_root=tmp_path)
    assert frame.ohlc_status.tolist() == ["VALID", "SOURCE_RELATION_ANOMALY"]
    assert pd.isna(frame.loc[1, "volume"])
    validate_global_commodity_futures(frame)
    call = next((tmp_path / "yahoo" / "commodity_chart_daily").iterdir())
    assert (call / "response.body").read_bytes() == Response.content


def test_futures_all_null_middle_row_is_recorded_and_dropped():
    frame = fetch_commodity_future(
        "WTI_CRUDE_OIL", date(2026, 8, 26), date(2026, 8, 28),
        session=GapSession("middle"),
    )

    assert frame["date"].tolist() == ["2026-08-26", "2026-08-28"]
    assert frame.attrs["provider_gap_dates"] == ["2026-08-27"]
    validate_global_commodity_futures(frame)


def test_futures_partial_null_row_still_fails_closed():
    with pytest.raises(RuntimeError, match="partial or invalid"):
        fetch_commodity_future(
            "WTI_CRUDE_OIL", date(2026, 8, 26), date(2026, 8, 28),
            session=GapSession("partial"),
        )


def test_futures_all_null_rows_fail_closed():
    with pytest.raises(RuntimeError, match="provider gaps"):
        fetch_commodity_future(
            "WTI_CRUDE_OIL", date(2026, 8, 26), date(2026, 8, 28),
            session=GapSession("all"),
        )


def test_live_forming_futures_bar_is_not_normalized_as_a_completed_duplicate(tmp_path):
    class LiveResponse(Response):
        def json(self):
            payload = super().json()
            item = payload["chart"]["result"][0]
            item["timestamp"] = [1787025600, 1787107075]
            item["meta"]["regularMarketTime"] = 1787107075
            return payload

    class LiveSession:
        def get(self, *args, **kwargs):
            return LiveResponse("NQ=F")

    frame = fetch_commodity_future(
        "NASDAQ100_FUTURES", date(2026, 8, 18), date(2026, 8, 18),
        session=LiveSession(), capture_root=tmp_path,
    )
    assert frame["date"].tolist() == ["2026-08-18"]
    assert frame["symbol"].tolist() == ["NASDAQ100_FUTURES"]


@pytest.mark.parametrize(
    ("symbol", "ticker", "asset"),
    (
        ("SP500_FUTURES", "ES=F", "S&P 500 E-mini vendor-continuous future"),
        ("DOW_FUTURES", "YM=F", "Dow E-mini vendor-continuous future"),
    ),
)
def test_new_futures_use_the_existing_contract_fields_and_identity_path(
    symbol, ticker, asset,
):
    frame = fetch_commodity_future(
        symbol, date(2020, 7, 5), date(2020, 7, 8), session=Session(),
    )
    assert set(frame["symbol"]) == {symbol}
    assert set(frame["source_ticker"]) == {ticker}
    assert set(frame["asset"]) == {asset}
    assert frame["ohlc_status"].tolist() == ["VALID", "SOURCE_RELATION_ANOMALY"]
