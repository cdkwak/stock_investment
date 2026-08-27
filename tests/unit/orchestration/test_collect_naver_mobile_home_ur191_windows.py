from datetime import datetime, timezone
from scripts.manual.collect.collect_naver_mobile_home_ur191_windows import run
from stock_data.orchestration.naver_mobile_home_ur191_windows import ensure_manifest

class Response:
    def __init__(self, code): self.status_code, self.content = code, b""

def test_missing_or_malformed_manifest_is_api_zero(tmp_path) -> None:
    now = datetime(2026, 8, 24, 0, 31, tzinfo=timezone.utc); calls = []
    assert run(tmp_path, now=now, get=lambda *args, **kwargs: calls.append(1))["raw_gets"] == 0
    path = tmp_path / "data/state/naver_mobile_home_ur191_activation.json"; path.parent.mkdir(parents=True, exist_ok=True); path.write_text("{", encoding="utf-8")
    assert run(tmp_path, now=now, get=lambda *args, **kwargs: calls.append(1))["raw_gets"] == 0 and calls == []

def test_plus_one_minute_reports_selected_boundary_without_call_before_date(tmp_path) -> None:
    ensure_manifest(tmp_path); result = run(tmp_path, now=datetime(2026, 8, 21, 0, 31, tzinfo=timezone.utc), get=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no call")))
    assert result["selected_boundary"] is None and result["raw_gets"] == 0

def test_0931_selects_0930_and_terminal_ledger_blocks_callback(tmp_path) -> None:
    ensure_manifest(tmp_path); now = datetime(2026, 8, 24, 0, 31, tzinfo=timezone.utc); calls = []
    first = run(tmp_path, now=now, get=lambda *args, **kwargs: calls.append(1) or Response(500))
    second = run(tmp_path, now=now, get=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no callback")))
    assert first["selected_boundary"] == "2026-08-24T09:30:00+09:00" and first["attempted_at_utc"] == now.isoformat() and calls == [1] and second["raw_gets"] == 0

def test_empty_ledger_is_malformed_api_zero(tmp_path) -> None:
    ensure_manifest(tmp_path); path = tmp_path / "data/state/naver_mobile_home_ur191_windows.json"; path.write_text("{}", encoding="utf-8")
    assert run(tmp_path, now=datetime(2026, 8, 24, 0, 31, tzinfo=timezone.utc), get=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no call")))["raw_gets"] == 0
