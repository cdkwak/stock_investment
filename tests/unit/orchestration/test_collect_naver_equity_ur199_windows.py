from datetime import datetime, timezone

from scripts.manual.collect.collect_naver_equity_ur199_windows import run


def test_pre_date_entrypoint_does_not_construct_or_call_transport(tmp_path) -> None:
    calls = []
    result = run(tmp_path, now=datetime(2026, 8, 21, 0, 30, tzinfo=timezone.utc), get=lambda *args, **kwargs: calls.append((args, kwargs)))
    assert result["api_calls"] == 0 and calls == []


def test_missing_or_malformed_manifest_is_api_zero(tmp_path) -> None:
    calls = []
    missing = run(tmp_path, now=datetime(2026, 8, 24, 0, 30, tzinfo=timezone.utc), get=lambda *args, **kwargs: calls.append(1))
    manifest = tmp_path / "data/state/naver_equity_ur199_activation.json"; manifest.parent.mkdir(parents=True, exist_ok=True); manifest.write_text("{", encoding="utf-8")
    malformed = run(tmp_path, now=datetime(2026, 8, 24, 0, 30, tzinfo=timezone.utc), get=lambda *args, **kwargs: calls.append(1))
    assert missing["api_calls"] == malformed["api_calls"] == 0 and calls == []


def test_malformed_ledger_is_api_zero(tmp_path) -> None:
    from stock_data.orchestration.naver_equity_ur199_windows import ensure_manifest
    ensure_manifest(tmp_path); ledger = tmp_path / "data/state/naver_equity_ur199_windows.json"; ledger.write_text("{}", encoding="utf-8")
    result = run(tmp_path, now=datetime(2026, 8, 24, 0, 30, tzinfo=timezone.utc), get=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("no call")))
    assert result["api_calls"] == 0 and set(result["statuses"].values()) == {"PREFLIGHT_INVALID_API_ZERO"}
