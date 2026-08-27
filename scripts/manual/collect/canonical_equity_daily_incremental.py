from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

from scripts.manual.diagnostic.data_go_kr_equity_availability_sentinel import _lock, _read_audited_pair, run_sentinel
from stock_data.pipelines.canonical_equity_incremental import (
    build_date_frames, promote_date_atomic, publication_window_passed, refresh_breadth_date_atomic,
)


def main() -> int:
    p=argparse.ArgumentParser()
    p.add_argument("--project-root",type=Path,default=ROOT)
    p.add_argument("--date",required=True)
    p.add_argument("--publication-deadline-kst",required=True)
    p.add_argument("--confirm-live-two-call-atomic",action="store_true")
    p.add_argument("--capture-run",type=Path)
    a=p.parse_args(); root=a.project_root.resolve()
    deadline=datetime.fromisoformat(a.publication_deadline_kst)
    now=datetime.now(ZoneInfo("Asia/Seoul"))
    if not publication_window_passed(deadline_kst=deadline,now_kst=now): raise SystemExit("publication window has not passed")
    if a.capture_run is None:
        if not a.confirm_live_two_call_atomic: raise SystemExit("explicit live confirmation required")
        capture=run_sentinel(root,a.date)
        if capture["status"]!="NONEMPTY_AVAILABLE":
            print(json.dumps({"status":"SOURCE_NOT_AVAILABLE","capture":capture},ensure_ascii=False,default=str)); return 2
        run=Path(capture["run_root"]); business_calls=2
    else:
        if a.confirm_live_two_call_atomic: raise SystemExit("capture rerun must be zero-network")
        run=a.capture_run.resolve(); business_calls=0
    manifest,paths=_read_audited_pair(root,run)
    if str(manifest["base_date"])!=a.date: raise SystemExit("capture base date mismatch")
    frames=build_date_frames(root,base_date=a.date,price_landing=paths["price_cap"],universe_landing=paths["universe"])
    promotion_id="canonical_equity_"+a.date
    with _lock(root,promotion_id):
        result=promote_date_atomic(root,base_date=a.date,frames=frames,landing_manifest_sha256=hashlib.sha256((run/"manifest.json").read_bytes()).hexdigest())
        result["breadth"]=refresh_breadth_date_atomic(root,base_date=a.date)
    result.update({"business_calls":business_calls,"retry_count":0,"publication_deadline_kst":deadline.isoformat(),"capture_run":str(run.relative_to(root))})
    print(json.dumps(result,ensure_ascii=False,default=str,indent=2)); return 0


if __name__=="__main__": raise SystemExit(main())
