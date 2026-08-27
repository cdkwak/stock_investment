from datetime import datetime, timezone
from stock_data.orchestration.naver_mobile_home_ur211_window import selected_boundary
from stock_data.orchestration.naver_mobile_home_ur211_window import collector
from stock_data.orchestration.naver_mobile_home_windowed_current import NaverMobileHomeWindowedCollector
from pathlib import Path
from stock_data.providers.naver_mobile_home_observation import route_for
from tests.unit.providers.test_naver_mobile_home_observation import HTML
class Response:
 def __init__(self, code, body): self.status_code,self.content=code,body
def test_half_open_boundary_has_no_backfill():
 assert selected_boundary(now=datetime(2026,8,21,9,30,tzinfo=timezone.utc))=="2026-08-21T18:30:00+09:00"
 assert selected_boundary(now=datetime(2026,8,21,9,59,59,tzinfo=timezone.utc))=="2026-08-21T18:30:00+09:00"
 assert selected_boundary(now=datetime(2026,8,21,10,0,tzinfo=timezone.utc)) is None

def test_fx_only_landing_and_terminal_replay(tmp_path):
 now=datetime(2026,8,21,9,30,tzinfo=timezone.utc); body=HTML.replace(b"14:12",b"18:29")
 first=collector(tmp_path).run(now=now,response_factory=lambda:Response(200,body),allowed_window_ids=("2026-08-21T18:30:00+09:00",))
 again=collector(tmp_path).run(now=now,response_factory=lambda:(_ for _ in ()).throw(AssertionError("no repeat")),allowed_window_ids=("2026-08-21T18:30:00+09:00",))
 assert first.accepted_cids==("FX_USDKRW",) and first.raw_gets==1 and again.raw_gets==0 and again.replay_api_calls==0
 assert collector(tmp_path).store.select(route_for("FX_USDKRW")).unit=="KRW per USD"

def test_stale_orphan_and_nonfx_are_fail_closed(tmp_path):
 now=datetime(2026,8,21,9,30,tzinfo=timezone.utc); result=collector(tmp_path).run(now=now,response_factory=lambda:Response(200,HTML),allowed_window_ids=("2026-08-21T18:30:00+09:00",))
 assert result.accepted_cids==() and result.raw_gets==1

def test_attempting_ledger_is_no_repeat_without_callback(tmp_path):
 import json
 now=datetime(2026,8,21,9,30,tzinfo=timezone.utc); c=collector(tmp_path); c.state_path.parent.mkdir(parents=True); c.state_path.write_text(json.dumps({"schema_version":1,"operation_id":"UR-211","windows":{"2026-08-21T18:30:00+09:00":{"status":"ATTEMPTING"}}}),encoding="utf-8")
 result=c.run(now=now,response_factory=lambda:(_ for _ in ()).throw(AssertionError("no callback")),allowed_window_ids=("2026-08-21T18:30:00+09:00",))
 assert result.status=="NO_REPEAT" and result.raw_gets==0 and result.replay_api_calls==0

def test_stale_next_window_preserves_exact_prior_bytes(tmp_path):
 c=NaverMobileHomeWindowedCollector(tmp_path,operation_id="TEST",state_path=Path("state.json"),landing_root=Path("landing"),projection_cids=("FX_USDKRW",)); first=datetime(2026,8,21,9,30,tzinfo=timezone.utc); second=datetime(2026,8,21,10,0,tzinfo=timezone.utc); allowed=("2026-08-21T18:30:00+09:00","2026-08-21T19:00:00+09:00")
 c.run(now=first,response_factory=lambda:Response(200,HTML.replace(b"14:12",b"18:29")),allowed_window_ids=allowed); path=tmp_path/"data/state/current_observations/naver_mobile_home_current.json"; prior=path.read_bytes()
 c.run(now=second,response_factory=lambda:Response(200,HTML),allowed_window_ids=allowed)
 assert path.read_bytes()==prior

def test_tampered_landing_readback_is_terminal_no_projection(tmp_path,monkeypatch):
 original=Path.read_bytes
 def tamper(path):
  value=original(path)
  return value+b"x" if path.name=="response.html" else value
 monkeypatch.setattr(Path,"read_bytes",tamper); now=datetime(2026,8,21,9,30,tzinfo=timezone.utc)
 result=collector(tmp_path).run(now=now,response_factory=lambda:Response(200,HTML.replace(b"14:12",b"18:29")),allowed_window_ids=("2026-08-21T18:30:00+09:00",))
 assert result.status=="COMPLETE_FAILURE" and result.accepted_cids==()
