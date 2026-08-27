from datetime import date
import pandas as pd
import pytest
from stock_data.providers.pykrx.vkospi_daily import VKOSPIPilotError,fetch_bounded_pilot
def raw(): return pd.DataFrame([[56.,57.,55.,55.3,0,0,0],[55.,56.,54.,55.2,0,0,0],[55.,56.,55.,55.3,0,0,0]],columns=list("ABCDEFG"),index=pd.to_datetime(["2026-08-12","2026-08-13","2026-08-14"]))
def test_one_call_identity():
    calls=[]
    class S:
        def get_index_ohlcv(self,*a): calls.append(a); return raw()
    f=fetch_bounded_pilot(date(2026,8,12),date(2026,8,14),stock_module=S())
    assert calls==[("20260812","20260814","1300")]; assert f.ticker.eq("1300").all()
def test_no_retry():
    calls=[]
    class S:
        def get_index_ohlcv(self,*a): calls.append(a); raise RuntimeError("blocked")
    with pytest.raises(VKOSPIPilotError,match="retry-zero"): fetch_bounded_pilot(date(2026,8,12),date(2026,8,14),stock_module=S())
    assert len(calls)==1
