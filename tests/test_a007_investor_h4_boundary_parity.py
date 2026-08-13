import json
from pathlib import Path
import pytest
import requests
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

class Auth:
 def __init__(self,session): self.session=session; self.is_authenticated=True
 def is_valid(self): return True
def response(content):
 r=requests.Response(); r.status_code=200; r._content=content; r.headers['Content-Type']='application/json'; return r
def execute_run(tmp_path,monkeypatch,bodies):
 project=tmp_path/'project'; project.mkdir(); env=project/'.env'; env.write_text('KRX_ID=user\nKRX_PW=password\n')
 monkeypatch.delenv('KRX_ID',raising=False); monkeypatch.delenv('KRX_PW',raising=False)
 monkeypatch.setattr(runner.base.importlib.metadata,'version',lambda unused:'1.2.8'); monkeypatch.setattr(s,'expected_dates',lambda unused:D)
 session=requests.Session(); queue=list(bodies); calls=[]
 def fake(current,method,url,**kwargs):
  calls.append((method,url,kwargs)); return response(b'{}' if 'getJsonData' not in url else queue.pop(0))
 monkeypatch.setattr(requests.Session,'request',fake)
 def authenticate():
  for _ in range(5): session.get('https://data.krx.co.kr/contents/MDC/COMS/client/MDCCOMS001.cmd')
  return Auth(session)
 result=runner.run(env_file=env,project_root=project,session_getter=authenticate)
 return project,result,calls
def test_mocked_healthy_mixed_run_has_three_landing_provenance_pairs(tmp_path,monkeypatch):
 project,result,calls=execute_run(tmp_path,monkeypatch,[body(),body(('20170522',)),body(('20170522',))])
 assert result['classification']=='METRIC_OR_MARKET_SPECIFIC_WINDOW_EFFECT'
 assert result['business_requests']==3 and result['raw_http_requests']==8
 rd=Path(result['run_dir'])
 for i in range(1,4):
  assert (rd/f'response_{i:02d}.json').is_file() and (rd/f'response_{i:02d}.json.provenance.json').is_file()
 assert sum('getJsonData' in url for _,url,_ in calls)==3
@pytest.mark.parametrize('bodies,expected_calls',[
 ([body(('20170519',)),body(('20170522',)),body(('20170522',))],1),
 ([body(('20170522',)),body(('20170519',)),body(('20170522',))],2),])
def test_anomaly_stops_before_next_post(tmp_path,monkeypatch,bodies,expected_calls):
 with pytest.raises(PilotStopped): execute_run(tmp_path,monkeypatch,bodies)
 rd=next((tmp_path/'project/data/landing/diagnostics/a007_investor_h4_boundary_parity').iterdir())
 ledger=[json.loads(x) for x in (rd/'call_ledger.jsonl').read_text().splitlines()]
 business=[x for x in ledger if x.get('event')=='HTTP_RESPONSE' and x.get('authentication') is False]
 assert len(business)==expected_calls
 assert [x['raw_sequence'] for x in business]==list(range(6,6+expected_calls))
