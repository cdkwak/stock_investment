from dataclasses import dataclass
from datetime import datetime, timezone
from stock_data.orchestration.naver_mobile_home_windowed_current import NaverMobileHomeWindowedCollector
@dataclass
class Response: status_code:int=503; content:bytes=b""
def test_exact_window_is_claimed_before_transport_and_never_repeats(tmp_path):
 collector=NaverMobileHomeWindowedCollector(tmp_path); now=datetime(2026,8,21,5,30,tzinfo=timezone.utc); seen=[]
 result=collector.run(now=now,response_factory=lambda:(seen.append("called") or Response()))
 assert seen==["called"] and result.status=="COMPLETE_FAILURE" and result.raw_gets==1
 assert collector.run(now=now,response_factory=lambda:Response()).status=="NO_REPEAT"
