from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.manual.collect.collect_naver_home_ur227_usdkrw import run
from stock_data.orchestration.naver_mobile_home_ur227_window import (
    LANDING_ROOT, STATE_PATH, WINDOW_ID, collector, ensure_manifest, selected_boundary,
)
from stock_data.orchestration.naver_mobile_home_windowed_current import NaverMobileHomeWindowedCollector
from stock_data.providers.naver_mobile_home_observation import route_for
from tests.unit.providers.test_naver_mobile_home_observation import HTML


NOW = datetime(2026, 8, 21, 10, 45, tzinfo=timezone.utc)
FRESH_FX_HTML = HTML.replace(b"14:12", b"19:44")


class Response:
    def __init__(self, status_code: int, content: bytes = b"") -> None:
        self.status_code = status_code
        self.content = content


def _seed_prior_fx(root: Path) -> bytes:
    seed = NaverMobileHomeWindowedCollector(
        root, operation_id="TEST_UR227_SEED", state_path=Path("data/state/test_ur227_seed.json"),
        landing_root=Path("data/landing/test_ur227_seed"), projection_cids=("FX_USDKRW",),
        window_selector=lambda *, now: WINDOW_ID,
    )
    result = seed.run(now=NOW, response_factory=lambda: Response(200, FRESH_FX_HTML), allowed_window_ids=(WINDOW_ID,))
    assert result.status == "COMPLETE" and result.accepted_cids == ("FX_USDKRW",)
    return (root / "data/state/current_observations/naver_mobile_home_current.json").read_bytes()


def test_cli_exact_1945_half_open_boundary_is_api_zero_outside_window(tmp_path) -> None:
    calls: list[object] = []
    assert run(tmp_path, now=NOW, get=lambda *args, **kwargs: calls.append((args, kwargs)))["raw_gets"] == 0
    ensure_manifest(tmp_path)
    assert selected_boundary(tmp_path, now=datetime(2026, 8, 21, 10, 44, 59, tzinfo=timezone.utc)) is None
    assert selected_boundary(tmp_path, now=NOW) == WINDOW_ID
    assert selected_boundary(tmp_path, now=datetime(2026, 8, 21, 11, 0, tzinfo=timezone.utc)) is None
    assert selected_boundary(tmp_path, now=datetime(2026, 8, 22, 10, 45, tzinfo=timezone.utc)) is None
    assert calls == []


def test_cli_absent_ledger_durably_claims_landing_readback_and_accepts_only_fx(tmp_path) -> None:
    ensure_manifest(tmp_path)
    claimed: list[dict[str, object]] = []

    def get(*args, **kwargs):
        state = json.loads((tmp_path / STATE_PATH).read_text(encoding="utf-8"))
        claimed.append(state["windows"][WINDOW_ID])
        return Response(200, FRESH_FX_HTML)

    result = run(tmp_path, now=NOW, get=get)
    assert result == {
        "selected_boundary": WINDOW_ID, "attempted_at_utc": NOW.isoformat(),
        "status": "COMPLETE", "raw_gets": 1, "replay_api_calls": 0,
    }
    assert claimed == [{"status": "ATTEMPTING", "attempted_at_utc": NOW.isoformat(), "raw_gets_reserved": 1, "raw_gets_invoked": 1, "raw_gets_completed": 0, "retry_count": 0, "redirect_count": 0, "fallback_count": 0}]
    landing = list((tmp_path / LANDING_ROOT).rglob("response.html"))
    assert len(landing) == 1 and landing[0].read_bytes() == FRESH_FX_HTML
    observation = collector(tmp_path).store.select(route_for("FX_USDKRW"))
    assert observation is not None and observation.unit == "KRW per USD" and observation.value == 1381.7
    payload = json.loads((tmp_path / "data/state/current_observations/naver_mobile_home_current.json").read_text(encoding="utf-8"))
    assert [item["identity"]["symbol"] for item in payload["observations"]] == ["USD_KRW"]


def test_cli_malformed_attempting_terminal_and_replay_are_callback_zero(tmp_path) -> None:
    for label, payload in (
        ("malformed", "{not-json"),
        ("attempting", {"schema_version": 1, "operation_id": "UR-227", "windows": {WINDOW_ID: {"status": "ATTEMPTING"}}}),
        ("terminal", {"schema_version": 1, "operation_id": "UR-227", "windows": {WINDOW_ID: {"status": "COMPLETE_FAILURE"}}}),
    ):
        root = tmp_path / label
        ensure_manifest(root)
        path = root / STATE_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
        calls: list[object] = []
        assert run(root, now=NOW, get=lambda *args, **kwargs: calls.append((args, kwargs)))["raw_gets"] == 0
        assert calls == []
    root = tmp_path / "replay"
    ensure_manifest(root)
    assert run(root, now=NOW, get=lambda *args, **kwargs: Response(200, FRESH_FX_HTML))["raw_gets"] == 1
    assert run(root, now=NOW, get=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not call")))["raw_gets"] == 0


def test_cli_transport_failure_is_terminal_and_preserves_prior_bytes(tmp_path) -> None:
    ensure_manifest(tmp_path)
    prior = _seed_prior_fx(tmp_path)
    result = run(tmp_path, now=NOW, get=lambda *args, **kwargs: Response(503))
    assert result["status"] == "COMPLETE_FAILURE" and result["raw_gets"] == 1
    assert (tmp_path / "data/state/current_observations/naver_mobile_home_current.json").read_bytes() == prior
    state = json.loads((tmp_path / STATE_PATH).read_text(encoding="utf-8"))
    assert state["windows"][WINDOW_ID]["failure_type"] == "HTTP_STATUS"
