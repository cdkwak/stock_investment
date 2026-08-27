from dataclasses import dataclass
from datetime import datetime, timezone
from stock_data.orchestration.naver_mobile_home_post_close import collector
@dataclass
class Response: status_code:int=503; content:bytes=b''
def test_post_close_has_distinct_durable_no_repeat_state(tmp_path):
    now=datetime(2026,8,21,7,0,tzinfo=timezone.utc); target=collector(tmp_path)
    assert target.run(now=now,response_factory=lambda:Response(),allowed_window_ids=('2026-08-21T16:00:00+09:00',)).raw_gets==1
    assert target.run(now=now,response_factory=lambda:Response(),allowed_window_ids=('2026-08-21T16:00:00+09:00',)).status=='NO_REPEAT'
