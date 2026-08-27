"""UR-187 one-shot official Cboe CDN delayed VIX capture."""
from __future__ import annotations
import argparse, hashlib, json, os, uuid
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
from stock_data.orchestration.naver_mobile_home_capture import capture_once
from stock_data.providers.cboe_vix_delayed_quote import CboeVixPayloadError, parse_payload
URL="https://cdn.cboe.com/api/global/delayed_quotes/quotes/_VIX.json"; STATE=Path("data/state/cboe_vix_ur187.json"); ROOT=Path("data/landing/cboe_vix/ur187")
def atomic(path:Path,body:bytes)->None:
 path.parent.mkdir(parents=True,exist_ok=True); tmp=path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
 try:
  with tmp.open("xb") as out: out.write(body); out.flush(); os.fsync(out.fileno())
  os.replace(tmp,path)
 finally:
  try:tmp.unlink()
  except FileNotFoundError:pass
def run(root:Path)->dict[str,object]:
 root=Path(root); now=datetime.now(timezone.utc); response=capture_once(root/STATE,lambda:requests.get(URL,timeout=10,allow_redirects=False))
 if response.status_code!=200:return {"status":"HTTP_FAILURE","raw_gets":1,"http_status":response.status_code}
 body=bytes(response.content); digest=hashlib.sha256(body).hexdigest(); landing=root/ROOT/digest/"response.json"; atomic(landing,body)
 if hashlib.sha256(landing.read_bytes()).hexdigest()!=digest:raise RuntimeError("LANDING_HASH_READBACK_FAILED")
 try: parsed=parse_payload(json.loads(body))
 except (json.JSONDecodeError,CboeVixPayloadError) as error:return {"status":"SCHEMA_OR_CONTRACT_FAILURE","raw_gets":1,"landing_sha256":digest,"reason":str(error)}
 age=now-parsed["provider_at"]
 if parsed["provider_at"]>now or parsed["provider_at"].astimezone(ZoneInfo("Asia/Seoul")).date()!=now.astimezone(ZoneInfo("Asia/Seoul")).date() or age.total_seconds()>3600:return {"status":"FRESHNESS_FAILURE","raw_gets":1,"landing_sha256":digest,"reason":"TODAY_KST_OR_60M_FAILED"}
 return {"status":"ACCEPTANCE_REQUIRES_ATOMIC_PROJECTION_IMPLEMENTATION","raw_gets":1,"landing_sha256":digest,"provider_timestamp_utc":parsed["provider_at"].isoformat()}
def main()->int:
 p=argparse.ArgumentParser();p.add_argument("--project-root",type=Path,required=True);p.add_argument("--confirm-ur187",action="store_true");a=p.parse_args()
 if not a.confirm_ur187:p.error("--confirm-ur187 is required")
 print(run(a.project_root));return 0
if __name__=="__main__":raise SystemExit(main())
