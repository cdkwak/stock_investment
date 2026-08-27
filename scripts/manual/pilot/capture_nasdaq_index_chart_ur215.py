"""Capture one independently authorized UR-215 Nasdaq index chart route."""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
from pathlib import Path
import requests
from stock_data.orchestration.nasdaq_index_chart_ur215 import PUBLIC_HEADERS,ROUTES,capture
def main()->int:
 p=argparse.ArgumentParser(description=__doc__);p.add_argument("--project-root",type=Path,required=True);p.add_argument("--symbol",choices=tuple(ROUTES),required=True);p.add_argument("--confirm-ur215-chart",action="store_true");a=p.parse_args()
 if not a.confirm_ur215_chart:p.error("--confirm-ur215-chart is required")
 result=capture(a.project_root,symbol=a.symbol,now=datetime.now(timezone.utc),response_factory=lambda:requests.get(ROUTES[a.symbol]["url"],headers=PUBLIC_HEADERS,timeout=10,allow_redirects=False));print({"status":result.status,"symbol":result.symbol,"raw_gets":result.raw_gets,"landing_sha256":result.landing_sha256});return 0
if __name__=="__main__":raise SystemExit(main())
