"""Run exactly one manifest-approved UR-167 Naver mobile-home window."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import requests
from stock_data.orchestration.naver_mobile_home_windowed_current import NaverMobileHomeWindowedCollector
from stock_data.orchestration.naver_mobile_home_windows import ensure_manifest, read_manifest
URL = "https://m.stock.naver.com/"
def run(root: Path) -> dict[str, object]:
    root=Path(root); ensure_manifest(root); manifest=read_manifest(root); allowed=manifest["allowed_window_ids"]
    if not isinstance(allowed,list) or not all(isinstance(value,str) for value in allowed): raise RuntimeError("UR-167 public manifest has invalid windows")
    result=NaverMobileHomeWindowedCollector(root).run(now=datetime.now(timezone.utc),response_factory=lambda:requests.get(URL,timeout=10,allow_redirects=False),allowed_window_ids=allowed)
    return {"status":result.status,"window_id":result.window_id,"raw_gets":result.raw_gets,"accepted_cids":result.accepted_cids,"rejected":result.rejected,"replay_api_calls":result.replay_api_calls}
def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument("--project-root",type=Path,required=True); parser.add_argument("--confirm-ur167-window",action="store_true"); args=parser.parse_args()
    if not args.confirm_ur167_window: parser.error("--confirm-ur167-window is required")
    print(run(args.project_root)); return 0
if __name__ == "__main__": raise SystemExit(main())
