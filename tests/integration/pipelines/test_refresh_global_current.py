from datetime import date, timedelta
import json
from pathlib import Path

import pandas as pd
import pytest
from urllib.parse import quote

import scripts.manual.collect.refresh_global_current as refresh
from scripts.manual.collect.refresh_global_current import (
    BudgetSession, RefreshError, _files_manifest, _finite_latest, _replace_roots_atomically,
    _artifact_fingerprint, _assert_plain_path, _recover_transaction, _series_revision,
)
from stock_data.contracts.global_market import (
    EndpointWindowPolicy, GLOBAL_INDEX_PRICE_DAILY, global_index_endpoint_window,
)
from stock_data.contracts.global_market import (
    FRED_TREASURY_YIELD_DAILY, FRED_VIX_DAILY, US_TREASURY_SPREAD_DAILY,
)
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


def test_revision_report_detects_planned_overlap_omitted_before_response_start():
    old = pd.DataFrame({"date": ["2026-08-01", "2026-08-02"], "x": [1.0, 2.0]})
    new = pd.DataFrame({"date": ["2026-08-02", "2026-08-03"], "x": [2.0, 3.0]})
    report = _series_revision(old, new, item="X", phase="fred_fx",
                              planned_start="2026-08-01", planned_end="2026-08-03")
    assert report["source_omitted_existing_dates"] == 1


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
        "source_fingerprint": _artifact_fingerprint(source),
        "pre_target_fingerprint": _artifact_fingerprint(backup),
    }]}))
    _recover_transaction(journal, committed=False, allowed_pairs=[(source, target)], project_root=tmp_path)
    assert (target / "year=2026/data.parquet").read_bytes() == b"old"
    assert json.loads(journal.read_text())["status"] == "ROLLED_BACK_RECOVERED"


def test_committed_recovery_reconstructs_missing_target_before_backup_cleanup(tmp_path):
    source, target = tmp_path / "candidate", tmp_path / "production"
    _root(source, b"new")
    run = tmp_path / "run"; run.mkdir()
    stage = target.parent / f".{target.name}.refresh-{run.name}-0.stage"
    backup = target.parent / f".{target.name}.refresh-{run.name}-0.backup"
    _root(backup, b"old")
    journal = run / "promotion_transaction.json"
    journal.write_text(json.dumps({"version": 1, "status": "COMMITTED", "replacements": [{
        "source": str(source), "target": str(target), "stage": str(stage),
        "backup": str(backup), "original_exists": True,
        "source_fingerprint": _artifact_fingerprint(source),
        "pre_target_fingerprint": _artifact_fingerprint(backup),
    }]}))
    _recover_transaction(journal, committed=True, allowed_pairs=[(source, target)], project_root=tmp_path)
    assert (target / "year=2026/data.parquet").read_bytes() == b"new"
    assert not backup.exists()


def test_committed_recovery_refuses_to_delete_last_unverified_backup(tmp_path):
    source, target = tmp_path / "missing-source", tmp_path / "production"
    run = tmp_path / "run"; run.mkdir()
    stage = target.parent / f".{target.name}.refresh-{run.name}-0.stage"
    backup = target.parent / f".{target.name}.refresh-{run.name}-0.backup"
    _root(backup, b"old")
    journal = run / "promotion_transaction.json"
    journal.write_text(json.dumps({"version": 1, "status": "COMMITTED", "replacements": [{
        "source": str(source), "target": str(target), "stage": str(stage),
        "backup": str(backup), "original_exists": True,
        "source_fingerprint": {"kind": "directory", "value": {"not": "available"}},
        "pre_target_fingerprint": _artifact_fingerprint(backup),
    }]}))
    with pytest.raises(RefreshError, match="no verified canonical"):
        _recover_transaction(journal, committed=True, allowed_pairs=[(source, target)], project_root=tmp_path)
    assert backup.exists()


def test_uncommitted_recovery_prefers_verified_target_over_corrupt_backup(tmp_path):
    source, target = tmp_path / "candidate", tmp_path / "production"
    _root(source, b"new"); _root(target, b"old")
    expected = _artifact_fingerprint(target)
    run = tmp_path / "run"; run.mkdir()
    stage = target.parent / f".{target.name}.refresh-{run.name}-0.stage"
    backup = target.parent / f".{target.name}.refresh-{run.name}-0.backup"
    _root(backup, b"corrupt")
    journal = run / "promotion_transaction.json"
    journal.write_text(json.dumps({"version": 1, "status": "PREPARED", "replacements": [{
        "source": str(source), "target": str(target), "stage": str(stage),
        "backup": str(backup), "original_exists": True,
        "source_fingerprint": _artifact_fingerprint(source), "pre_target_fingerprint": expected,
    }]}))
    _recover_transaction(journal, committed=False, allowed_pairs=[(source, target)], project_root=tmp_path)
    assert _artifact_fingerprint(target) == expected


def test_recovery_rejects_swapped_ordered_source_target_pairs_without_mutation(tmp_path):
    source1, source2 = tmp_path / "candidate1", tmp_path / "candidate2"
    target1, target2 = tmp_path / "production1", tmp_path / "production2"
    for path, body in ((source1, b"new1"), (source2, b"new2"), (target1, b"old1"), (target2, b"old2")):
        _root(path, body)
    before = (_artifact_fingerprint(target1), _artifact_fingerprint(target2))
    run = tmp_path / "run"; run.mkdir(); entries = []
    for number, (source, target) in enumerate(((source2, target1), (source1, target2))):
        entries.append({"source": str(source), "target": str(target),
                        "stage": str(target.parent / f".{target.name}.refresh-{run.name}-{number}.stage"),
                        "backup": str(target.parent / f".{target.name}.refresh-{run.name}-{number}.backup"),
                        "original_exists": True, "source_fingerprint": _artifact_fingerprint(source),
                        "pre_target_fingerprint": _artifact_fingerprint(target)})
    journal = run / "promotion_transaction.json"; journal.write_text(json.dumps({"version": 1, "status": "PREPARED", "replacements": entries}))
    with pytest.raises(RefreshError, match="ordered source/target"):
        _recover_transaction(journal, committed=False, allowed_pairs=[(source1, target1), (source2, target2)], project_root=tmp_path)
    assert (_artifact_fingerprint(target1), _artifact_fingerprint(target2)) == before


def test_path_gate_rejects_lexical_escape(tmp_path):
    with pytest.raises(RefreshError, match="escapes"):
        _assert_plain_path(tmp_path, tmp_path / "inside" / ".." / ".." / "outside", must_exist=False)


def test_file_fingerprint_rejects_symlink_or_reparse(tmp_path):
    target = tmp_path / "target.json"; target.write_text("{}")
    link = tmp_path / "link.json"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation unavailable")
    target.unlink()
    with pytest.raises(RefreshError, match="links/reparse"):
        refresh._file_fingerprint(link)


def test_prepare_is_nonmutating_tamper_fails_and_offline_promotion_is_atomic(tmp_path, monkeypatch):
    root = tmp_path / "project"; root.mkdir()
    production = root / "data/normalized" / GLOBAL_INDEX_PRICE_DAILY.name
    rows = []
    for symbol, ticker in CONFIG.items():
        for observed, close in (("2026-07-23", 100.0), ("2026-08-02", 101.0)):
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
    with pytest.raises(RefreshError, match="approval/call/plan"):
        refresh.promote_phase(root, checkpoint, approval_digest="0" * 64)
    body = next((root / "data/landing/global_current_refresh" / result["run_id"]).rglob("response.body"))
    original_body = body.read_bytes(); body.write_bytes(b"tampered")
    with pytest.raises(RefreshError, match="body hash"):
        refresh.promote_phase(root, checkpoint, approval_digest=result["approval_digest"])
    body.write_bytes(original_body)
    rogue = body.parents[3] / "rogue.txt"; rogue.write_text("rogue")
    with pytest.raises(RefreshError, match="unexpected topology"):
        refresh.promote_phase(root, checkpoint, approval_digest=result["approval_digest"])
    rogue.unlink()
    call = next((root / "data/landing/global_current_refresh" / result["run_id"]).rglob("call.json"))
    original_call = call.read_bytes(); record = json.loads(original_call)
    record["request_parameters"]["symbol"] = "WRONG"
    call.write_text(json.dumps(record))
    with pytest.raises(RefreshError, match="frozen plan"):
        refresh.promote_phase(root, checkpoint, approval_digest=result["approval_digest"])
    call.write_bytes(original_call)
    record = json.loads(original_call); record.pop("response_content_type")
    call.write_text(json.dumps(record))
    with pytest.raises(RefreshError, match="schema/value"):
        refresh.promote_phase(root, checkpoint, approval_digest=result["approval_digest"])
    call.write_bytes(original_call)
    tamper = candidate / "unexpected.txt"; tamper.write_text("tamper")
    with pytest.raises(RefreshError, match="topology"):
        refresh.promote_phase(root, checkpoint, approval_digest=result["approval_digest"])
    assert _files_manifest(production) == before
    tamper.unlink()
    original_state = state.read_bytes(); state.write_bytes(b'{"changed":true}\n')
    with pytest.raises(RefreshError, match="CAS/input"):
        refresh.promote_phase(root, checkpoint, approval_digest=result["approval_digest"])
    state.write_bytes(original_state)
    lock = root / "data/state/global_current_refresh.lock"; lock.write_text("other-run")
    with pytest.raises(RefreshError, match="lock"):
        refresh.promote_phase(root, checkpoint, approval_digest=result["approval_digest"])
    assert lock.read_text() == "other-run" and _files_manifest(production) == before
    lock.unlink()
    promoted = refresh.promote_phase(root, checkpoint, approval_digest=result["approval_digest"])
    assert promoted["status"] == "PROMOTED"
    restored = read_dataset(production, GLOBAL_INDEX_PRICE_DAILY, validate_global_index)
    assert len(restored) == len(CONFIG) * 3 and restored.date.max() == "2026-08-03"
    assert json.loads(state.read_text())["run_id"] == result["run_id"]


def test_provider_native_index_accepts_shifted_endpoints_and_records_coverage(tmp_path, monkeypatch):
    root = tmp_path / "provider-native"; root.mkdir()
    production = root / "data/normalized" / GLOBAL_INDEX_PRICE_DAILY.name
    existing = pd.DataFrame([{
        "date": "2026-08-05", "symbol": "DOLLAR_INDEX", "source_ticker": "DX-Y.NYB",
        "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 10,
    }])
    existing["volume"] = existing.volume.astype("Int64")
    write_dataset_atomic(existing, production, GLOBAL_INDEX_PRICE_DAILY, validate_global_index)

    def shifted_fetch(symbol, start, end, *, session, capture_root):
        params = {"period1": _epoch(start), "period2": _epoch(end + timedelta(days=1)),
                  "interval": "1d", "events": "history", "includeAdjustedClose": "false"}
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(CONFIG[symbol], safe='')}"
        response = session.get(url, params=params)
        capture_public_response(root=capture_root, provider="yahoo", operation="chart",
                                request_url=url, request_parameters={"symbol": symbol, **params}, response=response)
        return pd.DataFrame([
            {"date": observed, "symbol": symbol, "source_ticker": CONFIG[symbol],
             "open": close, "high": close + 1, "low": close - 1,
             "close": close, "volume": 10}
            for observed, close in (("2026-08-05", 101.0), ("2026-08-12", 102.0))
        ]).astype({"volume": "Int64"})

    monkeypatch.setattr(refresh, "fetch_global_index", shifted_fetch)
    result = refresh.prepare_phase(
        root, "yahoo", symbols=("DOLLAR_INDEX",),
        start=date(2026, 8, 3), end=date(2026, 8, 14), session=Backend(),
    )
    assert global_index_endpoint_window("DOLLAR_INDEX") is EndpointWindowPolicy.PROVIDER_NATIVE
    assert result["coverage"]["DOLLAR_INDEX"] == {
        "planned_start": "2026-08-03", "planned_end": "2026-08-14",
        "observed_start": "2026-08-05", "observed_end": "2026-08-12",
        "coverage_first": "2026-08-05", "coverage_last": "2026-08-12",
        "coverage_policy": "provider_native",
    }
    state = json.loads((root / result["candidate_operational_state"]).read_text())
    assert state["coverage"] == result["coverage"]


def test_strict_index_still_rejects_shifted_endpoint_window(tmp_path, monkeypatch):
    root = tmp_path / "strict"; root.mkdir()
    production = root / "data/normalized" / GLOBAL_INDEX_PRICE_DAILY.name
    existing = pd.DataFrame([{
        "date": "2026-08-05", "symbol": "SP500", "source_ticker": "^GSPC",
        "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 10,
    }])
    existing["volume"] = existing.volume.astype("Int64")
    write_dataset_atomic(existing, production, GLOBAL_INDEX_PRICE_DAILY, validate_global_index)

    def shifted_fetch(symbol, start, end, *, session, capture_root):
        params = {"period1": _epoch(start), "period2": _epoch(end + timedelta(days=1)),
                  "interval": "1d", "events": "history", "includeAdjustedClose": "false"}
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(CONFIG[symbol], safe='')}"
        response = session.get(url, params=params)
        capture_public_response(root=capture_root, provider="yahoo", operation="chart",
                                request_url=url, request_parameters={"symbol": symbol, **params}, response=response)
        return pd.DataFrame([
            {"date": observed, "symbol": symbol, "source_ticker": CONFIG[symbol],
             "open": close, "high": close + 1, "low": close - 1,
             "close": close, "volume": 10}
            for observed, close in (("2026-08-05", 100.0), ("2026-08-12", 102.0))
        ]).astype({"volume": "Int64"})

    monkeypatch.setattr(refresh, "fetch_global_index", shifted_fetch)
    assert global_index_endpoint_window("SP500") is EndpointWindowPolicy.STRICT_EXCHANGE
    with pytest.raises(RefreshError, match="strict planned endpoint window"):
        refresh.prepare_phase(
            root, "yahoo", symbols=("SP500",),
            start=date(2026, 8, 3), end=date(2026, 8, 14), session=Backend(),
        )


def test_fred_yields_end_to_end_promotes_yields_spread_and_both_states(tmp_path, monkeypatch):
    root = tmp_path / "project"; root.mkdir()
    production = root / "data/normalized/fred_treasury_yield_daily"
    existing = pd.DataFrame({"date": ["2026-08-01", "2026-08-02"],
                             "dgs2": [3.0, 3.1], "dgs10": [4.0, 4.1], "dgs30": [4.5, 4.6]})
    write_dataset_atomic(existing, production, FRED_TREASURY_YIELD_DAILY, refresh.validate_fred)
    state = root / "data/state/fred_treasury_yield_daily.json"
    state.parent.mkdir(parents=True); state.write_text('{"dataset":"fred_treasury_yield_daily"}\n')
    spread = root / "data/derived/us_treasury_spread_daily"
    refresh._build_spread_candidate(existing, spread)
    spread_state = root / "data/state/us_treasury_spread_daily.json"
    spread_state.write_text('{"dataset":"us_treasury_spread_daily"}\n')

    def fake_fred(item, start, *, end, session, capture_root):
        params = {"id": item, "cosd": start.isoformat(), "coed": end.isoformat()}
        response = session.get("https://fred.stlouisfed.org/graph/fredgraph.csv", params=params)
        capture_public_response(root=capture_root, provider="fred", operation="fredgraph_csv",
                                request_url="https://fred.stlouisfed.org/graph/fredgraph.csv",
                                request_parameters=params, response=response)
        column = item.lower(); base = {"dgs2": 3.0, "dgs10": 4.0, "dgs30": 4.5}[column]
        return pd.DataFrame({"date": [start.isoformat(), "2026-08-01", "2026-08-02", end.isoformat()],
                             column: [base - .1, base, base + .1, base + .2]})

    monkeypatch.setattr(refresh, "fetch_series", fake_fred)
    result = refresh.prepare_phase(root, "fred_yields", end=date(2026, 8, 3), session=Backend())
    checkpoint = root / "data/state/global_current_refresh" / result["run_id"] / "checkpoint.json"
    promoted = refresh.promote_phase(root, checkpoint, approval_digest=result["approval_digest"])
    assert promoted["status"] == "PROMOTED"
    yields = read_dataset(production, FRED_TREASURY_YIELD_DAILY, refresh.validate_fred)
    assert yields.date.max() == "2026-08-03"
    assert _files_manifest(spread)["rows"] == len(yields)
    assert json.loads(state.read_text())["run_id"] == result["run_id"]
    assert json.loads(spread_state.read_text())["run_id"] == result["run_id"]


def test_fred_vix_uses_bounded_capture_candidate_and_offline_promotion(tmp_path, monkeypatch):
    root = tmp_path / "project"; root.mkdir()
    production = root / "data/normalized/fred_vix_daily"
    existing = pd.DataFrame({"date": ["2026-08-01", "2026-08-02"], "vixcls": [16.0, 17.0]})
    write_dataset_atomic(existing, production, FRED_VIX_DAILY, refresh.validate_fred)
    state = root / "data/state/fred_vix_daily.json"
    state.parent.mkdir(parents=True); state.write_text('{"dataset":"fred_vix_daily"}\n')
    before = _files_manifest(production)

    def fake_fred(item, start, *, end, session, capture_root):
        assert item == "VIXCLS"
        params = {"id": item, "cosd": start.isoformat(), "coed": end.isoformat()}
        response = session.get("https://fred.stlouisfed.org/graph/fredgraph.csv", params=params)
        capture_public_response(
            root=capture_root, provider="fred", operation="fredgraph_csv",
            request_url="https://fred.stlouisfed.org/graph/fredgraph.csv",
            request_parameters=params, response=response,
        )
        return pd.DataFrame({
            "date": [start.isoformat(), "2026-08-01", "2026-08-02", end.isoformat()],
            "vixcls": [15.5, 16.0, 17.0, 18.0],
        })

    monkeypatch.setattr(refresh, "fetch_series", fake_fred)
    result = refresh.prepare_phase(root, "fred_vix", end=date(2026, 8, 3), session=Backend())
    assert result["http_calls"] == 1 and result["normalized_mutation"] is False
    assert result["as_of_observations"] == [{
        "series_id": "VIXCLS", "observation_date": "2026-08-03", "value": 18.0,
        "retrieved_at": result["as_of_observations"][0]["retrieved_at"],
        "realtime_start": None, "realtime_end": None, "series_last_updated": None,
        "vintage_metadata_status": "UNAVAILABLE_FROM_FREDGRAPH_CSV",
        "source": "FRED fredgraph.csv", "operational_status": "CURRENT_AS_RETRIEVED",
        "predictive_status": "PIT_BLOCKED_PENDING_VINTAGE_RESOLVER",
    }]
    assert _files_manifest(production) == before
    checkpoint = root / "data/state/global_current_refresh" / result["run_id"] / "checkpoint.json"
    promoted = refresh.promote_phase(root, checkpoint, approval_digest=result["approval_digest"])
    assert promoted["status"] == "PROMOTED"
    restored = read_dataset(production, FRED_VIX_DAILY, refresh.validate_fred)
    assert restored.date.max() == "2026-08-03" and restored.vixcls.iloc[-1] == 18.0
    assert json.loads(state.read_text())["run_id"] == result["run_id"]

    class MustNotCall:
        def get(self, *args, **kwargs):
            raise AssertionError("same-end FRED rerun must stop before provider access")

    rerun = refresh.prepare_phase(
        root, "fred_vix", end=date(2026, 8, 3), session=MustNotCall(),
    )
    assert rerun["status"] == "NOOP_IDEMPOTENT"
    assert rerun["http_calls"] == 0
    assert rerun["normalized_mutation"] is False
    assert rerun["pre_dataset"] == _files_manifest(production)
    assert rerun["retained_run_id"] == result["run_id"]


def test_fred_vix_schema_failure_uses_exact_fdr_fallback_then_api_zero_replay(
    tmp_path, monkeypatch,
):
    root = tmp_path / "project"; root.mkdir()
    production = root / "data/normalized/fred_vix_daily"
    existing = pd.DataFrame({
        "date": ["2026-08-01", "2026-08-02"],
        "vixcls": [16.0, 17.0],
    })
    write_dataset_atomic(existing, production, FRED_VIX_DAILY, refresh.validate_fred)
    state = root / "data/state/fred_vix_daily.json"
    state.parent.mkdir(parents=True); state.write_text('{"dataset":"fred_vix_daily"}\n')
    before = _files_manifest(production)

    class VixResponse(Response):
        content = (
            b"observation_date,VIXCLS\n"
            b"2026-07-23,15.5\n2026-08-01,16.0\n"
            b"2026-08-02,17.0\n2026-08-03,18.0\n"
        )
        text = content.decode()
        headers = {
            "Content-Type": "text/csv",
            "content-disposition": 'attachment; filename="VIXCLS.csv"',
        }

    class ThreeCallBackend:
        def __init__(self): self.calls = 0
        def get(self, *args, **kwargs):
            self.calls += 1
            return Response() if self.calls == 1 else VixResponse()

    backend = ThreeCallBackend()

    def malformed_primary(item, start, *, end, session, capture_root):
        params = {"id": item, "cosd": start.isoformat(), "coed": end.isoformat()}
        response = session.get(refresh.FRED_URL, params=params)
        capture_public_response(
            root=capture_root, provider="fred", operation="fredgraph_csv",
            request_url=refresh.FRED_URL, request_parameters=params,
            response=response,
        )
        raise RuntimeError("synthetic malformed primary schema")

    monkeypatch.setattr(refresh, "fetch_series", malformed_primary)
    result = refresh.prepare_phase(
        root, "fred_vix", end=date(2026, 8, 3), session=backend,
    )
    assert backend.calls == 3
    assert result["http_calls"] == 3
    assert result["http_statuses"] == [200, 200, 200]
    assert len(result["landing_captures"]) == 3
    assert result["fallback_decision"]["outcome"] == "FALLBACK_ACCEPTED"
    assert result["fallback_decision"]["primary_requests"] == 1
    assert result["fallback_decision"]["fallback_requests"] == 2
    assert result["fallback_decision"]["selected_provenance"]["provider"] == "financedatareader"
    assert result["fallback_decision"]["selected_provenance"]["upstream_provider"] == "FRED"
    assert _files_manifest(production) == before

    checkpoint = root / "data/state/global_current_refresh" / result["run_id"] / "checkpoint.json"
    promoted = refresh.promote_phase(
        root, checkpoint, approval_digest=result["approval_digest"],
    )
    assert promoted["status"] == "PROMOTED"
    restored = read_dataset(production, FRED_VIX_DAILY, refresh.validate_fred)
    assert restored.date.max() == "2026-08-03"
    assert restored.vixcls.iloc[-1] == 18.0
    circuit = json.loads((
        root / "data/state/automatic_fallback/fred_vix_daily_vixcls.json"
    ).read_text())
    assert circuit["is_open"] is False

    class MustNotCall:
        def get(self, *args, **kwargs):
            raise AssertionError("accepted fallback replay must be API zero")

    replay = refresh.prepare_phase(
        root, "fred_vix", end=date(2026, 8, 3), session=MustNotCall(),
    )
    assert replay["status"] == "NOOP_IDEMPOTENT"
    assert replay["http_calls"] == 0


def _stopped_yields_fixture(root: Path, endpoints=None):
    endpoints = endpoints or {item: "2026-08-02" for item in ("DGS2", "DGS10", "DGS30")}
    production = root / "data/normalized/fred_treasury_yield_daily"
    existing = pd.DataFrame({"date": ["2026-08-01"], "dgs2": [3.0], "dgs10": [4.0], "dgs30": [4.5]})
    write_dataset_atomic(existing, production, FRED_TREASURY_YIELD_DAILY, refresh.validate_fred)
    state = root / "data/state/fred_treasury_yield_daily.json"; state.parent.mkdir(parents=True)
    state.write_text('{"dataset":"fred_treasury_yield_daily"}\n')
    spread = root / "data/derived/us_treasury_spread_daily"; refresh._build_spread_candidate(existing, spread)
    (root / "data/state/us_treasury_spread_daily.json").write_text('{"dataset":"us_treasury_spread_daily"}\n')
    run = "20260813T000000Z_" + "a" * 32
    checkpoint = root / "data/state/global_current_refresh" / run / "checkpoint.json"
    landing = root / "data/landing/global_current_refresh" / run
    plan = []
    for item in ("DGS2", "DGS10", "DGS30"):
        plan.append({"item": item, "start": "2026-08-01", "end": "2026-08-03"})
        body = f"DATE,{item}\n2026-08-01,3.0\n{endpoints[item]},3.1\n".encode()
        response = type("R", (), {"status_code": 200, "content": body, "headers": {"Content-Type": "text/csv"}})()
        capture_public_response(root=landing, provider="fred", operation="fredgraph_csv",
                                request_url="https://fred.stlouisfed.org/graph/fredgraph.csv",
                                request_parameters={"id": item, "cosd": "2026-08-01", "coed": "2026-08-03"}, response=response)
    payload = {"version": 2, "run_id": run, "phase": "fred_yields", "status": "STOPPED",
               "max_http_calls": 3, "http_calls": 3, "http_statuses": [200, 200, 200],
               "retry_count": 0, "frozen_plan": plan, "pre_dataset": _files_manifest(production),
               "normalized_mutation": False, "error_type": "RefreshError"}
    refresh._atomic_json(checkpoint, payload)
    return checkpoint


def test_offline_stopped_yields_adoption_builds_candidate_without_network(tmp_path):
    root = tmp_path / "project"; root.mkdir(); checkpoint = _stopped_yields_fixture(root)
    result = refresh.adopt_stopped_fred_yields(root, checkpoint,
        accepted_observed_end=date(2026, 8, 2), confirm_requested_end=date(2026, 8, 3))
    assert result["status"] == "CANDIDATE_REVIEW_REQUIRED"
    assert result["requested_end"] == "2026-08-03" and result["accepted_observed_end"] == "2026-08-02"
    assert {row["series_id"] for row in result["as_of_observations"]} == {"DGS2", "DGS10", "DGS30"}
    assert all(row["predictive_status"] == "PIT_BLOCKED_PENDING_VINTAGE_RESOLVER"
               for row in result["as_of_observations"])
    assert result["approval_digest"] == refresh._approval_digest(result)
    assert _files_manifest(root / result["candidate_root"])["rows"] == 2
    assert _files_manifest(root / "data/normalized/fred_treasury_yield_daily")["rows"] == 1


def test_offline_adoption_rejects_relabel_tamper_and_unequal_endpoints(tmp_path):
    root = tmp_path / "project"; root.mkdir()
    checkpoint = _stopped_yields_fixture(root, {"DGS2": "2026-08-02", "DGS10": "2026-08-03", "DGS30": "2026-08-02"})
    with pytest.raises(RefreshError, match="endpoints"):
        refresh.adopt_stopped_fred_yields(root, checkpoint,
            accepted_observed_end=date(2026, 8, 2), confirm_requested_end=date(2026, 8, 3))
    root2 = tmp_path / "project2"; root2.mkdir(); checkpoint2 = _stopped_yields_fixture(root2)
    call = next((root2 / "data/landing/global_current_refresh").rglob("call.json"))
    record = json.loads(call.read_text()); record["request_parameters"]["id"] = "DGS30"; call.write_text(json.dumps(record))
    with pytest.raises(RefreshError, match="frozen plan|count"):
        refresh.adopt_stopped_fred_yields(root2, checkpoint2,
            accepted_observed_end=date(2026, 8, 2), confirm_requested_end=date(2026, 8, 3))


def _stopped_fx_fixture(root: Path, endpoints=None):
    items = ("DEXKOUS", "DEXJPUS"); endpoints = endpoints or {item: "2026-08-02" for item in items}
    production = root / "data/normalized/fred_usd_fx_daily"
    existing = pd.DataFrame({"date": ["2026-08-01"], "dexkous": [1400.0], "dexjpus": [150.0]})
    write_dataset_atomic(existing, production, refresh.FRED_USD_FX_DAILY, refresh.validate_fred)
    state = root / "data/state/fred_usd_fx_daily.json"; state.parent.mkdir(parents=True)
    state.write_text('{"dataset":"fred_usd_fx_daily"}\n')
    run = "20260813T010000Z_" + "b" * 32
    checkpoint = root / "data/state/global_current_refresh" / run / "checkpoint.json"
    landing = root / "data/landing/global_current_refresh" / run
    plan = []
    for item in items:
        plan.append({"item": item, "start": "2026-08-01", "end": "2026-08-03"})
        body = f"observation_date,{item}\n2026-08-01,1.0\n{endpoints[item]},1.1\n".encode()
        response = type("R", (), {"status_code": 200, "content": body, "headers": {"Content-Type": "text/csv"}})()
        capture_public_response(root=landing, provider="fred", operation="fredgraph_csv",
            request_url="https://fred.stlouisfed.org/graph/fredgraph.csv",
            request_parameters={"id": item, "cosd": "2026-08-01", "coed": "2026-08-03"}, response=response)
    refresh._atomic_json(checkpoint, {"version": 2, "run_id": run, "phase": "fred_fx", "status": "STOPPED",
        "max_http_calls": 2, "http_calls": 2, "http_statuses": [200, 200], "retry_count": 0,
        "frozen_plan": plan, "pre_dataset": _files_manifest(production), "normalized_mutation": False,
        "error_type": "RefreshError"})
    return checkpoint


def test_offline_stopped_fx_adoption_and_rejection_gates(tmp_path):
    root = tmp_path / "fx"; root.mkdir(); checkpoint = _stopped_fx_fixture(root)
    result = refresh.adopt_stopped_fred_fx(root, checkpoint,
        accepted_observed_end=date(2026, 8, 2), confirm_requested_end=date(2026, 8, 3))
    assert result["status"] == "CANDIDATE_REVIEW_REQUIRED"
    assert {row["series_id"] for row in result["as_of_observations"]} == {"DEXKOUS", "DEXJPUS"}
    assert result["approval_digest"] == refresh._approval_digest(result)
    assert _files_manifest(root / result["candidate_root"])["rows"] == 2
    root2 = tmp_path / "unequal"; root2.mkdir()
    unequal = _stopped_fx_fixture(root2, {"DEXKOUS": "2026-08-02", "DEXJPUS": "2026-08-03"})
    with pytest.raises(RefreshError, match="endpoints"):
        refresh.adopt_stopped_fred_fx(root2, unequal,
            accepted_observed_end=date(2026, 8, 2), confirm_requested_end=date(2026, 8, 3))
    root3 = tmp_path / "tamper"; root3.mkdir(); tampered = _stopped_fx_fixture(root3)
    call = next((root3 / "data/landing/global_current_refresh").rglob("call.json"))
    record = json.loads(call.read_text()); record["request_parameters"]["id"] = "DEXJPUS"; call.write_text(json.dumps(record))
    with pytest.raises(RefreshError, match="frozen plan|count"):
        refresh.adopt_stopped_fred_fx(root3, tampered,
            accepted_observed_end=date(2026, 8, 2), confirm_requested_end=date(2026, 8, 3))


def test_cli_refuses_implicit_live_mode(tmp_path):
    import subprocess, sys
    script = Path(__file__).resolve().parents[3] / "scripts/manual/collect/refresh_global_current.py"
    result = subprocess.run([sys.executable, str(script), "--project-root", str(tmp_path), "--phase", "yahoo", "--end", "2026-08-12"], capture_output=True, text=True)
    assert result.returncode != 0 and "Landing-only" in result.stderr
