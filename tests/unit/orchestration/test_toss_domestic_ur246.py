from __future__ import annotations
from datetime import datetime
from stock_data.orchestration.toss_domestic_ur246 import MANIFEST_ROOT, runner

class Transport:
    def __init__(self, stock_stamp="2026-08-24T08:59:00+09:00", index_stamp="2026-08-24T09:00:00+09:00"): self.oauth_calls=0; self.business_calls=0; self.calls=[]; self.stock_stamp=stock_stamp; self.index_stamp=index_stamp
    def _payload(self, symbol, stamp): return {"result":[{"symbol":symbol,"timestamp":stamp,"lastPrice":"100000" if symbol.isdigit() else "3000.5", **({"currency":"KRW"} if symbol.isdigit() else {})}]}
    def stock(self,symbol): self.calls.append(symbol); self.business_calls+=1; self.oauth_calls=1; return self._payload(symbol,self.stock_stamp)
    def index(self,symbol): self.calls.append(symbol); self.business_calls+=1; return self._payload(symbol,self.index_stamp)

def at(text): return datetime.fromisoformat(text)

def execute(root, *, now, transport_factory, response_clock=None):
    return runner(root).run(
        now=now,
        transport_factory=transport_factory,
        response_clock=response_clock or (lambda: now),
    )

def test_calendar_and_off_window_precede_factory_and_manifest(tmp_path):
    called=[]
    result=execute(tmp_path,now=at("2026-08-17T09:00:00+09:00"),transport_factory=lambda: called.append(1) or Transport())
    assert result.oauth_calls==result.business_calls==0 and called==[] and not (tmp_path/MANIFEST_ROOT).exists()
    result=execute(tmp_path,now=at("2026-08-24T07:30:00+09:00"),transport_factory=lambda: called.append(1) or Transport())
    assert result.oauth_calls==result.business_calls==0 and called==[]

def test_regular_index_window_is_serial_four_call_global_cap_and_no_repeat(tmp_path):
    transport=Transport(); result=execute(tmp_path,now=at("2026-08-24T09:00:00+09:00"),transport_factory=lambda:transport)
    again=execute(tmp_path,now=at("2026-08-24T09:10:00+09:00"),transport_factory=lambda: (_ for _ in ()).throw(AssertionError("no repeat")))
    assert transport.calls==["000660","005930","KOSPI","KOSDAQ"] and result.oauth_calls==1 and result.business_calls==4
    assert all(v=="COMPLETE" for v in result.statuses.values()) and again.oauth_calls==again.business_calls==0

def test_null_index_provider_timestamp_is_retained_as_retrieval_time_display_only(tmp_path):
    transport=Transport(index_stamp=None)
    result=execute(tmp_path,now=at("2026-08-24T09:00:00+09:00"),transport_factory=lambda:transport)

    assert result.statuses["KOSPI"]==result.statuses["KOSDAQ"]=="COMPLETE"
    for symbol in ("kospi", "kosdaq"):
        payload=(tmp_path/f"data/state/current_observations/toss_{symbol}_ur246.json").read_text(encoding="utf-8")
        assert '"timestamp_basis": "RETRIEVAL_TIMESTAMP"' in payload
        assert '"pit_safe": false' in payload

def test_post_index_window_keeps_indices_api_zero_and_final_close_is_not_live(tmp_path):
    transport=Transport(stock_stamp="2026-08-24T15:29:00+09:00"); result=execute(tmp_path,now=at("2026-08-24T15:30:00+09:00"),transport_factory=lambda:transport)
    assert transport.calls==["000660","005930"] and result.business_calls==2 and set(result.statuses)=={"000660","005930"}
    final=Transport()
    final.stock=lambda symbol: (final.calls.append(symbol) or setattr(final,"business_calls",final.business_calls+1) or setattr(final,"oauth_calls",1) or final._payload(symbol,"2026-08-24T19:59:59+09:00"))
    closed=execute(tmp_path,now=at("2026-08-24T20:00:00+09:00"),transport_factory=lambda:final)
    assert closed.business_calls==2 and all(v=="COMPLETE" for v in closed.statuses.values())

def test_each_response_uses_post_transport_clock_instead_of_run_start(tmp_path):
    transport=Transport(
        stock_stamp="2026-08-24T09:30:04+09:00",
        index_stamp="2026-08-24T09:30:05+09:00",
    )
    instants=iter([
        at("2026-08-24T09:30:06+09:00"),
        at("2026-08-24T09:30:07+09:00"),
        at("2026-08-24T09:30:08+09:00"),
        at("2026-08-24T09:30:09+09:00"),
    ])

    result=execute(
        tmp_path,
        now=at("2026-08-24T09:30:02+09:00"),
        transport_factory=lambda:transport,
        response_clock=lambda:next(instants),
    )

    assert transport.calls==["000660","005930","KOSPI","KOSDAQ"]
    assert set(result.statuses.values())=={"COMPLETE"}
    assert result.oauth_calls==1 and result.business_calls==4
