from datetime import date, timedelta
import json
from pathlib import Path

import pandas as pd
import pytest
from urllib.parse import quote

import scripts.manual.refresh_global_current as refresh
from scripts.manual.refresh_global_current import (
    BudgetSession, RefreshError, _files_manifest, _finite_latest, _replace_roots_atomically,
    _assert_plain_path, _recover_transaction, _series_revision,
)
from stock_data.contracts.global_market import GLOBAL_INDEX_PRICE_DAILY
from stock_data.providers.fred import fetch_series
from stock_data.providers.public_http_capture import capture_public_response
from stock_data.providers.yahoo import CONFIG, _epoch
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic
from stock_data.validation.global_market import validate_global_index


class Response:
    status_code = 200
    content = b"DATE,DGS2\n2026-08-12,3.70\n"
    text = content.decode()
    headers = {"Content-Type": "text/csv"}
    def raise_for_status(self): pass


class Backend:
    def __init__(self): self.kwargs = None
    def get(self, *args, **kwargs): self.kwargs = kwargs; return Response()


class YahooResponse(Response):
    content = b'{"chart":"captured-by-offline-test"}'
    text = content.decode()


def test_budget_session_enforces_exact_hard_cap():
    session = BudgetSession(2, Backend())
    session.get("a"); session.get("b")
    with pytest.raises(RefreshError, match="cap"):
        session.get("c")
    assert session.calls == 2 and session.statuses == [200, 200]


def test_finite_latest_ignores_source_missing_values():
    frame = pd.DataFrame({"date": ["2026-08-10", "2026-08-11"], "dgs10": [4.1, None]})
    assert _finite_latest(frame, ("dgs10",)) == {"dgs10": "2026-08-10"}


def test_fred_explicit_start_end_are_captured(tmp_path):
    backend = Backend()
    frame = fetch_series("DGS2", date(2026, 8, 1), end=date(2026, 8, 12), session=backend, capture_root=tmp_path)
    assert frame.iloc[0].to_dict() == {"date": "2026-08-12", "dgs2": 3.7}
    assert backend.kwargs["params"] == {"id": "DGS2", "cosd": "2026-08-01", "coed": "2026-08-12"}
    call = json.loads(next(tmp_path.rglob("call.json")).read_text())
    assert call["request_parameters"] == {"coed": "2026-08-12", "cosd": "2026-08-01", "id": "DGS2"}


def test_revision_report_is_explicit_per_series_and_detects_finite_to_null():
    old = pd.DataFrame({"date": ["2026-08-10", "2026-08-11"], "x": [1.0, 2.0]})
    new = pd.DataFrame({"date": ["2026-08-10", "2026-08-11", "2026-08-12"], "x": [1.5, None, 3.0]})
    report = _series_revision(old, new, item="X", phase="fred_fx")
    assert report["revised_finite_cells"] == 1
    assert report["finite_to_null_cells"] == 1
    assert report["inserted_rows"] == 1
    assert report["source_omitted_existing_dates"] == 0


def _root(path: Path, body: bytes) -> None:
    target = path / "year=2026" / "data.parquet"
    target.parent.mkdir(parents=True)
    target.write_bytes(body)


def test_whole_root_replacement_installs_copy_and_preserves_candidate(tmp_path):
    source, target = tmp_path / "candidate", tmp_path / "production"
    _root(source, b"new"); _root(target, b"old")
    _replace_roots_atomically([(source, target)])
    assert (target / "year=2026/data.parquet").read_bytes() == b"new"
    assert (source / "year=2026/data.parquet").read_bytes() == b"new"


def test_whole_root_replacement_rolls_back_when_state_finalize_fails(tmp_path):
    source, target = tmp_path / "candidate", tmp_path / "production"
    source_state, target_state = tmp_path / "candidate.json", tmp_path / "production.json"
    _root(source, b"new"); _root(target, b"old")
    source_state.write_bytes(b"new-state"); target_state.write_bytes(b"old-state")
    with pytest.raises(RuntimeError, match="state"):
        _replace_roots_atomically(
            [(source, target), (source_state, target_state)],
            finalize=lambda: (_ for _ in ()).throw(RuntimeError("state"))
        )
    assert (target / "year=2026/data.parquet").read_bytes() == b"old"
    assert target_state.read_bytes() == b"old-state"


def test_crash_journal_recovery_rolls_back_incomplete_swap(tmp_path):
    source, target = tmp_path / "candidate", tmp_path / "production"
    _root(source, b"new"); _root(target, b"old")
    run = tmp_path / "run"; run.mkdir()
    stage = target.parent / f".{target.name}.refresh-{run.name}-0.stage"
    backup = target.parent / f".{target.name}.refresh-{run.name}-0.backup"
    _root(stage, b"new"); target.replace(backup); stage.replace(target)
    journal = run / "promotion_transaction.json"
    journal.write_text(json.dumps({"version": 1, "status": "PREPARED", "replacements": [{
        "source": str(source), "target": str(target), "stage": str(stage),
        "backup": str(backup), "original_exists": True,
    }]}))
    _recover_transaction(journal, committed=False, allowed_targets={target})
    assert (target / "year=2026/data.parquet").read_bytes() == b"old"
    assert json.loads(journal.read_text())["status"] == "ROLLED_BACK_RECOVERED"


def test_path_gate_rejects_lexical_escape(tmp_path):
    with pytest.raises(RefreshError, match="escapes"):
        _assert_plain_path(tmp_path, tmp_path / "inside" / ".." / ".." / "outside", must_exist=False)


def test_prepare_is_nonmutating_tamper_fails_and_offline_promotion_is_atomic(tmp_path, monkeypatch):
    root = tmp_path / "project"; root.mkdir()
    production = root / "data/normalized" / GLOBAL_INDEX_PRICE_DAILY.name
    rows = []
    for symbol, ticker in CONFIG.items():
        for observed, close in (("2026-08-01", 100.0), ("2026-08-02", 101.0)):
            rows.append({"date": observed, "symbol": symbol, "source_ticker": ticker,
                         "open": close, "high": close + 1, "low": close - 1,
                         "close": close, "volume": 10})
    existing = pd.DataFrame(rows).sort_values(["date", "symbol"]).reset_index(drop=True)
    existing["volume"] = existing.volume.astype("Int64")
    write_dataset_atomic(existing, production, GLOBAL_INDEX_PRICE_DAILY, validate_global_index)
    state = root / "data/state/global_index_price_daily.json"
    state.parent.mkdir(parents=True); state.write_text('{"dataset":"global_index_price_daily"}\n')
    before = _files_manifest(production)

    def fake_fetch(symbol, start, end, *, session, capture_root):
        params = {"period1": _epoch(start), "period2": _epoch(end + timedelta(days=1)),
                  "interval": "1d", "events": "history", "includeAdjustedClose": "false"}
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(CONFIG[symbol], safe='')}"
        response = session.get(url, params=params)
        capture_public_response(root=capture_root, provider="yahoo", operation="chart",
                                request_url=url, request_parameters={"symbol": symbol, **params}, response=response)
        selected = existing.loc[existing.symbol.eq(symbol)].copy()
        new = selected.iloc[[-1]].copy(); new["date"] = end.isoformat(); new["close"] += 1
        return pd.concat([selected, new], ignore_index=True)

    monkeypatch.setattr(refresh, "fetch_global_index", fake_fetch)
    result = refresh.prepare_phase(root, "yahoo", end=date(2026, 8, 3), session=Backend())
    assert _files_manifest(production) == before and result["normalized_mutation"] is False
    checkpoint = root / "data/state/global_current_refresh" / result["run_id"] / "checkpoint.json"
    candidate = root / result["candidate_root"]
    with pytest.raises(RefreshError, match="approval digest"):
        refresh.promote_phase(root, checkpoint, approval_digest="0" * 64)
    body = next((root / "data/landing/global_current_refresh" / result["run_id"]).rglob("response.body"))
    original_body = body.read_bytes(); body.write_bytes(b"tampered")
    with pytest.raises(RefreshError, match="body hash"):
        refresh.promote_phase(root, checkpoint, approval_digest=result["approval_digest"])
    body.write_bytes(original_body)
    call = next((root / "data/landing/global_current_refresh" / result["run_id"]).rglob("call.json"))
    original_call = call.read_bytes(); record = json.loads(original_call)
    record["request_parameters"]["symbol"] = "WRONG"
    call.write_text(json.dumps(record))
    with pytest.raises(RefreshError, match="frozen plan"):
        refresh.promote_phase(root, checkpoint, approval_digest=result["approval_digest"])
    call.write_bytes(original_call)
    tamper = candidate / "unexpected.txt"; tamper.write_text("tamper")
    with pytest.raises(RefreshError, match="topology"):
        refresh.promote_phase(root, checkpoint, approval_digest=result["approval_digest"])
    assert _files_manifest(production) == before
    tamper.unlink()
    original_state = state.read_bytes(); state.write_bytes(b'{"changed":true}\n')
    with pytest.raises(RefreshError, match="operational-state"):
        refresh.promote_phase(root, checkpoint, approval_digest=result["approval_digest"])
    state.write_bytes(original_state)
    promoted = refresh.promote_phase(root, checkpoint, approval_digest=result["approval_digest"])
    assert promoted["status"] == "PROMOTED"
    restored = read_dataset(production, GLOBAL_INDEX_PRICE_DAILY, validate_global_index)
    assert len(restored) == 9 and restored.date.max() == "2026-08-03"
    assert json.loads(state.read_text())["run_id"] == result["run_id"]


def test_cli_refuses_implicit_live_mode(tmp_path):
    import subprocess, sys
    script = Path(__file__).parents[1] / "scripts/manual/refresh_global_current.py"
    result = subprocess.run([sys.executable, str(script), "--project-root", str(tmp_path), "--phase", "yahoo", "--end", "2026-08-12"], capture_output=True, text=True)
    assert result.returncode != 0 and "Landing-only" in result.stderr
