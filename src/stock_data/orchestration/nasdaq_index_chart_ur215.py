"""UR-215's durable, independently budgeted official Nasdaq index charts."""
from __future__ import annotations
import hashlib, json, os, uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Protocol

ROUTES={"COMP":{"identity":"NASDAQ_COMPOSITE","url":"https://api.nasdaq.com/api/quote/COMP/chart?assetclass=index"},"SPX":{"identity":"SP500","url":"https://api.nasdaq.com/api/quote/SPX/chart?assetclass=index"}}
PUBLIC_HEADERS={"Accept":"application/json, text/plain, */*","User-Agent":"Mozilla/5.0"}
STATE_PATH=Path("data/state/nasdaq_index_chart_ur215.json")
LANDING_ROOT=Path("data/landing/nasdaq/index_chart_ur215")
class HttpResponse(Protocol): status_code:int; content:bytes
@dataclass(frozen=True)
class ChartCaptureResult: status:str; symbol:str; raw_gets:int; landing_sha256:str|None
def _write(path:Path,payload:dict[str,object])->None:
 path.parent.mkdir(parents=True,exist_ok=True); tmp=path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
 try:
  with tmp.open("xb") as s: s.write(json.dumps(payload,ensure_ascii=False,sort_keys=True,indent=2).encode()); s.flush(); os.fsync(s.fileno())
  os.replace(tmp,path)
 finally:
  try:tmp.unlink()
  except FileNotFoundError:pass
def _state(path:Path)->dict[str,object]:
 try:v=json.loads(path.read_text(encoding="utf-8"))
 except FileNotFoundError:return {"schema_version":1,"operation_id":"UR-215","routes":{}}
 if not isinstance(v,dict) or set(v)!={"schema_version","operation_id","routes"} or v["schema_version"]!=1 or v["operation_id"]!="UR-215" or not isinstance(v["routes"],dict):raise RuntimeError("UR-215 state schema invalid")
 return v
def capture(root:Path,*,symbol:str,now:datetime,response_factory:Callable[[],HttpResponse]|None)->ChartCaptureResult:
 if symbol not in ROUTES:raise ValueError("UR-215 symbol not allowed")
 root=Path(root); path=root/STATE_PATH; state=_state(path); routes=dict(state["routes"])
 if symbol in routes:return ChartCaptureResult("NO_REPEAT",symbol,0,None)
 if response_factory is None:raise RuntimeError("UR-215 response factory required")
 spec=ROUTES[symbol]; claim:dict[str,object]={"status":"ATTEMPTING","identity":spec["identity"],"url":spec["url"],"attempted_at_utc":now.astimezone(timezone.utc).isoformat(),"raw_gets_reserved":1,"raw_gets_invoked":0,"raw_gets_completed":0,"retry_count":0,"redirect_count":0,"fallback_count":0,"auth_cookie_env_used":False}
 routes[symbol]=claim;state["routes"]=routes;_write(path,state)
 try:
  claim["raw_gets_invoked"]=1;routes[symbol]=claim;state["routes"]=routes;_write(path,state);response=response_factory();claim["raw_gets_completed"]=1
  if response.status_code!=200:claim.update({"status":"COMPLETE_FAILURE","failure_type":"HTTP_STATUS","http_status":int(response.status_code),"raw_gets":1});digest=None
  else:
   body=bytes(response.content);digest=hashlib.sha256(body).hexdigest();landing=root/LANDING_ROOT/symbol/digest/"body.json";landing.parent.mkdir(parents=True,exist_ok=True)
   with landing.open("xb") as s:s.write(body);s.flush();os.fsync(s.fileno())
   if hashlib.sha256(landing.read_bytes()).hexdigest()!=digest:raise RuntimeError("UR-215 Landing hash mismatch")
   claim.update({"status":"COMPLETE_CAPTURED","raw_gets":1,"landing_file":landing.relative_to(root).as_posix(),"landing_sha256":digest,"body_bytes":len(body)})
 except Exception as e:digest=None;claim.update({"status":"COMPLETE_FAILURE","failure_type":type(e).__name__,"raw_gets":int(claim["raw_gets_invoked"])})
 routes[symbol]=claim;state["routes"]=routes;_write(path,state);return ChartCaptureResult(str(claim["status"]),symbol,int(claim["raw_gets"]),digest)

def finalize_numeric_free(root:Path,*,symbol:str,failure_type:str)->ChartCaptureResult:
 """Atomically terminalize one retained chart capture after API-zero inspection."""
 if symbol not in ROUTES or not failure_type:raise ValueError("UR-215 finalization input invalid")
 root=Path(root);path=root/STATE_PATH;state=_state(path);routes=dict(state["routes"]);claim=routes.get(symbol)
 if not isinstance(claim,dict) or claim.get("status")!="COMPLETE_CAPTURED":return ChartCaptureResult("NO_REPEAT",symbol,0,None)
 claim.update({"status":"COMPLETE_FAILURE","failure_type":failure_type,"retained_schema_review_api_calls":0,"raw_gets":1});routes[symbol]=claim;state["routes"]=routes;_write(path,state)
 if _state(path)["routes"].get(symbol)!=claim:raise RuntimeError("UR-215 terminal readback mismatch")
 return ChartCaptureResult("COMPLETE_FAILURE",symbol,0,str(claim.get("landing_sha256")) if isinstance(claim.get("landing_sha256"),str) else None)
