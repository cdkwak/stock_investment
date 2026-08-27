"""UR-211 one-shot FX_USDKRW-only future home window."""
from datetime import datetime
from pathlib import Path
from stock_data.orchestration.naver_mobile_home_windowed_current import NaverMobileHomeWindowedCollector
from stock_data.orchestration.naver_mobile_home_windows import window_id

WINDOW_ID="2026-08-21T18:30:00+09:00"
STATE_PATH=Path("data/state/naver_mobile_home_ur211_window.json")
LANDING_ROOT=Path("data/landing/naver_mobile_home/ur211")
def selected_boundary(*, now:datetime)->str|None: return WINDOW_ID if window_id(now=now)==WINDOW_ID else None
def collector(root:Path)->NaverMobileHomeWindowedCollector: return NaverMobileHomeWindowedCollector(Path(root),operation_id="UR-211",state_path=STATE_PATH,landing_root=LANDING_ROOT,projection_cids=("FX_USDKRW",))
