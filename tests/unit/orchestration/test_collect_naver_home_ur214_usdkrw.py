from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.manual.collect.collect_naver_home_ur214_usdkrw import run
from stock_data.orchestration.naver_mobile_home_ur214_window import (
    STATE_PATH, WINDOW_ID, collector, ensure_manifest, selected_boundary,
)
from stock_data.orchestration.naver_mobile_home_windowed_current import NaverMobileHomeWindowedCollector
from stock_data.providers.naver_mobile_home_observation import route_for
from tests.unit.providers.test_naver_mobile_home_observation import HTML


NOW = datetime(2026, 8, 21, 10, 0, tzinfo=timezone.utc)
FRESH_FX_HTML = HTML.replace(b"14:12", b"18:59")


class Response:
    def __init__(self, status_code: int, content: bytes = b"") -> None:
        self.status_code = status_code
        self.content = content


def _seed_prior_fx(root: Path) -> bytes:
    seed = NaverMobileHomeWindowedCollector(
        root,
        operation_id="TEST_UR214_SEED",
        state_path=Path("data/state/test_ur214_seed.json"),
        landing_root=Path("data/landing/test_ur214_seed"),
        projection_cids=("FX_USDKRW",),
    )
    result = seed.run(
        now=NOW,
        response_factory=lambda: Response(200, FRESH_FX_HTML),
        allowed_window_ids=(WINDOW_ID,),
    )
    assert result.status == "COMPLETE" and result.accepted_cids == ("FX_USDKRW",)
    return (root / "data/state/current_observations/naver_mobile_home_current.json").read_bytes()


def test_cli_missing_or_prewindow_manifest_is_api_zero_without_callback(tmp_path) -> None:
    calls: list[object] = []
    assert run(tmp_path, now=NOW, get=lambda *args, **kwargs: calls.append((args, kwargs))) ["raw_gets"] == 0
    ensure_manifest(tmp_path)
    prewindow = datetime(2026, 8, 21, 9, 59, tzinfo=timezone.utc)
    assert selected_boundary(tmp_path, now=prewindow) is None
    assert run(tmp_path, now=prewindow, get=lambda *args, **kwargs: calls.append((args, kwargs))) ["raw_gets"] == 0
    assert calls == []


def test_cli_manifest_uses_only_half_open_1900_boundary(tmp_path) -> None:
    ensure_manifest(tmp_path)
    assert selected_boundary(tmp_path, now=NOW) == WINDOW_ID
    assert selected_boundary(tmp_path, now=datetime(2026, 8, 21, 10, 29, 59, tzinfo=timezone.utc)) == WINDOW_ID
    assert selected_boundary(tmp_path, now=datetime(2026, 8, 21, 10, 30, tzinfo=timezone.utc)) is None


def test_cli_absent_ledger_is_eligible_and_projects_only_fresh_fx(tmp_path) -> None:
    ensure_manifest(tmp_path)
    calls: list[object] = []
    result = run(
        tmp_path,
        now=NOW,
        get=lambda *args, **kwargs: calls.append((args, kwargs)) or Response(200, FRESH_FX_HTML),
    )
    assert result["selected_boundary"] == WINDOW_ID
    assert result["status"] == "COMPLETE"
    assert result["raw_gets"] == 1 and result["replay_api_calls"] == 0
    assert len(calls) == 1
    observation = collector(tmp_path).store.select(route_for("FX_USDKRW"))
    assert observation is not None and observation.unit == "KRW per USD"
    assert observation.value == 1381.7
    payload = json.loads((tmp_path / "data/state/current_observations/naver_mobile_home_current.json").read_text(encoding="utf-8"))
    assert [row["identity"]["symbol"] for row in payload["observations"]] == ["USD_KRW"]


def test_cli_malformed_attempting_or_terminal_state_never_invokes_callback(tmp_path) -> None:
    ensure_manifest(tmp_path)
    for label, payload in (
        ("malformed", "{not-json"),
        ("attempting", {"schema_version": 1, "operation_id": "UR-214", "windows": {WINDOW_ID: {"status": "ATTEMPTING"}}}),
        ("terminal", {"schema_version": 1, "operation_id": "UR-214", "windows": {WINDOW_ID: {"status": "COMPLETE_FAILURE"}}}),
    ):
        root = tmp_path / label
        ensure_manifest(root)
        path = root / STATE_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
        calls: list[object] = []
        result = run(root, now=NOW, get=lambda *args, **kwargs: calls.append((args, kwargs)))
        assert result["status"] == "PREFLIGHT_API_ZERO" and result["raw_gets"] == 0
        assert calls == []


def test_cli_http_failure_is_terminal_and_preserves_prior_bytes(tmp_path) -> None:
    ensure_manifest(tmp_path)
    prior = _seed_prior_fx(tmp_path)
    calls: list[object] = []
    result = run(
        tmp_path,
        now=NOW,
        get=lambda *args, **kwargs: calls.append((args, kwargs)) or Response(503),
    )
    assert result["status"] == "COMPLETE_FAILURE" and result["raw_gets"] == 1
    assert len(calls) == 1
    assert (tmp_path / "data/state/current_observations/naver_mobile_home_current.json").read_bytes() == prior
    state = json.loads((tmp_path / STATE_PATH).read_text(encoding="utf-8"))
    assert state["windows"][WINDOW_ID]["failure_type"] == "HTTP_STATUS"
