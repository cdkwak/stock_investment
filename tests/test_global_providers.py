from datetime import date

import pandas as pd
import pytest

from stock_data.providers.yahoo import fetch_global_index
from stock_data.providers.fred import fetch_series


class Response:
    def __init__(self,payload=None,text=""):
        self._payload=payload; self.text=text
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
