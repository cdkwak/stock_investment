"""Three sequential calls in one authenticated session; Landing-only."""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from scripts.manual.diagnostic import a007_investor_h4_boundary_parity_support as support
from scripts.manual.diagnostic import diagnose_a007_investor_range as base
from scripts.manual.pilot.pykrx_short_selling_pilot_support import AppendOnlyLedger,PilotStopped,assert_no_credentials,d_owned_run_lock,redact,utc_now
LANDING_ROOT=ROOT/"data/landing/diagnostics/a007_investor_h4_boundary_parity"; LOCK=ROOT/"data/state/d_owned_krx_short_selling.lock"
def run(*,env_file=ROOT/".env",project_root=ROOT,session_getter=None,execute_scope=None):
 project_root=project_root.resolve(); dates=support.expected_dates(project_root); creds=base._load_credentials(env_file)
 if not all(creds): raise PilotStopped("KRX credentials are not configured")
 rid=base._new_run_id(); rd=(project_root/"data/landing/diagnostics/a007_investor_h4_boundary_parity"/rid); rd.mkdir(parents=True); led=AppendOnlyLedger(rd/base.LEDGER_NAME,credential_values=creds); base._atomic_json_new(rd/base.MANIFEST_NAME,support.manifest_payload(run_id=rid,created_at_utc=utc_now(),dates=dates))
 try:
  with d_owned_run_lock((project_root/"data/state/d_owned_krx_short_selling.lock"),run_id=rid):
   with base.HttpCapture(ledger=led,run_dir=rd,project_root=project_root,credential_values=creds,run_id=rid,diagnostic_support=support,expected_dates=dates) as cap:
    auth=(session_getter or base._default_session_getter)()
    if auth is None or not getattr(auth,"is_authenticated",False) or not auth.is_valid(): raise PilotStopped("AUTHENTICATION_FAILED")
    transport=base._authenticated_transport(auth); base._install_verified_zero_retry(transport); cap.authorize_business_session(transport)
    led.append("SCOPE_STARTED",bld=support.BUSINESS_BLD,scope=support.SCOPE_ID,params=list(support.SCOPES),business_request_limit=3)
    parts=[]
    for index,scope in enumerate(support.SCOPES,1):
     if execute_scope: execute_scope(transport,scope)
     else: transport.post("https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd",data={"bld":support.BUSINESS_BLD,**{k:str(v) for k,v in scope.items() if k!="name"}})
     response=cap.take_latest_response(index)
     # Landing capture and ledger append already completed inside HttpCapture.
     # Fail this scope before the next POST.
     parts.append(support.boundary.classify_response(response.content,dates))
    if cap.raw_count!=8: raise PilotStopped(f"RAW_REQUEST_COUNT_MISMATCH:{cap.raw_count}/8")
    classification=support.aggregate_classifications(parts); led.append("DIAGNOSTIC_PASSED",classification=classification,raw_http_requests=8,business_requests=3,parts=[r.classification for r in parts]); result={"status":"PASS","classification":classification,"run_dir":str(rd),"raw_http_requests":8,"business_requests":3}
 except Exception as error: led.append("DIAGNOSTIC_STOPPED",error_type=type(error).__name__,error=redact(str(error),creds)); raise
 for p in rd.rglob('*'):
  if p.is_file(): assert_no_credentials(p.read_bytes(),creds)
 return result
def main():
 p=argparse.ArgumentParser(); p.add_argument('--acknowledge-no-active-krx-stream',action='store_true'); p.add_argument('--confirm-three-live-requests',action='store_true'); p.add_argument('--confirm-landing-only',action='store_true'); p.add_argument('--confirm-scope'); a=p.parse_args()
 if not(a.acknowledge_no_active_krx_stream and a.confirm_three_live_requests and a.confirm_landing_only and a.confirm_scope==support.SCOPE_ID): return 2
 print(json.dumps(run(),ensure_ascii=False)); return 0
if __name__=='__main__': raise SystemExit(main())
