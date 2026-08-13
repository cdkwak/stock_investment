import hashlib,json
from pathlib import Path
import pytest
from scripts.manual import a007_investor_h4_boundary_diagnostic_support as s
from scripts.manual import diagnose_a007_investor_h4_boundary as runner
from scripts.manual.pykrx_short_selling_pilot_support import PilotStopped
D=("20170519","20170522")
def row(d,t=10): return {"TRD_DD":f"{d[:4]}/{d[4:6]}/{d[6:]}","STR_CONST_VAL1":str(t),"STR_CONST_VAL2":"0","STR_CONST_VAL3":"0","STR_CONST_VAL4":"0","STR_CONST_VAL5":str(t)}
def body(ds=D,t=10): return json.dumps({"OutBlock_1":[row(d,t) for d in ds]}).encode()
def test_plan_exact_and_retained_dates():
    assert s.SCOPE=={"strtDd":"20170519","endDd":"20170522","inqCondTpCd":1,"mktTpCd":1}
    assert s.MAX_BUSINESS_REQUESTS==1 and s.MAX_RAW_HTTP_REQUESTS==s.EXPECTED_RAW_HTTP_REQUESTS==6 and s.REQUIRE_ZERO_RETRY_AUTH_SESSION
    dates=s.expected_dates(Path('.')); assert dates==D
    assert hashlib.sha256(("\n".join(dates)+"\n").encode()).hexdigest()=="a8e1c5b7be734fb70104c2a93405a36610ccd9dbef05e85cb3bf55789ececfd1"
    assert runner.D_OWNED_LOCK_PATH.name=="d_owned_krx_short_selling.lock"
def test_exact_classifications_only():
    assert s.classify_response(body(),D).classification=="RANGE_WINDOW_EFFECT"
    assert s.classify_response(body(("20170522",)),D).classification=="BOUNDARY_SHAPED_CONFIRMED"
    for b in (body(("20170519",)),body(("20170522",),0),body(D,0)):
        with pytest.raises(PilotStopped,match="AMBIGUOUS_STOP"): s.classify_response(b,D)
@pytest.mark.parametrize("b,reason",[(b"<html>x</html>","HTML_OR_RESTRICTION"),(b'{"OutBlock_1":[]}',"ANOMALOUS_EMPTY"),(json.dumps({"OutBlock_1":[{**row("20170522"),"x":1}]}).encode(),"SCHEMA_MISMATCH"),(json.dumps({"OutBlock_1":[{**row("20170522"),"STR_CONST_VAL1":"-1"}]}).encode(),"NEGATIVE_VALUE")])
def test_strict_gates(b,reason):
    with pytest.raises(PilotStopped,match=reason): s.classify_response(b,D)
def test_cli_guard_prevents_network(monkeypatch):
    called=False
    def no(**kwargs):
        nonlocal called; called=True
    monkeypatch.setattr(runner,"run_diagnostic",no); monkeypatch.setattr("sys.argv",["x"])
    assert runner.main()==2 and not called
