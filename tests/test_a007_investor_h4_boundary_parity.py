import json
from pathlib import Path
import pytest
from scripts.manual import a007_investor_h4_boundary_parity_support as s
from scripts.manual import diagnose_a007_investor_h4_boundary_parity as runner
from scripts.manual.pykrx_short_selling_pilot_support import PilotStopped
D=("20170519","20170522")
def body(ds=D):
 def r(d): return {"TRD_DD":f"{d[:4]}/{d[4:6]}/{d[6:]}","STR_CONST_VAL1":"10","STR_CONST_VAL2":"0","STR_CONST_VAL3":"0","STR_CONST_VAL4":"0","STR_CONST_VAL5":"10"}
 return json.dumps({"OutBlock_1":[r(d) for d in ds]}).encode()
def test_plan_and_shared_market_dates():
 assert s.expected_dates(Path('.'))==D
 assert s.MAX_BUSINESS_REQUESTS==3 and s.MAX_RAW_HTTP_REQUESTS==s.EXPECTED_RAW_HTTP_REQUESTS==8
 assert [(x['inqCondTpCd'],x['mktTpCd']) for x in s.SCOPES]==[(2,1),(1,2),(2,2)]
def test_aggregate_classifications():
 assert s.classify_responses([body(("20170522",))]*3,D)[0]=="SHARED_BOUNDARY_SHAPED_CONFIRMED"
 assert s.classify_responses([body(),body(("20170522",)),body(("20170522",))],D)[0]=="METRIC_OR_MARKET_SPECIFIC_WINDOW_EFFECT"
 with pytest.raises(PilotStopped): s.classify_responses([body(("20170519",)),body(("20170522",)),body(("20170522",))],D)
def test_cli_refuses_before_live(monkeypatch):
 called=False
 def no():
  nonlocal called; called=True
 monkeypatch.setattr(runner,'run',no); monkeypatch.setattr('sys.argv',['x'])
 assert runner.main()==2 and not called
