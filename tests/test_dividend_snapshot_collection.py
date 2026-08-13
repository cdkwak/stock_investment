import json
from pathlib import Path

import requests

from scripts.manual.dividend_snapshot_collection import collect_dividend_snapshot
from stock_data.providers.data_go_kr.dividend_observation import load_dividend_observation


def item(index, day="20260813"):
    return {"basDt": day, "crno": f"110111{index:07d}", "isinCd": f"KR7{index:09d}",
            "stckIssuCmpyNm": f"issuer-{index}", "dvdnBasDt": "20261231",
            "cashDvdnPayDt": "", "stckHndvDt": "", "scrsItmsKcdNm": "common",
            "stckDvdnRcdNm": "ordinary", "stckGenrDvdnAmt": "1",
            "stckGenrCashDvdnRt": "0", "stckGenrDvdnRt": "0",
            "stckGrdnDvdnAmt": "0", "cashGrdnDvdnRt": "0",
            "stckGrdnDvdnRt": "0", "stckParPrc": "5000", "stckStacMd": "1231"}


def response(page, rows, total):
    payload={"response":{"header":{"resultCode":"00","resultMsg":"NORMAL SERVICE."},
             "body":{"items":{"item":rows},"numOfRows":2,"pageNo":page,"totalCount":total}}}
    r=requests.Response(); r.status_code=200; r.headers["Content-Type"]="application/json"
    r._content=json.dumps(payload).encode(); return r


class Delegate:
    def __init__(self, responses): self.responses=list(responses); self.calls=[]
    def get(self,url,**kwargs): self.calls.append((url,kwargs)); return self.responses.pop(0)


def test_bounded_checkpoint_resume_builds_complete_landing(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("scripts.manual.dividend_snapshot_collection.PAGE_SIZE", 2)
    first=Delegate([response(1,[item(1),item(2)],3)])
    a=collect_dividend_snapshot(project_root=tmp_path,snapshot_date="20260813",
        service_key="fixture-key",max_calls=1,delegate=first)
    assert a["status"]=="RUNNING" and a["completed_pages"]==[1]
    second=Delegate([response(2,[item(3)],3)])
    b=collect_dividend_snapshot(project_root=tmp_path,snapshot_date="20260813",
        service_key="fixture-key",max_calls=1,delegate=second)
    assert b["status"]=="COMPLETE" and len(first.calls)+len(second.calls)==2
    frame,meta=load_dividend_observation(tmp_path/b["landing_path"])
    assert len(frame)==3 and meta["response_count"]==2
    persisted=b"".join(p.read_bytes() for p in tmp_path.rglob("*") if p.is_file())
    assert b"fixture-key" not in persisted


def test_total_change_stops_without_complete_landing(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("scripts.manual.dividend_snapshot_collection.PAGE_SIZE", 2)
    delegate=Delegate([response(1,[item(1),item(2)],3),response(2,[item(3)],4)])
    result=collect_dividend_snapshot(project_root=tmp_path,snapshot_date="20260813",
        service_key="fixture-key",max_calls=2,delegate=delegate)
    assert result["status"]=="TOTAL_CHANGED_STOP" and len(delegate.calls)==2
    assert not list(tmp_path.rglob("full_history.json"))
    stopped=json.loads(next((tmp_path/"data/state/dividend_snapshot_collection").glob("*.json")).read_text())
    evidence=tmp_path/stopped["terminal_evidence"]["path"]
    before=evidence.read_bytes()
    later=Delegate([response(2,[item(3)],3)])
    try: collect_dividend_snapshot(project_root=tmp_path,snapshot_date="20260813",
        service_key="fixture-key",max_calls=1,delegate=later)
    except Exception as error: assert "terminal" in str(error)
    else: raise AssertionError("terminal checkpoint resumed")
    assert not later.calls and evidence.read_bytes()==before


def test_provider_lock_blocks_before_call(tmp_path: Path):
    lock=tmp_path/"data/state/.data_go_kr_network.lock"; lock.parent.mkdir(parents=True); lock.write_text("{}")
    delegate=Delegate([response(1,[item(1)],1)])
    try: collect_dividend_snapshot(project_root=tmp_path,snapshot_date="20260813",
        service_key="fixture-key",max_calls=1,delegate=delegate)
    except Exception as error: assert "lock already exists" in str(error)
    else: raise AssertionError("lock ignored")
    assert not delegate.calls
