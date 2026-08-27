"""Run the sole eligible UR-211 USD/KRW home window after read-only preflight."""
from __future__ import annotations
import argparse,json
from datetime import datetime,timezone
from pathlib import Path
import requests
from stock_data.orchestration.naver_mobile_home_ur211_window import STATE_PATH,collector,selected_boundary
URL="https://m.stock.naver.com/"
def run(root:Path,*,now:datetime|None=None,get=requests.get)->dict[str,object]:
 root=Path(root); now=now or datetime.now(timezone.utc); boundary=selected_boundary(now=now); path=root/STATE_PATH
 if boundary is None: return {"selected_boundary":boundary,"attempted_at_utc":now.isoformat(),"status":"PREFLIGHT_API_ZERO","raw_gets":0}
 if path.exists():
  try:
   state=json.loads(path.read_text(encoding="utf-8")); current=state["windows"].get(boundary)
   if not isinstance(state,dict) or state.get("schema_version")!=1 or state.get("operation_id")!="UR-211" or not isinstance(state.get("windows"),dict) or current is not None: return {"selected_boundary":boundary,"attempted_at_utc":now.isoformat(),"status":"PREFLIGHT_API_ZERO","raw_gets":0}
  except (OSError,json.JSONDecodeError,KeyError,TypeError): return {"selected_boundary":boundary,"attempted_at_utc":now.isoformat(),"status":"PREFLIGHT_API_ZERO","raw_gets":0}
 r=collector(root).run(now=now,response_factory=lambda:get(URL,timeout=10,allow_redirects=False),allowed_window_ids=(boundary,))
 return {"selected_boundary":boundary,"attempted_at_utc":now.isoformat(),"status":r.status,"raw_gets":r.raw_gets,"replay_api_calls":r.replay_api_calls}
def main()->int:
 p=argparse.ArgumentParser(description=__doc__);p.add_argument("--project-root",type=Path,required=True);p.add_argument("--confirm-ur211-window",action="store_true");a=p.parse_args()
 if not a.confirm_ur211_window:p.error("--confirm-ur211-window is required")
 print(run(a.project_root));return 0
if __name__=="__main__":raise SystemExit(main())
