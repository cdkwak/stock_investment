from datetime import date
import pandas as pd

from stock_data.providers.yahoo import COMMODITY_CONFIG, fetch_commodity_future
from stock_data.validation.global_market import validate_global_commodity_futures


class Response:
    status_code = 200
    content = b'{"captured":true}'
    headers = {"Content-Type": "application/json"}
    def raise_for_status(self): pass
    def json(self):
        return {"chart": {"error": None, "result": [{"meta": {"dataGranularity": "1d"}, "timestamp": [1593993600, 1594080000], "indicators": {"quote": [{"open": [1.0, -2.0], "high": [2.0, -1.0], "low": [0.5, -3.0], "close": [1.5, -0.5], "volume": [10, None]}]}}]}}


class Session:
    def get(self, *args, **kwargs): return Response()


def test_bounded_symbols_and_explicit_daily_period_capture(tmp_path):
    assert [ticker for ticker, _ in COMMODITY_CONFIG.values()] == [
        "GC=F", "SI=F", "HG=F", "CL=F", "BZ=F", "NQ=F",
    ]
    frame = fetch_commodity_future("WTI_CRUDE_OIL", date(1900, 1, 1), date(2026, 8, 14), session=Session(), capture_root=tmp_path)
    assert frame.ohlc_status.tolist() == ["VALID", "SOURCE_RELATION_ANOMALY"]
    assert pd.isna(frame.loc[1, "volume"])
    validate_global_commodity_futures(frame)
    call = next((tmp_path / "yahoo" / "commodity_chart_daily").iterdir())
    assert (call / "response.body").read_bytes() == Response.content


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
            return LiveResponse()

    frame = fetch_commodity_future(
        "NASDAQ100_FUTURES", date(2026, 8, 18), date(2026, 8, 18),
        session=LiveSession(), capture_root=tmp_path,
    )
    assert frame["date"].tolist() == ["2026-08-18"]
    assert frame["symbol"].tolist() == ["NASDAQ100_FUTURES"]
