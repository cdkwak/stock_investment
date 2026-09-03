from datetime import date, datetime, timezone
import hashlib
import json

import pandas as pd
import pytest

from stock_data.providers.yahoo import (
    ETF_REGISTRY, fetch_global_etf, fetch_global_index, fetch_global_market_60m,
    fetch_market_60m,
)
from stock_data.providers.yahoo_15m import fetch_market_15m
from stock_data.providers.fred import fetch_series
from stock_data.providers.public_http_capture import PublicHttpCaptureError
from stock_data.validation.global_market import validate_global_etf, validate_global_index


class Response:
    def __init__(self,payload=None,text=""):
        self._payload=payload; self.text=text
        self.content=text.encode("utf-8") if text else json.dumps(payload).encode("utf-8")
        self.status_code=200; self.headers={"Content-Type":"application/json"}
    def raise_for_status(self): pass
    def json(self): return self._payload


class YahooSession:
    @staticmethod
    def get(url,*args,**kwargs):
        ticker = url.rsplit("/", 1)[-1].replace("%5E", "^")
        return Response({"chart":{"error":None,"result":[{
            "meta": {
                "symbol": ticker, "instrumentType": "INDEX",
                "dataGranularity": "1d",
            },
            "timestamp":[1786032000],
            "indicators":{"quote":[{"open":[100.0],"high":[110.0],"low":[90.0],"close":[105.0],"volume":[1000]}]}
        }]}})


@pytest.mark.parametrize(
    ("symbol", "ticker"),
    (
        ("SP500", "^GSPC"),
        ("SOX", "^SOX"),
        ("DOW_JONES", "^DJI"),
        ("DOLLAR_INDEX", "DX-Y.NYB"),
    ),
)
def test_yahoo_registered_index_arrays_are_identity_validated_and_normalized(
    symbol: str, ticker: str,
) -> None:
    frame=fetch_global_index(symbol,date(2026,8,1),date(2026,8,7),session=YahooSession)
    assert len(frame)==1 and frame.symbol.item()==symbol
    assert frame.source_ticker.item() == ticker


class BadYahoo(YahooSession):
    @staticmethod
    def get(*args,**kwargs):
        response=YahooSession.get(*args, **kwargs); response._payload["chart"]["result"][0]["indicators"]["quote"][0]["volume"]=[]
        return response


def test_yahoo_array_mismatch_is_rejected() -> None:
    with pytest.raises(RuntimeError,match="lengths"):
        fetch_global_index("SP500",date(2026,8,1),date(2026,8,7),session=BadYahoo)


class YahooEtfSession:
    @staticmethod
    def get(url, *args, **kwargs):
        ticker = url.rsplit("/", 1)[-1]
        exchange = ETF_REGISTRY[ticker]["accepted_yahoo_exchanges"][0]
        return Response({"chart": {"error": None, "result": [{
            "meta": {
                "symbol": ticker, "instrumentType": "ETF", "dataGranularity": "1d",
                "currency": "USD", "exchangeName": exchange,
            },
            "timestamp": [1786032000],
            "indicators": {
                "quote": [{
                    "open": [70.0], "high": [71.0], "low": [69.0],
                    "close": [70.5], "volume": [1000],
                }],
                "adjclose": [{"adjclose": [70.25]}],
            },
        }]}})


@pytest.mark.parametrize(
    ("symbol", "issuer", "official_name", "multiple"),
    (
        ("SOXX", "iShares", "iShares Semiconductor ETF", 1),
        ("EWY", "iShares", "iShares MSCI South Korea ETF", 1),
        ("SOXL", "Direxion", "Direxion Daily Semiconductor Bull 3X Shares", 3),
        ("TQQQ", "ProShares", "ProShares UltraPro QQQ", 3),
        ("QLD", "ProShares", "ProShares Ultra QQQ", 2),
        ("TLT", "iShares", "iShares 20+ Year Treasury Bond ETF", 1),
        ("QQQ", "Invesco", "Invesco QQQ Trust, Series 1", 1),
        ("SPY", "State Street Global Advisors", "SPDR S&P 500 ETF Trust", 1),
    ),
)
def test_watchlist_etf_registry_and_daily_identity_are_validated_offline(
    symbol: str, issuer: str, official_name: str, multiple: int,
) -> None:
    spec = ETF_REGISTRY[symbol]
    assert spec["source_ticker"] == symbol
    assert spec["issuer"] == issuer
    assert spec["official_fund_name"] == official_name
    assert spec["official_exchange"] in {"NASDAQ", "NYSE Arca"}
    assert str(spec["official_identity_url"]).startswith("https://")
    assert spec["expected_currency"] == "USD"
    assert spec["accepted_yahoo_exchanges"]
    assert spec["leverage_multiple"] == multiple
    assert spec["automation_enabled"] is True
    frame = fetch_global_etf(
        symbol, date(2026, 8, 1), date(2026, 8, 7), session=YahooEtfSession,
        retrieved_at=datetime(2026, 8, 8, tzinfo=timezone.utc),
    )
    assert frame[["symbol", "source_ticker", "currency"]].iloc[0].tolist() == [
        symbol, symbol, "USD",
    ]
    assert frame["exchange"].iloc[0] in spec["accepted_yahoo_exchanges"]


def _daily_gap_payload(ticker: str, instrument_type: str, gap_kind: str) -> dict:
    timestamps = [
        int(pd.Timestamp(day, tz="UTC").timestamp())
        for day in ("2026-08-26 16:00", "2026-08-27 16:00", "2026-08-28 16:00")
    ]
    quote = {
        "open": [70.0, 71.0, 72.0],
        "high": [71.0, 72.0, 73.0],
        "low": [69.0, 70.0, 71.0],
        "close": [70.5, 71.5, 72.5],
        "volume": [1000, 1100, 1200],
    }
    adjusted = [70.25, 71.25, 72.25]
    if gap_kind == "middle":
        for column in ("open", "high", "low", "close"):
            quote[column][1] = None
        adjusted[1] = None
    elif gap_kind == "partial":
        quote["open"][1] = None
    elif gap_kind == "all":
        for column in ("open", "high", "low", "close"):
            quote[column] = [None, None, None]
        adjusted = [None, None, None]
    indicators = {"quote": [quote]}
    meta = {"symbol": ticker, "instrumentType": instrument_type, "dataGranularity": "1d"}
    if instrument_type == "ETF":
        meta.update({"currency": "USD", "exchangeName": "PCX"})
        indicators["adjclose"] = [{"adjclose": adjusted}]
    return {"chart": {"error": None, "result": [{
        "meta": meta, "timestamp": timestamps, "indicators": indicators,
    }]}}


class YahooDailyGapSession:
    def __init__(self, instrument_type: str, gap_kind: str):
        self.instrument_type = instrument_type
        self.gap_kind = gap_kind

    def get(self, url, *args, **kwargs):
        ticker = url.rsplit("/", 1)[-1].replace("%5E", "^")
        return Response(_daily_gap_payload(ticker, self.instrument_type, self.gap_kind))


@pytest.mark.parametrize(
    ("instrument_type", "fetcher", "symbol"),
    (("INDEX", fetch_global_index, "SP500"), ("ETF", fetch_global_etf, "EWY")),
)
def test_yahoo_daily_all_null_middle_row_is_recorded_and_dropped(
    instrument_type, fetcher, symbol,
) -> None:
    kwargs = {"session": YahooDailyGapSession(instrument_type, "middle")}
    if instrument_type == "ETF":
        kwargs["retrieved_at"] = datetime(2026, 8, 29, tzinfo=timezone.utc)
    frame = fetcher(symbol, date(2026, 8, 26), date(2026, 8, 28), **kwargs)

    assert frame["date"].tolist() == ["2026-08-26", "2026-08-28"]
    assert frame.attrs["provider_gap_dates"] == ["2026-08-27"]
    (validate_global_etf if instrument_type == "ETF" else validate_global_index)(frame)


@pytest.mark.parametrize(
    ("instrument_type", "fetcher", "symbol"),
    (("INDEX", fetch_global_index, "SP500"), ("ETF", fetch_global_etf, "EWY")),
)
def test_yahoo_daily_partial_null_row_still_fails_closed(
    instrument_type, fetcher, symbol,
) -> None:
    kwargs = {"session": YahooDailyGapSession(instrument_type, "partial")}
    if instrument_type == "ETF":
        kwargs["retrieved_at"] = datetime(2026, 8, 29, tzinfo=timezone.utc)
    with pytest.raises((RuntimeError, ValueError), match="missing|invalid"):
        fetcher(symbol, date(2026, 8, 26), date(2026, 8, 28), **kwargs)


@pytest.mark.parametrize(
    ("instrument_type", "fetcher", "symbol"),
    (("INDEX", fetch_global_index, "SP500"), ("ETF", fetch_global_etf, "EWY")),
)
def test_yahoo_daily_all_null_rows_fail_closed(
    instrument_type, fetcher, symbol,
) -> None:
    kwargs = {"session": YahooDailyGapSession(instrument_type, "all")}
    if instrument_type == "ETF":
        kwargs["retrieved_at"] = datetime(2026, 8, 29, tzinfo=timezone.utc)
    with pytest.raises(RuntimeError, match="provider gaps"):
        fetcher(symbol, date(2026, 8, 26), date(2026, 8, 28), **kwargs)


class Yahoo60mSession:
    calls = []

    @classmethod
    def get(cls, *args, **kwargs):
        cls.calls.append(kwargs)
        starts = (
            datetime(2026, 8, 17, 13, 30, tzinfo=timezone.utc),
            datetime(2026, 8, 17, 14, 30, tzinfo=timezone.utc),
        )
        return Response({"chart": {"error": None, "result": [{
            "meta": {
                "symbol": "SPY", "dataGranularity": "1h",
                "exchangeTimezoneName": "America/New_York",
            },
            "timestamp": [int(value.timestamp()) for value in starts],
            "indicators": {"quote": [{
                "open": [100.0, 101.0], "high": [101.0, 102.0],
                "low": [99.0, 100.0], "close": [100.5, 101.5],
                "volume": [1000, 1100],
            }]},
        }]}})


def test_yahoo_60m_requires_explicit_calendar_and_complete_session() -> None:
    Yahoo60mSession.calls.clear()
    frame = fetch_market_60m(
        "SPY",
        session_windows={
            date(2026, 8, 17): (
                datetime(2026, 8, 17, 13, 30, tzinfo=timezone.utc),
                datetime(2026, 8, 17, 15, 30, tzinfo=timezone.utc),
            )
        },
        session=Yahoo60mSession,
        retrieved_at=datetime(2026, 8, 18, tzinfo=timezone.utc),
    )
    assert len(frame) == 2
    assert set(frame["provider"]) == {"yahoo_chart_api"}
    assert Yahoo60mSession.calls[0]["params"]["includePrePost"] == "false"


def test_yahoo_60m_rejects_unregistered_symbol_before_network() -> None:
    Yahoo60mSession.calls.clear()
    with pytest.raises(ValueError, match="unregistered"):
        fetch_market_60m(
            "UNKNOWN",
            session_windows={
                date(2026, 8, 17): (
                    datetime(2026, 8, 17, 13, 30, tzinfo=timezone.utc),
                    datetime(2026, 8, 17, 15, 30, tzinfo=timezone.utc),
                )
            },
            session=Yahoo60mSession,
        )
    assert Yahoo60mSession.calls == []


class YahooGlobal60mSession:
    calls = []

    @classmethod
    def get(cls, url, **kwargs):
        cls.calls.append((url, kwargs))
        ticker = url.rsplit("/", 1)[-1].replace("%3D", "=")
        starts = (
            datetime(2026, 8, 19, 10, tzinfo=timezone.utc),
            datetime(2026, 8, 19, 11, tzinfo=timezone.utc),
            datetime(2026, 8, 19, 12, tzinfo=timezone.utc),
        )
        instrument = "CURRENCY" if ticker == "KRW=X" else "FUTURE"
        returned_ticker = "USDKRW=X" if ticker == "KRW=X" else ticker
        return Response({"chart": {"error": None, "result": [{
            "meta": {
                "symbol": returned_ticker, "dataGranularity": "60m",
                "instrumentType": instrument,
                "regularMarketTime": int(starts[-1].timestamp()),
            },
            "timestamp": [int(value.timestamp()) for value in starts],
            "indicators": {"quote": [{
                "open": [100.0, 101.0, 102.0], "high": [101.0, 102.0, 103.0],
                "low": [99.0, 100.0, 101.0], "close": [100.5, 101.5, 102.5],
                "volume": [None, None, None],
            }]},
        }]}})


def test_yahoo_global_60m_keeps_only_finalized_bars_and_delayed_identity() -> None:
    YahooGlobal60mSession.calls.clear()
    frame = fetch_global_market_60m(
        "USD_KRW_60M",
        start=datetime(2026, 8, 19, 9, tzinfo=timezone.utc),
        end=datetime(2026, 8, 19, 14, tzinfo=timezone.utc),
        session=YahooGlobal60mSession,
        retrieved_at=datetime(2026, 8, 19, 12, 30, tzinfo=timezone.utc),
    )
    assert frame["bar_start"].tolist() == list(pd.to_datetime([
        "2026-08-19T10:00:00Z", "2026-08-19T11:00:00Z",
    ]))
    assert frame["session"].eq("GLOBAL_CONTINUOUS").all()
    assert frame["adjustment_status"].eq("PROVIDER_UNADJUSTED_INTRADAY_DELAYED").all()
    assert YahooGlobal60mSession.calls[0][1]["params"]["includePrePost"] == "true"


def test_yahoo_global_60m_rejects_unregistered_identity_without_network() -> None:
    YahooGlobal60mSession.calls.clear()
    with pytest.raises(ValueError, match="unregistered"):
        fetch_global_market_60m(
            "DGS10",
            start=datetime(2026, 8, 19, 9, tzinfo=timezone.utc),
            end=datetime(2026, 8, 19, 14, tzinfo=timezone.utc),
            session=YahooGlobal60mSession,
        )
    assert YahooGlobal60mSession.calls == []


class YahooKrx60mSession:
    @classmethod
    def get(cls, url, **kwargs):
        starts = pd.to_datetime(["2026-08-21T05:00:00Z", "2026-08-21T06:00:00Z"])
        return Response({"chart": {"error": None, "result": [{
            "meta": {
                "symbol": "^KS11", "dataGranularity": "60m",
                "instrumentType": "INDEX",
                "regularMarketTime": int(pd.Timestamp("2026-08-21T06:30:00Z").timestamp()),
            },
            "timestamp": [int(value.timestamp()) for value in starts],
            "indicators": {"quote": [{
                "open": [6900.0, 6910.0], "high": [6920.0, 6920.0],
                "low": [6890.0, 6900.0], "close": [6910.0, 6912.95],
                "volume": [None, None],
            }]},
        }]}})


def test_yahoo_krx_final_60m_bar_is_truncated_to_official_1530_close() -> None:
    frame = fetch_global_market_60m(
        "KOSPI_CURRENT_60M",
        start=datetime(2026, 8, 21, 4, tzinfo=timezone.utc),
        end=datetime(2026, 8, 21, 7, tzinfo=timezone.utc),
        session=YahooKrx60mSession,
        retrieved_at=datetime(2026, 8, 21, 7, tzinfo=timezone.utc),
    )

    assert frame["bar_end"].tolist() == list(pd.to_datetime([
        "2026-08-21T06:00:00Z", "2026-08-21T06:30:00Z",
    ]))
    assert frame["actual_duration_minutes"].tolist() == [60, 30]


class YahooUsCash60mSession:
    @classmethod
    def get(cls, url, **kwargs):
        starts = pd.to_datetime([
            "2026-08-21T19:30:00Z", "2026-08-21T20:00:00Z",
        ])
        return Response({"chart": {"error": None, "result": [{
            "meta": {
                "symbol": "^GSPC", "dataGranularity": "60m",
                "instrumentType": "INDEX",
                "regularMarketTime": int(pd.Timestamp("2026-08-21T20:05:00Z").timestamp()),
            },
            "timestamp": [int(value.timestamp()) for value in starts],
            "indicators": {"quote": [{
                "open": [7670.0, 7680.0], "high": [7680.0, 7690.0],
                "low": [7660.0, 7670.0], "close": [7675.0, 7685.0],
                "volume": [None, None],
            }]},
        }]}})


def test_yahoo_us_cash_final_bar_ends_at_1600_and_post_close_bar_is_rejected() -> None:
    frame = fetch_global_market_60m(
        "SP500_CURRENT_60M",
        start=datetime(2026, 8, 21, 19, tzinfo=timezone.utc),
        end=datetime(2026, 8, 21, 21, tzinfo=timezone.utc),
        session=YahooUsCash60mSession,
        retrieved_at=datetime(2026, 8, 21, 21, tzinfo=timezone.utc),
    )

    assert frame["bar_start"].tolist() == list(pd.to_datetime([
        "2026-08-21T19:30:00Z",
    ]))
    assert frame["bar_end"].tolist() == list(pd.to_datetime([
        "2026-08-21T20:00:00Z",
    ]))
    assert frame["actual_duration_minutes"].tolist() == [30]


class Yahoo15mSession:
    calls = []

    @classmethod
    def get(cls, url, **kwargs):
        cls.calls.append((url, kwargs))
        starts = pd.to_datetime([
            "2026-08-19T13:30:00Z",
            "2026-08-19T13:45:00Z",
            "2026-08-19T14:00:00Z",
        ])
        ticker = url.rsplit("/", 1)[-1].replace("%5E", "^").replace("%3D", "=")
        return Response({"chart": {"error": None, "result": [{
            "meta": {
                "symbol": ticker,
                "dataGranularity": "15m",
                "exchangeTimezoneName": "America/New_York",
            },
            "timestamp": [int(value.timestamp()) for value in starts],
            "indicators": {"quote": [{
                "open": [100.0, 101.0, 102.0],
                "high": [101.0, 102.0, 103.0],
                "low": [99.0, 100.0, 101.0],
                "close": [100.5, 101.5, 102.5],
                "volume": [100, 110, 120],
            }]},
        }]}})


def test_yahoo_15m_retains_only_completed_native_bars() -> None:
    Yahoo15mSession.calls.clear()
    frame = fetch_market_15m(
        "^GSPC",
        start=datetime(2026, 8, 19, 13, 30, tzinfo=timezone.utc),
        end=datetime(2026, 8, 19, 14, 15, tzinfo=timezone.utc),
        retrieved_at=datetime(2026, 8, 19, 14, 7, tzinfo=timezone.utc),
        session=Yahoo15mSession,
    )
    assert frame["bar_start"].tolist() == list(pd.to_datetime([
        "2026-08-19T13:30:00Z", "2026-08-19T13:45:00Z",
    ]))
    assert frame["display_timezone"].eq("Asia/Seoul").all()
    assert frame["data_availability"].eq(
        "INDICATIVE_DELAYED_NOT_LICENSED_REALTIME"
    ).all()
    assert Yahoo15mSession.calls[0][1]["params"]["interval"] == "15m"
    assert Yahoo15mSession.calls[0][1]["params"]["includePrePost"] == "false"


class Yahoo15mOffGridSession:
    timestamps = (
        "2026-08-19T13:30:00Z",
        "2026-08-19T13:47:01Z",
    )

    @classmethod
    def get(cls, url, **kwargs):
        starts = pd.to_datetime(cls.timestamps)
        ticker = url.rsplit("/", 1)[-1].replace("%5E", "^").replace("%3D", "=")
        return Response({"chart": {"error": None, "result": [{
            "meta": {
                "symbol": ticker,
                "dataGranularity": "15m",
                "exchangeTimezoneName": "America/New_York",
            },
            "timestamp": [int(value.timestamp()) for value in starts],
            "indicators": {"quote": [{
                "open": [100.0, 101.0], "high": [101.0, 102.0],
                "low": [99.0, 100.0], "close": [100.5, 101.5],
                "volume": [100, 110],
            }]},
        }]}})


def test_yahoo_15m_omits_only_a_trailing_off_grid_snapshot_row() -> None:
    frame = fetch_market_15m(
        "^VIX",
        start=datetime(2026, 8, 19, 13, 30, tzinfo=timezone.utc),
        end=datetime(2026, 8, 19, 14, 15, tzinfo=timezone.utc),
        retrieved_at=datetime(2026, 8, 19, 14, 15, tzinfo=timezone.utc),
        session=Yahoo15mOffGridSession,
    )

    assert frame["bar_start"].tolist() == list(pd.to_datetime([
        "2026-08-19T13:30:00Z",
    ]))


def test_yahoo_15m_preserves_treasury_phase_and_omits_trailing_snapshot() -> None:
    class TreasurySession:
        @classmethod
        def get(cls, url, **kwargs):
            starts = pd.to_datetime((
                "2026-08-19T13:20:00Z",
                "2026-08-19T13:35:00Z",
                "2026-08-19T13:47:02Z",
            ))
            ticker = url.rsplit("/", 1)[-1].replace("%5E", "^")
            return Response({"chart": {"error": None, "result": [{
                "meta": {
                    "symbol": ticker, "dataGranularity": "15m",
                    "exchangeTimezoneName": "America/Chicago",
                },
                "timestamp": [int(value.timestamp()) for value in starts],
                "indicators": {"quote": [{
                    "open": [100.0, 101.0, 102.0],
                    "high": [101.0, 102.0, 103.0],
                    "low": [99.0, 100.0, 101.0],
                    "close": [100.5, 101.5, 102.5],
                    "volume": [100, 110, 120],
                }]},
            }]}})

    frame = fetch_market_15m(
        "^TNX",
        start=datetime(2026, 8, 19, 13, 20, tzinfo=timezone.utc),
        end=datetime(2026, 8, 19, 14, 20, tzinfo=timezone.utc),
        retrieved_at=datetime(2026, 8, 19, 14, 20, tzinfo=timezone.utc),
        session=TreasurySession,
    )

    assert frame["bar_start"].tolist() == list(pd.to_datetime([
        "2026-08-19T13:20:00Z", "2026-08-19T13:35:00Z",
    ]))


def test_yahoo_15m_rejects_an_off_grid_row_before_a_later_native_bar() -> None:
    class NonTrailingOffGridSession(Yahoo15mOffGridSession):
        timestamps = (
            "2026-08-19T13:30:00Z",
            "2026-08-19T13:47:01Z",
            "2026-08-19T14:00:00Z",
        )

        @classmethod
        def get(cls, url, **kwargs):
            response = super().get(url, **kwargs)
            payload = response.json()
            quote = payload["chart"]["result"][0]["indicators"]["quote"][0]
            for column, value in (
                ("open", 102.0), ("high", 103.0), ("low", 101.0),
                ("close", 102.5), ("volume", 120),
            ):
                quote[column].append(value)
            return Response(payload)

    with pytest.raises(ValueError, match="off-grid"):
        fetch_market_15m(
            "^VIX",
            start=datetime(2026, 8, 19, 13, 30, tzinfo=timezone.utc),
            end=datetime(2026, 8, 19, 14, 30, tzinfo=timezone.utc),
            retrieved_at=datetime(2026, 8, 19, 14, 30, tzinfo=timezone.utc),
            session=NonTrailingOffGridSession,
        )


def test_yahoo_15m_rejects_unregistered_symbol_before_network() -> None:
    Yahoo15mSession.calls.clear()
    with pytest.raises(ValueError, match="unregistered"):
        fetch_market_15m(
            "SOXX",
            start=datetime(2026, 8, 19, 13, 30, tzinfo=timezone.utc),
            end=datetime(2026, 8, 19, 14, 15, tzinfo=timezone.utc),
            session=Yahoo15mSession,
        )
    assert Yahoo15mSession.calls == []


class FredSession:
    @staticmethod
    def get(*args,**kwargs):
        return Response(text="DATE,DGS2\n2026-08-03,3.0\n2026-08-04,.\n")


def test_fred_preserves_missing_observation() -> None:
    frame=fetch_series("DGS2",session=FredSession)
    assert frame.dgs2.iloc[0]==3.0 and pd.isna(frame.dgs2.iloc[1])


def test_yahoo_capture_retains_exact_body_and_call_record(tmp_path) -> None:
    fetch_global_index(
        "SP500", date(2026,8,1), date(2026,8,7), session=YahooSession,
        capture_root=tmp_path,
    )
    call_root = next((tmp_path / "yahoo" / "chart").iterdir())
    record = json.loads((call_root / "call.json").read_text(encoding="utf-8"))
    body = (call_root / "response.body").read_bytes()
    assert record["response_body_sha256"] == hashlib.sha256(body).hexdigest()
    assert record["request_parameters"]["symbol"] == "SP500"
    assert "?" not in record["request_url"]


def test_fred_capture_happens_before_parser_failure(tmp_path) -> None:
    class InvalidFred:
        @staticmethod
        def get(*args, **kwargs):
            return Response(text="not,the,documented,schema\n")

    with pytest.raises(RuntimeError, match="schema"):
        fetch_series("DGS2", session=InvalidFred, capture_root=tmp_path)
    call_root = next((tmp_path / "fred" / "fredgraph_csv").iterdir())
    assert (call_root / "response.body").read_bytes() == b"not,the,documented,schema\n"


def test_public_capture_refuses_credential_named_parameters(tmp_path) -> None:
    from stock_data.providers.public_http_capture import capture_public_response
    with pytest.raises(PublicHttpCaptureError, match="sensitive"):
        capture_public_response(
            root=tmp_path, provider="fred", operation="fredgraph_csv",
            request_url="https://example.test/data", request_parameters={"api_key":"secret"},
            response=Response(text="ok"),
        )
