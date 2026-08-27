"""Durable one-shot capture ledger for a future Naver mobile-home operation."""
from __future__ import annotations
import json, os, uuid
from pathlib import Path
from typing import Callable, Protocol

class Response(Protocol):
    status_code: int
    content: bytes

class NaverMobileHomeCaptureError(RuntimeError): pass

def _write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True); tmp=path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
    try:
        with tmp.open('xb') as f: f.write(json.dumps(payload,sort_keys=True).encode()); f.flush(); os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        try: tmp.unlink()
        except FileNotFoundError: pass

def capture_once(state_path: Path, response_factory: Callable[[], Response]) -> Response:
    """Persist CLAIMED before invoking injected transport; never retries."""
    if state_path.exists(): raise NaverMobileHomeCaptureError('DURABLE_STATE_EXISTS_NO_REPEAT')
    state={'schema_version':1,'status':'CLAIMED','raw_gets_reserved':1,'raw_gets_invoked':0,'raw_gets_completed':0,'retry_count':0,'redirect_count':0}
    _write(state_path,state)
    state['raw_gets_invoked']=1; _write(state_path,state)
    try:
        response=response_factory(); state['raw_gets_completed']=1; state['status']='COMPLETE' if response.status_code==200 else 'COMPLETE_FAILURE'; _write(state_path,state); return response
    except Exception:
        state['status']='COMPLETE_FAILURE'; _write(state_path,state); raise

def assert_defective_completed_state(payload: dict[str, object]) -> None:
    if payload.get('raw_gets_completed') != 1 or payload.get('raw_gets_invoked') != 0: raise NaverMobileHomeCaptureError('not the UR-166 defective-state signature')
