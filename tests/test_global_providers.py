from datetime import date
import hashlib
import json

import pandas as pd
import pytest

from stock_data.providers.yahoo import fetch_global_index
from stock_data.providers.fred import fetch_series
from stock_data.providers.public_http_capture import PublicHttpCaptureError


class Response:
    def __init__(self,payload=None,text=""):
        self._payload=payload; self.text=text
        self.content=text.encode("utf-8") if text else json.dumps(payload).encode("utf-8")
        self.status_code=200; self.headers={"Content-Type":"application/json"}
    def raise_for_status(self): pass
    def json(self): return self._payload


class YahooSession:
    @staticmethod
    def get(*args,**kwargs):
        return Response({"chart":{"error":None,"result":[{
            "timestamp":[1786032000],
            "indicators":{"quote":[{"open":[100.0],"high":[110.0],"low":[90.0],"close":[105.0],"volume":[1000]}]}
        }]}})


def test_yahoo_arrays_are_normalized() -> None:
    frame=fetch_global_index("SP500",date(2026,8,1),date(2026,8,7),session=YahooSession)
    assert len(frame)==1 and frame.symbol.item()=="SP500"


class BadYahoo(YahooSession):
    @staticmethod
    def get(*args,**kwargs):
        response=YahooSession.get(); response._payload["chart"]["result"][0]["indicators"]["quote"][0]["volume"]=[]
        return response


def test_yahoo_array_mismatch_is_rejected() -> None:
    with pytest.raises(RuntimeError,match="lengths"):
        fetch_global_index("SP500",date(2026,8,1),date(2026,8,7),session=BadYahoo)


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
