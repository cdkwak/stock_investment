"""Capture the one allowed UR-201 official Nasdaq TNX HTML page, once."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from pathlib import Path
import requests
from stock_data.orchestration.nasdaq_tnx_ur201_discovery import PAGE_URL, PUBLIC_HEADERS, capture_page
def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument("--project-root",type=Path,required=True); parser.add_argument("--confirm-ur201-html",action="store_true"); args=parser.parse_args()
    if not args.confirm_ur201_html: parser.error("--confirm-ur201-html is required")
    result=capture_page(args.project_root,now=datetime.now(timezone.utc),response_factory=lambda:requests.get(PAGE_URL,headers=PUBLIC_HEADERS,timeout=10,allow_redirects=False))
    print({"status":result.status,"raw_gets":result.raw_gets,"body_sha256":result.body_sha256}); return 0
if __name__=="__main__": raise SystemExit(main())
