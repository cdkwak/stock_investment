from datetime import datetime, timezone
from stock_data.orchestration.naver_mobile_home_windows import ensure_manifest, is_active
def test_only_exact_windows_are_active(tmp_path):
 ensure_manifest(tmp_path)
 assert is_active(tmp_path,now=datetime(2026,8,21,5,30,tzinfo=timezone.utc))
 assert not is_active(tmp_path,now=datetime(2026,8,21,5,0,tzinfo=timezone.utc))
