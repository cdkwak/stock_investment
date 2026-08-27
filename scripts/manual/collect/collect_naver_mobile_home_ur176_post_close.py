"""Run the single due UR-176 post-close Naver home-page capture."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from pathlib import Path
import requests
from stock_data.orchestration.naver_mobile_home_post_close import WINDOW_ID, collector, is_active
URL="https://m.stock.naver.com/"
def run(root:Path)->dict[str,object]:
    now=datetime.now(timezone.utc)
    if not is_active(now=now): return {"status":"WINDOW_NOT_DUE","window_id":WINDOW_ID,"raw_gets":0}
    result=collector(root).run(now=now,response_factory=lambda:requests.get(URL,timeout=10,allow_redirects=False),allowed_window_ids=(WINDOW_ID,))
    return {"status":result.status,"window_id":result.window_id,"raw_gets":result.raw_gets,"accepted_cids":result.accepted_cids,"rejected":result.rejected,"replay_api_calls":result.replay_api_calls}
def main()->int:
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument("--project-root",type=Path,required=True); parser.add_argument("--confirm-ur176-post-close",action="store_true"); args=parser.parse_args()
    if not args.confirm_ur176_post_close: parser.error("--confirm-ur176-post-close is required")
    print(run(args.project_root)); return 0
if __name__=="__main__": raise SystemExit(main())
