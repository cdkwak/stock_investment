"""Durable, transport-injected UR-167 Naver mobile-home collector."""
from __future__ import annotations
import hashlib, json, os, uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Collection, Protocol
from stock_data.orchestration.current_observation import CurrentObservationCoordinator, CurrentObservationFileStore
from stock_data.orchestration.current_observation_supervisor import CurrentObservationProcessLock
from stock_data.orchestration.naver_mobile_home_windows import WINDOW_IDS, window_id
from stock_data.providers.naver_mobile_home_observation import observation_for, parse_rows, route_for

STATE_PATH=Path("data/state/naver_mobile_home_ur167_windows.json")
LANDING_ROOT=Path("data/landing/naver_mobile_home/ur167")
PROJECTION_PATH=Path("data/state/current_observations/naver_mobile_home_current.json")
class HttpResponse(Protocol):
    status_code:int
    content:bytes
@dataclass(frozen=True)
class NaverMobileHomeWindowResult:
    status:str; window_id:str; raw_gets:int; accepted_cids:tuple[str,...]; rejected:dict[str,str]; replay_api_calls:int
def _write(path:Path,payload:dict[str,object])->None:
    path.parent.mkdir(parents=True,exist_ok=True); tmp=path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp.open("xb") as out: out.write(json.dumps(payload,ensure_ascii=False,sort_keys=True,indent=2).encode("utf-8")); out.flush(); os.fsync(out.fileno())
        os.replace(tmp,path)
    finally:
        try: tmp.unlink()
        except FileNotFoundError: pass
def _state(path:Path, operation_id:str)->dict[str,object]:
    try: value=json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError: return {"schema_version":1,"operation_id":operation_id,"windows":{}}
    if not isinstance(value,dict) or set(value)!={"schema_version","operation_id","windows"} or value["schema_version"]!=1 or value["operation_id"]!=operation_id or not isinstance(value["windows"],dict): raise RuntimeError(f"{operation_id} window state invalid")
    return value
class NaverMobileHomeWindowedCollector:
    def __init__(self,root:Path,*,operation_id:str="UR-167",state_path:Path=STATE_PATH,landing_root:Path=LANDING_ROOT,required_status:str="REALTIME",projection_cids:Collection[str]=( "KOSPI","KOSDAQ","FX_USDKRW","GCcv1","CLcv1"),lock:CurrentObservationProcessLock|None=None,window_selector:Callable[[datetime],str]=window_id)->None:
        self.root=Path(root); self.operation_id=operation_id; self.state_path=self.root/state_path; self.landing_root=landing_root; self.required_status=required_status; self.projection_cids=frozenset(projection_cids); self.store=CurrentObservationFileStore(self.root/PROJECTION_PATH); self.lock=lock or CurrentObservationProcessLock(self.state_path.with_suffix(".lock")); self.window_selector=window_selector
    def _replay(self)->int: return sum(CurrentObservationCoordinator(self.store).replay(route_for(cid)).api_calls for cid in ("KOSPI","KOSDAQ","FX_USDKRW","GCcv1","CLcv1"))
    def run(self,*,now:datetime,response_factory:Callable[[],HttpResponse]|None,allowed_window_ids:Collection[str]=WINDOW_IDS)->NaverMobileHomeWindowResult:
        wid=self.window_selector(now=now)
        if wid not in set(allowed_window_ids): return NaverMobileHomeWindowResult("WINDOW_NOT_MANIFESTED",wid,0,(),{},self._replay())
        if not self.lock.acquire(): return NaverMobileHomeWindowResult("PROCESS_LOCKED",wid,0,(),{},self._replay())
        try:
            state=_state(self.state_path,self.operation_id); windows=dict(state["windows"])
            if wid in windows: return NaverMobileHomeWindowResult("NO_REPEAT",wid,0,(),{},self._replay())
            if response_factory is None: raise RuntimeError("response factory is required for a due new window")
            claim:dict[str,object]={"status":"ATTEMPTING","attempted_at_utc":now.astimezone(timezone.utc).isoformat(),"raw_gets_reserved":1,"raw_gets_invoked":0,"raw_gets_completed":0,"retry_count":0,"redirect_count":0,"fallback_count":0}
            windows[wid]=claim; state["windows"]=windows; _write(self.state_path,state)
            try:
                claim["raw_gets_invoked"]=1; windows[wid]=claim; state["windows"]=windows; _write(self.state_path,state)
                response=response_factory(); claim["raw_gets_completed"]=1
                if response.status_code!=200: accepted,rejected=(),{}; claim.update({"status":"COMPLETE_FAILURE","failure_type":"HTTP_STATUS","raw_gets":1})
                else:
                    body=bytes(response.content); digest=hashlib.sha256(body).hexdigest(); landing=self.root/self.landing_root/wid.replace(":","")/digest/"response.html"; landing.parent.mkdir(parents=True,exist_ok=True)
                    with landing.open("xb") as out: out.write(body); out.flush(); os.fsync(out.fileno())
                    readback=landing.read_bytes()
                    if hashlib.sha256(readback).hexdigest()!=digest: raise RuntimeError("Landing hash readback mismatch")
                    rows=parse_rows(readback,recovered_at=now,required_status=self.required_status); accepted_list:list[str]=[]; rejected={}
                    for cid,row in rows.items():
                        if cid not in self.projection_cids: continue
                        if not row.get("accepted"): rejected[cid]=str(row.get("reason")); continue
                        source=observation_for(cid,row,recovered_at=now); result=CurrentObservationCoordinator(self.store).refresh(route_for(cid),primary_attempt=lambda source=source:source,fallback_attempt=lambda:(_ for _ in ()).throw(AssertionError("Naver has no fallback")))
                        if result.observation is not None: accepted_list.append(cid)
                    accepted=tuple(accepted_list); claim.update({"status":"COMPLETE","raw_gets":1,"landing_file":landing.relative_to(self.root).as_posix(),"landing_sha256":digest,"accepted_cids":list(accepted),"rejected":rejected})
            except Exception as error:
                accepted,rejected=(),{}; claim.update({"status":"COMPLETE_FAILURE","failure_type":type(error).__name__,"raw_gets":int(claim["raw_gets_invoked"])})
            windows[wid]=claim; state["windows"]=windows; _write(self.state_path,state)
            return NaverMobileHomeWindowResult(str(claim["status"]),wid,int(claim["raw_gets"]),accepted,rejected,self._replay())
        finally: self.lock.release()
__all__=["NaverMobileHomeWindowResult","NaverMobileHomeWindowedCollector"]
