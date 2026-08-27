from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from scripts.manual.collect.collect_naver_home_ur211_usdkrw import run
from stock_data.orchestration.naver_mobile_home_ur211_window import STATE_PATH, collector
from stock_data.orchestration.naver_mobile_home_windowed_current import NaverMobileHomeWindowedCollector
from stock_data.providers.naver_mobile_home_observation import route_for
from tests.unit.providers.test_naver_mobile_home_observation import HTML


NOW = datetime(2026, 8, 21, 9, 30, tzinfo=timezone.utc)
BOUNDARY = "2026-08-21T18:30:00+09:00"
FRESH_FX_HTML = HTML.replace(b"14:12", b"18:29")


class Response:
    def __init__(self, status_code: int, content: bytes = b"") -> None:
        self.status_code = status_code
        self.content = content


def _eligible_state(root: Path) -> None:
    path = root / STATE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": 1, "operation_id": "UR-211", "windows": {},
    }), encoding="utf-8")


def _seed_prior_fx(root: Path) -> bytes:
    seed = NaverMobileHomeWindowedCollector(
        root,
        operation_id="TEST_SEED",
        state_path=Path("data/state/test_ur211_seed.json"),
        landing_root=Path("data/landing/test_ur211_seed"),
        projection_cids=("FX_USDKRW",),
    )
    result = seed.run(
        now=NOW,
        response_factory=lambda: Response(200, FRESH_FX_HTML),
        allowed_window_ids=(BOUNDARY,),
    )
    assert result.status == "COMPLETE" and result.accepted_cids == ("FX_USDKRW",)
    path = root / "data/state/current_observations/naver_mobile_home_current.json"
    return path.read_bytes()


def test_prewindow_is_api_zero(tmp_path) -> None:
    seen: list[object] = []
    result = run(
        tmp_path,
        now=datetime(2026, 8, 21, 9, 29, tzinfo=timezone.utc),
        get=lambda *args, **kwargs: seen.append((args, kwargs)),
    )
    assert result == {
        "selected_boundary": None,
        "attempted_at_utc": "2026-08-21T09:29:00+00:00",
        "status": "PREFLIGHT_API_ZERO",
        "raw_gets": 0,
    }
    assert seen == []


def test_cli_absent_state_is_eligible_and_accepts_fresh_fx_with_one_get(tmp_path) -> None:
    assert not (tmp_path / STATE_PATH).exists()
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    result = run(
        tmp_path,
        now=NOW,
        get=lambda *args, **kwargs: calls.append((args, kwargs)) or Response(200, FRESH_FX_HTML),
    )

    assert result == {
        "selected_boundary": BOUNDARY,
        "attempted_at_utc": "2026-08-21T09:30:00+00:00",
        "status": "COMPLETE",
        "raw_gets": 1,
        "replay_api_calls": 0,
    }
    assert calls == [(("https://m.stock.naver.com/",), {"timeout": 10, "allow_redirects": False})]
    assert (tmp_path / STATE_PATH).exists()
    observation = collector(tmp_path).store.select(route_for("FX_USDKRW"))
    assert observation is not None
    assert observation.unit == "KRW per USD"
    assert observation.value == 1381.7


def test_cli_http_and_transport_failures_are_terminal_and_preserve_prior_fx(tmp_path) -> None:
    for label, get in (
        ("http", lambda *args, **kwargs: Response(503)),
        ("transport", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("synthetic"))),
    ):
        root = tmp_path / label
        prior = _seed_prior_fx(root)
        _eligible_state(root)
        calls: list[object] = []

        def counted_get(*args, _get=get, **kwargs):
            calls.append((args, kwargs))
            return _get(*args, **kwargs)

        result = run(root, now=NOW, get=counted_get)
        assert result["selected_boundary"] == BOUNDARY
        assert result["status"] == "COMPLETE_FAILURE"
        assert result["raw_gets"] == 1
        assert result["replay_api_calls"] == 0
        assert len(calls) == 1
        assert (root / "data/state/current_observations/naver_mobile_home_current.json").read_bytes() == prior
        state = json.loads((root / STATE_PATH).read_text(encoding="utf-8"))
        assert state["windows"][BOUNDARY]["status"] == "COMPLETE_FAILURE"
        assert state["windows"][BOUNDARY]["failure_type"] == (
            "HTTP_STATUS" if label == "http" else "OSError"
        )


def test_cli_terminal_or_malformed_ledger_never_invokes_callback(tmp_path) -> None:
    for label, state_payload in (
        ("terminal", {"schema_version": 1, "operation_id": "UR-211", "windows": {BOUNDARY: {"status": "COMPLETE"}}}),
        ("malformed", "{not-json"),
    ):
        root = tmp_path / label
        path = root / STATE_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            state_payload if isinstance(state_payload, str) else json.dumps(state_payload),
            encoding="utf-8",
        )
        calls: list[object] = []
        result = run(
            root,
            now=NOW,
            get=lambda *args, **kwargs: calls.append((args, kwargs)) or Response(200, FRESH_FX_HTML),
        )
        assert result == {
            "selected_boundary": BOUNDARY,
            "attempted_at_utc": "2026-08-21T09:30:00+00:00",
            "status": "PREFLIGHT_API_ZERO",
            "raw_gets": 0,
        }
        assert calls == []
