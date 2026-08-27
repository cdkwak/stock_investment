from __future__ import annotations

import json
from pathlib import Path

import scripts.manual.pilot.opendart_corporate_action_incremental as pilot


def _list_body(page: int, total_page: int, receipts: list[str]) -> bytes:
    rows = [{
        "corp_cls": "K", "corp_name": "Fixture", "corp_code": "01160363",
        "stock_code": "247540", "report_nm": "무상증자결정",
        "rcept_no": receipt, "flr_nm": "Fixture", "rcept_dt": receipt[:8],
        "rm": "정" if receipt.endswith("68") else "",
    } for receipt in receipts]
    return json.dumps({
        "status": "000", "message": "ok", "page_no": page,
        "page_count": 100, "total_count": total_page,
        "total_page": total_page, "list": rows,
    }, ensure_ascii=False).encode()


class _Response:
    status_code = 200

    def __init__(self, content: bytes):
        self.content = content


class _Session:
    def __init__(self, bodies):
        self.bodies = list(bodies)
        self.calls = []

    def get(self, url, *, params, timeout, allow_redirects):
        self.calls.append((url, params, timeout, allow_redirects))
        return _Response(self.bodies[len(self.calls) - 1])


def test_exact_pilot_captures_pages_advances_cursor_and_replays_api_zero(
    tmp_path, monkeypatch,
):
    key = "k" * 40
    monkeypatch.setenv("OPENDART_API_KEY", key)
    monkeypatch.setattr(pilot, "load_dotenv", lambda **kwargs: True)
    session = _Session([
        _list_body(1, 2, ["20220614000068"]),
        _list_body(2, 2, ["20220614000069"]),
        b'{"status":"013","message":"no data"}',
        b'{"status":"013","message":"no data"}',
    ])
    result = pilot.run(tmp_path, session=session)
    assert result["status"] == "COMPLETE"
    assert result["http_requests"] == 4
    assert result["cursor"] == {
        "receipt_date": "20220614", "receipt_no": "20220614000069",
    }
    assert len(session.calls) == 4
    assert all(timeout == 10 and redirects is False for _, _, timeout, redirects in session.calls)
    run_dir = tmp_path / result["run_dir"]
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["list"]["new_after_cursor"] == 2
    assert summary["factor_candidates"] == 0
    assert summary["backtest_eligible_events"] == 0
    assert {row["decision"] for row in summary["event_family_matrix"]} == {
        "observation_only", "unsupported",
    }
    assert all(key not in path.read_text(encoding="utf-8") for path in run_dir.iterdir())
    assert not (tmp_path / "data/normalized").exists()

    class MustNotCall:
        def get(self, *args, **kwargs):
            raise AssertionError("completed pilot replay must be API zero")

    replay = pilot.run(tmp_path, session=MustNotCall())
    assert replay["status"] == "NOOP_API_ZERO_REPLAY"
    assert replay["http_requests"] == 0


def test_single_page_scope_uses_three_calls_not_the_unused_page_budget(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("OPENDART_API_KEY", "k" * 40)
    monkeypatch.setattr(pilot, "load_dotenv", lambda **kwargs: True)
    session = _Session([
        json.dumps({
            "status": "000", "message": "ok", "page_no": 1,
            "page_count": 100, "total_count": 1, "total_page": 1,
            "list": [{
                "corp_cls": "K", "corp_name": "Fixture", "corp_code": "01160363",
                "stock_code": "247540", "report_nm": "무상증자결정",
                "rcept_no": "20220614000068", "flr_nm": "Fixture",
                "rcept_dt": "20220614", "rm": "정",
            }],
        }, ensure_ascii=False).encode(),
        b'{"status":"013","message":"no data"}',
        b'{"status":"013","message":"no data"}',
    ])
    result = pilot.run(tmp_path, session=session)
    assert result["http_requests"] == 3
    assert len(session.calls) == 3
