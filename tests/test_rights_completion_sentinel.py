from datetime import datetime, timezone
import json
from pathlib import Path

import requests

from scripts.manual.rights_completion_sentinel_support import (
    EXPECTED_TOTAL,
    FILTERS,
    NUM_ROWS,
    run_completion_sentinel,
)
from stock_data.providers.data_go_kr.data_v1 import RIGHTS_SOURCE_FIELDS
from stock_data.providers.data_go_kr.rights_observation import promote_rights_diagnostic


def _item(index: int) -> dict[str, str]:
    values = {field: "" for field in RIGHTS_SOURCE_FIELDS}
    values.update({
        "basDt": FILTERS["basDt"],
        "issuCmpyKsdCustNo": FILTERS["issuCmpyKsdCustNo"],
        "crno": f"110111021{index:04d}",
        "stckIssuCmpyNm": f"issuer-{index}",
        "scrsIssuMnbdCd": f"{index:05d}",
        "rgtExertSttgDt": "20191231",
        "rgtExertEdDt": "20191231",
        "stckParPrc": "5000",
        "stckStacMd": "1231",
    })
    return values


def _response(count: int = EXPECTED_TOTAL, *, total: int = EXPECTED_TOTAL) -> requests.Response:
    payload = {"response": {
        "header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE."},
        "body": {"items": {"item": [_item(i) for i in range(count)]},
                 "numOfRows": NUM_ROWS, "pageNo": 1, "totalCount": total},
    }}
    response = requests.Response()
    response.status_code = 200
    response.headers["Content-Type"] = "application/json"
    response._content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return response


class Delegate:
    def __init__(self, response: requests.Response):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def _times():
    values = iter([
        datetime(2026, 8, 13, 12, 30, tzinfo=timezone.utc),
        datetime(2026, 8, 13, 12, 30, 1, tzinfo=timezone.utc),
    ])
    return lambda: next(values)


def test_success_is_exactly_one_call_landing_first_and_promotable(tmp_path: Path):
    delegate = Delegate(_response())
    result = run_completion_sentinel(
        project_root=tmp_path, service_key="fixture-service-key",
        delegate=delegate, now_fn=_times(),
    )
    assert result["status"] == "SOURCE_SNAPSHOT_COMPLETE"
    assert result["request_count"] == 1 and result["retry_count"] == 0
    assert len(delegate.calls) == 1
    _, request = delegate.calls[0]
    assert request["params"] == {
        "serviceKey": "fixture-service-key", "resultType": "json",
        **FILTERS, "numOfRows": NUM_ROWS, "pageNo": 1,
    }
    run_dir = Path(result["diagnostic_root"])
    assert {path.name for path in run_dir.iterdir()} == {
        "response_envelope.json", "call_ledger.redacted.json", "handoff_manifest.json"
    }
    persisted = b"".join(path.read_bytes() for path in tmp_path.rglob("*") if path.is_file())
    assert b"fixture-service-key" not in persisted
    promoted = promote_rights_diagnostic(project_root=tmp_path, diagnostic_root=run_dir)
    assert promoted["row_count"] == EXPECTED_TOTAL
    assert promoted["snapshot"]["historical_completeness"] is False
    assert promoted["snapshot"]["canonical_economic_event_identity"] is False


def test_partial_response_stops_and_retains_evidence_without_normalized(tmp_path: Path):
    delegate = Delegate(_response(EXPECTED_TOTAL - 1))
    result = run_completion_sentinel(
        project_root=tmp_path, service_key="fixture-service-key",
        delegate=delegate, now_fn=_times(),
    )
    assert result["status"] == "PARTIAL_OR_AMBIGUOUS_STOP"
    assert result["request_count"] == 1 and len(delegate.calls) == 1
    assert Path(result["diagnostic_root"]).is_dir()
    assert not (tmp_path / "data/normalized").exists()


def test_provider_lock_blocks_before_network(tmp_path: Path):
    lock = tmp_path / "data/state/.data_go_kr_network.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("{}", encoding="utf-8")
    delegate = Delegate(_response())
    try:
        run_completion_sentinel(
            project_root=tmp_path, service_key="fixture-service-key",
            delegate=delegate, now_fn=_times(),
        )
    except RuntimeError as error:
        assert "lock already exists" in str(error)
    else:
        raise AssertionError("existing provider lock was ignored")
    assert not delegate.calls
