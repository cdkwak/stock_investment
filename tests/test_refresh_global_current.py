from datetime import date
import json
from pathlib import Path

import pandas as pd
import pytest

from scripts.manual.refresh_global_current import (
    BudgetSession, RefreshError, _finite_latest, _replace_roots_atomically,
    _revision_report,
)
from stock_data.providers.fred import fetch_series


class Response:
    status_code = 200
    content = b"DATE,DGS2\n2026-08-12,3.70\n"
    text = content.decode()
    headers = {"Content-Type": "text/csv"}
    def raise_for_status(self): pass


class Backend:
    def __init__(self): self.kwargs = None
    def get(self, *args, **kwargs): self.kwargs = kwargs; return Response()


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


def test_revision_report_is_explicit():
    old = pd.DataFrame({"date": ["2026-08-10", "2026-08-11"], "x": [1.0, 2.0]})
    new = pd.DataFrame({"date": ["2026-08-10", "2026-08-11", "2026-08-12"], "x": [1.5, 2.0, 3.0]})
    assert _revision_report(old, new, ["date"]) == {
        "overlap_rows": 2, "revised_cells": 1, "inserted_rows": 1,
        "source_omitted_existing_keys_within_response_range": 0,
    }


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


def test_cli_refuses_implicit_live_mode(tmp_path):
    import subprocess, sys
    script = Path(__file__).parents[1] / "scripts/manual/refresh_global_current.py"
    result = subprocess.run([sys.executable, str(script), "--project-root", str(tmp_path), "--phase", "yahoo", "--end", "2026-08-12"], capture_output=True, text=True)
    assert result.returncode != 0 and "Landing-only" in result.stderr
