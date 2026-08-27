"""UR-176's one exact post-close Naver home-page collector boundary."""
from __future__ import annotations
from datetime import datetime
from pathlib import Path
from stock_data.orchestration.naver_mobile_home_windowed_current import NaverMobileHomeWindowedCollector

WINDOW_ID="2026-08-21T16:00:00+09:00"
STATE_PATH=Path("data/state/naver_mobile_home_ur176_post_close.json")
LANDING_ROOT=Path("data/landing/naver_mobile_home/ur176_post_close")
def collector(root:Path)->NaverMobileHomeWindowedCollector:
    return NaverMobileHomeWindowedCollector(root,operation_id="UR-176",state_path=STATE_PATH,landing_root=LANDING_ROOT,required_status="POST_CLOSE")
def is_active(*,now:datetime)->bool:
    from stock_data.orchestration.naver_mobile_home_windows import window_id
    return window_id(now=now)==WINDOW_ID
__all__=["LANDING_ROOT","STATE_PATH","WINDOW_ID","collector","is_active"]
