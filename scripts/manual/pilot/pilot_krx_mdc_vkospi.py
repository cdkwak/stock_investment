from __future__ import annotations
import argparse,hashlib,importlib.metadata,io,json,sys,time
from contextlib import redirect_stderr,redirect_stdout
from datetime import datetime,timezone
from pathlib import Path
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from scripts.manual.pilot.pykrx_etf_pilot_support import AppendOnlyLedger,PilotStopped,shared_d_owned_krx_lock,write_bytes_atomic_new,write_json_atomic
from scripts.manual.pilot.pilot_pykrx_etf import _load_credentials
from stock_data.providers.krx_mdc.vkospi import BUSINESS_URL,OFFICIAL_CODE,parse_pilot_body,request_payload

LANDING_ROOT=ROOT/"data/landing/diagnostics/krx_mdc_vkospi_pilot"
LOCK_PATH=ROOT/"data/state/d_owned_krx_short_selling.lock"
EXPECTED={"2026-08-12":"56.48","2026-08-13":"55.28","2026-08-14":"55.31"}

def run_pilot(*,landing_root=LANDING_ROOT,lock_path=LOCK_PATH,env_file=ROOT/".env"):
    run_id=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"_"+uuid4().hex
    run_dir=landing_root/run_id; run_dir.mkdir(parents=True,exist_ok=False)
    ledger=AppendOnlyLedger(run_dir/"call_ledger.jsonl",secrets=())
    manifest={"run_id":run_id,"identity":"코스피 200 변동성지수","official_code":OFFICIAL_CODE,"screen":"KRX [11012]","business_request_limit":1,"retry_count":0,"parallelism":1,"normalized_writes":False,"historical_bulk":False,"request":request_payload("20260812","20260814")}
    write_json_atomic(run_dir/"manifest.json",manifest)
    with shared_d_owned_krx_lock(lock_path,run_id=run_id):
        krx_id,krx_pw=_load_credentials(env_file)
        if not krx_id or not krx_pw: raise PilotStopped("KRX credentials are not configured")
        with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            from pykrx.website.comm import get_session
            session=get_session()
        if session is None or not getattr(session,"is_authenticated",False) or not session.is_valid(): raise PilotStopped("AUTHENTICATION_FAILED")
        started=time.monotonic()
        response=session.post(BUSINESS_URL,data=manifest["request"],timeout=20)
        body=response.content; body_path=run_dir/"response.json"; write_bytes_atomic_new(body_path,body)
        ledger.append("HTTP_RESPONSE",business_sequence=1,status_code=response.status_code,response_bytes=len(body),response_sha256=hashlib.sha256(body).hexdigest(),elapsed_ms=round((time.monotonic()-started)*1000,3))
        if response.status_code in {403,429}: raise PilotStopped(f"HTTP_RESTRICTION:{response.status_code}")
        if response.status_code != 200: raise PilotStopped(f"HTTP_STATUS:{response.status_code}")
        result=parse_pilot_body(body,expected_closes=EXPECTED)
    dates=sorted(row["date"] for row in result.rows)
    checkpoint={"status":"PILOT_PASS","business_calls":1,"retry_count":0,"rows":len(result.rows),"coverage":[dates[0],dates[-1]],"exact_close_matches":3,"source_fields":result.source_fields,"source_current_datetime":result.current_datetime,"response_sha256":hashlib.sha256(body).hexdigest(),"normalized_written":False}
    write_json_atomic(run_dir/"checkpoint.json",checkpoint)
    return {"run_dir":str(run_dir),**checkpoint}

def main():
    p=argparse.ArgumentParser(); p.add_argument("--confirm-live-manual-pilot",action="store_true"); a=p.parse_args()
    if not a.confirm_live_manual_pilot: raise SystemExit("explicit confirmation flag required")
    print(json.dumps(run_pilot(),ensure_ascii=False,default=str)); return 0
if __name__=="__main__": raise SystemExit(main())
