import json
from dataclasses import dataclass
from stock_data.orchestration.naver_mobile_home_capture import assert_defective_completed_state, capture_once

@dataclass
class Response: status_code:int=200; content:bytes=b'<html/>'

def test_claim_exists_before_injected_transport(tmp_path) -> None:
    path=tmp_path/'state.json'
    def factory():
        state=json.loads(path.read_text()); assert state['status']=='CLAIMED' and state['raw_gets_invoked']==1 and state['raw_gets_completed']==0; return Response()
    assert capture_once(path,factory).status_code==200
    assert json.loads(path.read_text())['raw_gets_completed']==1

def test_observed_defect_signature_is_explicit() -> None:
    assert_defective_completed_state({'raw_gets_completed':1,'raw_gets_invoked':0})
