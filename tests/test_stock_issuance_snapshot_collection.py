from __future__ import annotations

import json
from pathlib import Path

import pytest

import scripts.manual.collect_data_go_kr_stock_issuance_snapshot as module
from scripts.manual.data_go_kr_stock_issuance_pilot import CaptureSession, PilotError
from stock_data.providers.data_go_kr.client import DataGoKrClient, write_landing_pages_atomic


class Response:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload
        self.content = json.dumps(payload).encode()

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class Backend:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def get(self, url, **kwargs):
        self.calls += 1
        return Response(self.responses.pop(0))


def item(index, page):
    from scripts.manual.data_go_kr_stock_issuance_pilot import SOURCE_FIELDS
    row = {field: "" for field in SOURCE_FIELDS}
    row.update({
        "basDt": "20260812", "crno": f"234111{page:02d}{index:05d}"[-13:],
        "isinCd": f"KR7{page:02d}{index:07d}"[-12:], "isinCdNm": f"name-{page}-{index}",
        "stckIssuCmpyNm": f"issuer-{page}-{index}", "scrsDcd": "21",
        "stckIssuSqno": str(index), "stckIssuDt": "20260813" if index == 0 else "20260801",
        "stckIssuDcnt": str(index), "scrsItmsKcd": "0101", "scrsItmsKcdNm": "common",
        "stckIssuRcd": "101", "stckIssuRcdNm": "paid", "issuStckCnt": str(index + 1),
    })
    return row


def response(page, rows, total=3):
    return {"response": {"header": {"resultCode": "00", "resultMsg": "NORMAL"},
        "body": {"pageNo": page, "numOfRows": 2, "totalCount": total,
                 "items": {"item": rows}}}}


@pytest.fixture
def configured(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_GO_KR_SERVICE_KEY", "fixture-key")
    monkeypatch.setattr(module, "EXPECTED_TOTAL", 3)
    monkeypatch.setattr(module, "PAGE_SIZE", 2)
    monkeypatch.setattr(module, "EXPECTED_PAGES", 2)
    monkeypatch.setattr(module, "SOURCE_REFERENCE_DATE_MAX", "20260812")
    monkeypatch.setattr(module, "SOURCE_REFERENCE_DATE_MIN_BOUND", "20200401")
    monkeypatch.setattr(module, "COUNT_RUN_ID", "count-run")
    monkeypatch.setattr(module, "COUNT_MANIFEST_SHA256", "a" * 64)
    monkeypatch.setattr(module, "_verify_count_gate", lambda root: None)
    root = tmp_path / "project"
    root.mkdir()
    return root


def test_checkpoint_resume_and_complete_audit(configured):
    first = Backend([response(1, [item(0, 1), item(1, 1)])])
    a = module.collect_snapshot(
        project_root=configured, approval_sha256=module.plan_sha256(), max_calls=1,
        delegate=first, sleep_fn=lambda _: None,
    )
    assert a["status"] == "RUNNING" and first.calls == 1
    second = Backend([response(2, [item(0, 2)])])
    b = module.collect_snapshot(
        project_root=configured, approval_sha256=module.plan_sha256(), max_calls=1,
        run_root=Path(a["run_root"]), delegate=second, sleep_fn=lambda _: None,
    )
    assert b["status"] == "COMPLETE_REVIEW_REQUIRED" and second.calls == 1
    audit = module.verify_complete_snapshot(configured, Path(a["run_root"]))
    assert audit["status"] == "OFFLINE_AUDIT_PASS" and audit["rows"] == 3
    assert audit["future_effective_rows"] == 2
    assert audit["source_reference_date_min"] == "20260812"
    persisted = b"".join(path.read_bytes() for path in configured.rglob("*") if path.is_file())
    assert b"fixture-key" not in persisted


def test_wrong_plan_fails_before_network(configured):
    backend = Backend([response(1, [item(0, 1), item(1, 1)])])
    with pytest.raises(module.CollectionStopped, match="approval"):
        module.collect_snapshot(
            project_root=configured, approval_sha256="0" * 64, max_calls=1,
            delegate=backend,
        )
    assert backend.calls == 0


def test_page_schema_anomaly_stops_with_raw_evidence(configured):
    bad = item(0, 1)
    bad["extra"] = "x"
    backend = Backend([response(1, [bad, item(1, 1)])])
    result = module.collect_snapshot(
        project_root=configured, approval_sha256=module.plan_sha256(), max_calls=1,
        delegate=backend, sleep_fn=lambda _: None,
    )
    assert result["status"] == "STOPPED" and result["failed_page"] == 1
    run = Path(next((configured / module.LANDING_RELATIVE).iterdir()))
    assert (run / "page=00001/raw_response.body").exists()
    assert not (configured / "data/normalized").exists()


def test_active_provider_lock_blocks_before_landing(configured):
    lock = configured / module.LOCK_RELATIVE
    lock.parent.mkdir(parents=True)
    lock.write_text("active", encoding="utf-8")
    before = set((configured / "data").rglob("*"))
    with pytest.raises(PilotError, match="already held"):
        module.collect_snapshot(
            project_root=configured, approval_sha256=module.plan_sha256(), max_calls=1,
            delegate=Backend([response(1, [item(0, 1), item(1, 1)])]),
        )
    assert set((configured / "data").rglob("*")) == before


def test_stopped_first_page_is_adopted_without_network(configured, monkeypatch):
    monkeypatch.setattr(module, "STOPPED_FIRST_PAGE_RUN_ID", "stopped-v1")
    monkeypatch.setattr(module, "STOPPED_FIRST_PAGE_PLAN_SHA256", "b" * 64)
    run = configured / module.LANDING_RELATIVE / "stopped-v1"
    page = run / "page=00001"
    page.mkdir(parents=True)
    backend = Backend([response(1, [item(0, 1), item(1, 1)])])
    capture = CaptureSession(
        backend, page, "fixture-key",
        {"numOfRows": "2", "pageNo": "1", "resultType": "json"},
    )
    result = DataGoKrClient(
        endpoint=module.ENDPOINT, service_key="fixture-key", session=capture,
        max_attempts=1,
    ).fetch_page(num_of_rows=2, page_no=1)
    write_landing_pages_atomic((result.payload,), page / "response.json")
    (run / "checkpoint.json").write_text(json.dumps({
        "version": 1, "status": "STOPPED", "plan_sha256": "b" * 64,
        "failed_page": 1, "network_calls": 1, "completed_pages": [],
        "page_evidence": [],
    }), encoding="utf-8")
    (run / "call_ledger.jsonl").write_text(json.dumps({
        "event": "PAGE_STOPPED", "page_no": 1, "network_calls": 1,
        "retry_count": 0,
    }) + "\n", encoding="utf-8")
    before_calls = backend.calls
    adopted = module.adopt_stopped_first_page(
        configured, approval_sha256=module.plan_sha256(),
    )
    assert backend.calls == before_calls
    assert adopted["completed_pages"] == [1] and adopted["calls_this_run"] == 0
    adopted_run = Path(adopted["run_root"])
    assert (adopted_run / "page=00001/raw_response.body").read_bytes() == (
        page / "raw_response.body"
    ).read_bytes()
