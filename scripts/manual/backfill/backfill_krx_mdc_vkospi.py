from __future__ import annotations
import argparse,hashlib,io,json,os,shutil,sys,time
from contextlib import redirect_stderr,redirect_stdout
from datetime import datetime,timezone
from pathlib import Path
from uuid import uuid4
ROOT=Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from scripts.manual.pilot.pilot_pykrx_etf import _load_credentials
from scripts.manual.pilot.pykrx_etf_pilot_support import AppendOnlyLedger,PilotStopped,shared_d_owned_krx_lock,write_bytes_atomic_new,write_json_atomic
from stock_data.contracts.vkospi_daily import KR_VKOSPI_DAILY,KR_VKOSPI_RAW_DAILY
from stock_data.providers.krx_mdc.vkospi import BUSINESS_URL,parse_history_body,frames_from_history,request_payload
from stock_data.storage.contract_parquet import read_dataset,write_dataset_atomic
from stock_data.validation.vkospi_daily import validate_vkospi_daily,validate_vkospi_raw_daily

LANDING_ROOT=ROOT/"data/landing/krx/vkospi_daily_raw"; LOCK_PATH=ROOT/"data/state/d_owned_krx_short_selling.lock"; STATE=ROOT/"data/state/kr_vkospi_daily.json"
RAW_ROOT=ROOT/"data/raw/kr_vkospi_daily"; NORMALIZED_ROOT=ROOT/"data/normalized/kr_vkospi_daily"

def discover(start="19900101",end="20260814",env_file=ROOT/".env"):
    run_id=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"_"+uuid4().hex; run=LANDING_ROOT/run_id; run.mkdir(parents=True,exist_ok=False)
    manifest={"run_id":run_id,"mode":"BOUNDED_COVERAGE_DISCOVERY_RAW_CANDIDATE","expected_business_calls":1,"retry_count":0,"normalized_writes":False,"request":request_payload(start,end)}; write_json_atomic(run/"manifest.json",manifest)
    ledger=AppendOnlyLedger(run/"call_ledger.jsonl",secrets=())
    with shared_d_owned_krx_lock(LOCK_PATH,run_id=run_id):
        krx_id,krx_pw=_load_credentials(env_file)
        if not krx_id or not krx_pw: raise PilotStopped("KRX credentials absent")
        with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            from pykrx.website.comm import get_session
            session=get_session()
        if session is None or not session.is_valid(): raise PilotStopped("AUTHENTICATION_FAILED")
        started=time.monotonic(); response=session.post(BUSINESS_URL,data=manifest["request"],timeout=30); body=response.content
        body_path=run/"response.json"; write_bytes_atomic_new(body_path,body); sha=hashlib.sha256(body).hexdigest()
        ledger.append("HTTP_RESPONSE",business_calls=1,status_code=response.status_code,response_bytes=len(body),response_sha256=sha,elapsed_ms=round((time.monotonic()-started)*1000,3))
        if response.status_code in {403,429}: raise PilotStopped(f"HTTP_RESTRICTION:{response.status_code}")
        if response.status_code!=200: raise PilotStopped(f"HTTP_STATUS:{response.status_code}")
        rows,current,fields=parse_history_body(body)
    dates=sorted(datetime.strptime(str(r["TRD_DD"]),"%Y/%m/%d").strftime("%Y-%m-%d") for r in rows)
    checkpoint={"status":"DISCOVERY_COMPLETE_RAW_CANDIDATE","business_calls":1,"retry_count":0,"requested":[start,end],"rows":len(rows),"coverage":[dates[0],dates[-1]],"source_fields":fields,"source_current_datetime":current,"response_sha256":sha,"landing_reference":str(body_path.relative_to(ROOT)).replace('\\','/'),"normalized_written":False}
    write_json_atomic(run/"checkpoint.json",checkpoint); return {"run_dir":str(run),**checkpoint}

def boundary_discover(env_file=ROOT/".env",seed_response:Path|None=None):
    run_id=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"_boundary_"+uuid4().hex; run=LANDING_ROOT/run_id; run.mkdir(parents=True,exist_ok=False)
    if seed_response is None: raise PilotStopped("RETAINED_2008_SEED_REQUIRED_TO_AVOID_RECOLLECTION")
    seed_rows,_,_=parse_history_body(seed_response.read_bytes())
    manifest={"run_id":run_id,"mode":"BOUNDED_ANNUAL_BINARY_BOUNDARY_DISCOVERY","maximum_business_calls":5,"retry_count":0,"normalized_writes":False,"retained_nonempty_seed_year":2008,"retained_seed":str(seed_response.relative_to(ROOT)).replace('\\','/'),"lower_search_year":1990}; write_json_atomic(run/"manifest.json",manifest)
    ledger=AppendOnlyLedger(run/"call_ledger.jsonl",secrets=()); completed=[]
    with shared_d_owned_krx_lock(LOCK_PATH,run_id=run_id):
        krx_id,krx_pw=_load_credentials(env_file)
        if not krx_id or not krx_pw: raise PilotStopped("KRX credentials absent")
        with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            from pykrx.website.comm import get_session
            session=get_session()
        if session is None or not session.is_valid(): raise PilotStopped("AUTHENTICATION_FAILED")
        lo,hi=1990,2008
        while lo<hi:
            if len(completed)>=5: raise PilotStopped("BOUNDARY_CALL_BUDGET_EXHAUSTED")
            year=(lo+hi)//2; params=request_payload(f"{year}0101",f"{year}1231"); started=time.monotonic(); response=session.post(BUSINESS_URL,data=params,timeout=30); body=response.content
            path=run/f"response_{len(completed)+1:02d}_{year}.json"; write_bytes_atomic_new(path,body); sha=hashlib.sha256(body).hexdigest()
            ledger.append("HTTP_RESPONSE",year=year,business_sequence=len(completed)+1,status_code=response.status_code,response_bytes=len(body),response_sha256=sha,elapsed_ms=round((time.monotonic()-started)*1000,3))
            if response.status_code in {403,429}: raise PilotStopped(f"HTTP_RESTRICTION:{response.status_code}")
            if response.status_code!=200: raise PilotStopped(f"HTTP_STATUS:{response.status_code}:{year}")
            if body.lstrip().startswith(b"<"): raise PilotStopped("HTML_OR_BLOCK_PAGE")
            payload=json.loads(body); rows=payload.get("output")
            if not isinstance(rows,list): raise PilotStopped("SCHEMA_OUTPUT_MISSING")
            if rows: parse_history_body(body); hi=year; classification="NONEMPTY"
            else: lo=year+1; classification="VALID_EMPTY"
            completed.append({"year":year,"classification":classification,"rows":len(rows),"body_file":path.name,"response_sha256":sha})
    first_year=lo; match=next((x for x in completed if x["year"]==first_year and x["classification"]=="NONEMPTY"),None)
    if match is None:
        params=request_payload(f"{first_year}0101",f"{first_year}1231")
        if first_year==2008:
            match={"year":2008,"classification":"NONEMPTY","rows":len(seed_rows),"body_file":str(seed_response),"response_sha256":hashlib.sha256(seed_response.read_bytes()).hexdigest()}
            rows=seed_rows
        elif len(completed)>=5: raise PilotStopped("BOUNDARY_RESULT_NOT_RETAINED")
        else:
            raise PilotStopped("BOUNDARY_RESULT_NOT_RETAINED")
    if first_year!=2008: rows,_,fields=parse_history_body((run/match["body_file"]).read_bytes())
    else: fields=tuple(sorted(seed_rows[0]))
    earliest=min(datetime.strptime(str(r["TRD_DD"]),"%Y/%m/%d").strftime("%Y-%m-%d") for r in rows)
    checkpoint={"status":"BOUNDARY_DISCOVERY_COMPLETE","earliest_verified_source_date":earliest,"first_nonempty_year":first_year,"business_calls":len(completed),"retry_count":0,"completed":completed,"source_fields":fields,"normalized_written":False}; write_json_atomic(run/"checkpoint.json",checkpoint); return {"run_dir":str(run),**checkpoint}

def historical_backfill(env_file=ROOT/".env"):
    retained={}
    for path in LANDING_ROOT.rglob("response*.json"):
        try:
            payload=json.loads(path.read_text(encoding="utf-8")); rows=payload.get("output")
            if not rows: continue
            years={int(str(row["TRD_DD"])[:4]) for row in rows}
            if len(years)==1 and len(rows)>100: retained.setdefault(years.pop(),path)
        except Exception: continue
    required=list(range(2003,2027)); missing=[year for year in required if year not in retained]
    run_id=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"_historical_"+uuid4().hex; run=LANDING_ROOT/run_id; run.mkdir(parents=True,exist_ok=False)
    manifest={"run_id":run_id,"mode":"HISTORICAL_RAW_BACKFILL","coverage_years":required,"reused_years":sorted(set(required)&set(retained)),"planned_call_years":missing,"expected_business_calls":len(missing),"retry_count":0,"min_interval_seconds":8,"normalized_writes":False}; write_json_atomic(run/"manifest.json",manifest)
    ledger=AppendOnlyLedger(run/"call_ledger.jsonl",secrets=()); completed=[]
    with shared_d_owned_krx_lock(LOCK_PATH,run_id=run_id):
        krx_id,krx_pw=_load_credentials(env_file)
        if not krx_id or not krx_pw: raise PilotStopped("KRX credentials absent")
        with redirect_stdout(io.StringIO()),redirect_stderr(io.StringIO()):
            from pykrx.website.comm import get_session
            session=get_session()
        if session is None or not session.is_valid(): raise PilotStopped("AUTHENTICATION_FAILED")
        for sequence,year in enumerate(missing,1):
            if sequence>1: time.sleep(8)
            params=request_payload(f"{year}0101",f"{year}1231" if year<2026 else "20260814"); started=time.monotonic(); response=session.post(BUSINESS_URL,data=params,timeout=30); body=response.content
            path=run/f"response_{sequence:02d}_{year}.json"; write_bytes_atomic_new(path,body); sha=hashlib.sha256(body).hexdigest(); ledger.append("HTTP_RESPONSE",year=year,business_sequence=sequence,status_code=response.status_code,response_bytes=len(body),response_sha256=sha,elapsed_ms=round((time.monotonic()-started)*1000,3))
            if response.status_code in {403,429}: raise PilotStopped(f"HTTP_RESTRICTION:{response.status_code}:{year}")
            if response.status_code!=200: raise PilotStopped(f"HTTP_STATUS:{response.status_code}:{year}")
            rows,_,_=parse_history_body(body); observed={int(str(row["TRD_DD"])[:4]) for row in rows}
            if observed!={year}: raise PilotStopped(f"YEAR_SCOPE_MISMATCH:{year}:{observed}")
            retained[year]=path; completed.append({"year":year,"rows":len(rows),"response_sha256":sha,"path":str(path.relative_to(ROOT)).replace('\\','/')})
    references=[]; all_dates=[]; total_rows=0
    for year in required:
        path=retained[year]; body=path.read_bytes(); rows,_,_=parse_history_body(body); dates=[datetime.strptime(str(r["TRD_DD"]),"%Y/%m/%d").strftime("%Y-%m-%d") for r in rows]
        references.append({"year":year,"path":str(path.relative_to(ROOT)).replace('\\','/'),"rows":len(rows),"response_sha256":hashlib.sha256(body).hexdigest()}); all_dates.extend(dates); total_rows+=len(rows)
    if len(all_dates)!=len(set(all_dates)): raise PilotStopped("DUPLICATE_DATE_ACROSS_CHUNKS")
    checkpoint={"status":"RAW_BACKFILL_COMPLETE_PROMOTION_READY","coverage":[min(all_dates),max(all_dates)],"rows":total_rows,"business_calls":len(missing),"retry_count":0,"response_files":references,"new_calls":completed,"normalized_written":False}; write_json_atomic(run/"checkpoint.json",checkpoint); return {"run_dir":str(run),**checkpoint}

def promote(run:Path):
    checkpoint=json.loads((run/"checkpoint.json").read_text(encoding="utf-8")); body=(run/"response.json").read_bytes()
    if hashlib.sha256(body).hexdigest()!=checkpoint["response_sha256"]: raise PilotStopped("LANDING_HASH_MISMATCH")
    rows,_,_=parse_history_body(body); collected_at=datetime.fromtimestamp((run/"response.json").stat().st_mtime,timezone.utc).isoformat()
    raw,normalized=frames_from_history(rows,collected_at=collected_at,landing_reference=checkpoint["landing_reference"],response_sha256=checkpoint["response_sha256"])
    raw=raw[list(KR_VKOSPI_RAW_DAILY.column_names)]; normalized=normalized[list(KR_VKOSPI_DAILY.column_names)]
    validate_vkospi_raw_daily(raw); validate_vkospi_daily(normalized)
    if RAW_ROOT.exists() or NORMALIZED_ROOT.exists(): raise PilotStopped("TARGET_ALREADY_EXISTS")
    stage=ROOT/"data/staging/vkospi_promotion"/run.name
    raw_stage=stage/"raw"; norm_stage=stage/"normalized"; write_dataset_atomic(raw,raw_stage,KR_VKOSPI_RAW_DAILY,validate_vkospi_raw_daily); write_dataset_atomic(normalized,norm_stage,KR_VKOSPI_DAILY,validate_vkospi_daily)
    RAW_ROOT.parent.mkdir(parents=True,exist_ok=True); NORMALIZED_ROOT.parent.mkdir(parents=True,exist_ok=True)
    raw_stage.replace(RAW_ROOT)
    try: norm_stage.replace(NORMALIZED_ROOT)
    except Exception: RAW_ROOT.replace(raw_stage); raise
    state={"status":"HISTORICAL_RAW_AND_NORMALIZED_COMPLETE_PIT_LIMITED","coverage":[normalized.market_date.min(),normalized.market_date.max()],"rows":len(normalized),"business_calls":checkpoint["business_calls"],"retry_count":0,"landing_reference":checkpoint["landing_reference"],"response_sha256":checkpoint["response_sha256"],"last_accepted_market_date":normalized.market_date.max(),"publication_revision_status":"UNRESOLVED"}; write_json_atomic(STATE,state)
    return state

def promote_history(run:Path):
    checkpoint=json.loads((run/"checkpoint.json").read_text(encoding="utf-8")); rows=[]; hashes=[]
    for item in checkpoint["response_files"]:
        path=ROOT/item["path"]; body=path.read_bytes(); sha=hashlib.sha256(body).hexdigest()
        if sha!=item["response_sha256"]: raise PilotStopped("LANDING_HASH_MISMATCH")
        chunk,_,_=parse_history_body(body); rows.extend(chunk); hashes.append(sha)
    combined_hash=hashlib.sha256("".join(hashes).encode()).hexdigest(); collected_at=datetime.now(timezone.utc).isoformat(); landing_reference=str((run/"checkpoint.json").relative_to(ROOT)).replace('\\','/')
    raw,normalized=frames_from_history(rows,collected_at=collected_at,landing_reference=landing_reference,response_sha256=combined_hash); validate_vkospi_raw_daily(raw); validate_vkospi_daily(normalized)
    if RAW_ROOT.exists() or NORMALIZED_ROOT.exists(): raise PilotStopped("TARGET_ALREADY_EXISTS")
    stage=ROOT/"data/staging/vkospi_promotion"/run.name; raw_stage=stage/"raw"; norm_stage=stage/"normalized"; write_dataset_atomic(raw,raw_stage,KR_VKOSPI_RAW_DAILY,validate_vkospi_raw_daily); write_dataset_atomic(normalized,norm_stage,KR_VKOSPI_DAILY,validate_vkospi_daily)
    RAW_ROOT.parent.mkdir(parents=True,exist_ok=True); NORMALIZED_ROOT.parent.mkdir(parents=True,exist_ok=True); raw_stage.replace(RAW_ROOT)
    try: norm_stage.replace(NORMALIZED_ROOT)
    except Exception: RAW_ROOT.replace(raw_stage); raise
    state={"status":"HISTORICAL_RAW_AND_NORMALIZED_COMPLETE_PIT_LIMITED","coverage":[normalized.market_date.min(),normalized.market_date.max()],"rows":len(normalized),"business_calls":checkpoint["business_calls"],"retry_count":0,"landing_reference":landing_reference,"response_sha256":combined_hash,"last_accepted_market_date":normalized.market_date.max(),"publication_revision_status":"UNRESOLVED"}; write_json_atomic(STATE,state); return state

def main():
    p=argparse.ArgumentParser(); p.add_argument("--discover",action="store_true"); p.add_argument("--boundary-discover",action="store_true"); p.add_argument("--boundary-seed-response",type=Path); p.add_argument("--historical-backfill",action="store_true"); p.add_argument("--promote-run",type=Path); p.add_argument("--promote-history-run",type=Path); p.add_argument("--confirm-live",action="store_true"); a=p.parse_args()
    if a.discover:
        if not a.confirm_live: raise SystemExit("--confirm-live required")
        result=discover()
    elif a.boundary_discover:
        if not a.confirm_live: raise SystemExit("--confirm-live required")
        result=boundary_discover(seed_response=a.boundary_seed_response)
    elif a.promote_run: result=promote(a.promote_run)
    elif a.historical_backfill:
        if not a.confirm_live: raise SystemExit("--confirm-live required")
        result=historical_backfill()
    elif a.promote_history_run: result=promote_history(a.promote_history_run)
    else: raise SystemExit("choose --discover or --promote-run")
    print(json.dumps(result,ensure_ascii=False,default=str)); return 0
if __name__=="__main__": raise SystemExit(main())
