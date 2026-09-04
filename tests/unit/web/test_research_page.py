from __future__ import annotations

import json
import os
from pathlib import Path
import threading
from urllib.parse import urlencode
from uuid import uuid4

import pandas as pd
import pytest

from stock_web.api import research_page
from stock_data.research import rule_leaderboard
from stock_data.research.rule_candidates import rules_version
from stock_web.app import create_app
from tests.unit.web import ASGITestClient


def _root() -> Path:
    root = Path(__file__).parents[3] / ".tmp/agents/research-page-20260904/fixtures" / uuid4().hex
    root.mkdir(parents=True)
    return root


def _metric(*, n: int, mean: float, diff: float, warn: bool = False) -> dict[str, object]:
    return {
        "n": n, "mean_20": mean / 2, "mean_60": mean, "mean_90": mean * 1.2,
        "median_60": mean * .8, "hit_60": .6, "baseline_60": mean - diff,
        "diff_60": diff, "vol_60": .2, "mdd_60": -.1,
        "warn_small_sample": warn,
    }


def _candidate(
    candidate_id: str, name: str, *, side: str, basket: str, diff: float,
    status: str = "active", n: int = 64,
) -> dict[str, object]:
    return {
        "id": candidate_id, "name": name, "side": side, "basket": basket,
        "status": status,
        "definition": {
            "type": "ladder",
            "indicators": [
                {"key": "drawdown252", "op": "<=", "threshold": -.2},
                {"key": "disp60", "op": "<=", "threshold": -.1},
            ],
            "levels": 2,
        },
        "added_on": "2026-09-04", "reason": "합성 검증 후보",
        "results": {
            "fit": _metric(n=80, mean=.04, diff=.01),
            "holdout": _metric(n=n, mean=.05, diff=diff),
        },
        "levels": [
            {"level": 0, "fit": {"n": 100, "mean_60": .01}, "holdout": {"n": 40, "mean_60": .02}},
            {"level": 1, "fit": {"n": 60, "mean_60": .04}, "holdout": {"n": 20, "mean_60": .06}},
        ],
        "cycles": [{
            "id": "gfc_2008", "signals": 12, "first_signal": "2008-01-22",
            "mean_60": .04, "verdict": "hit",
        }],
        "current": {
            "date": "2026-09-03", "score": 1, "level": 1, "max_level": 2,
            "exposure": .87,
            "indicators": {"drawdown252": -.31, "disp60": -.10, "rsi14": 46.6, "volidx_pct": .42},
            "analog": {"n": 52, "mean_60": .075, "hit_60": .63},
        },
    }


def _write_research_fixture(root: Path) -> None:
    leaderboard = {
        "schema_version": 1, "generated_at": "2026-09-03T21:51:09+00:00",
        "rules_version": "1234567890abcdef", "attempt_count": 7,
        "fit_window": {"end": "2015-12-31"},
        "holdout_window": {"start": "2016-01-01"},
        "cycles": [{
            "id": "gfc_2008", "label": "2008 금융위기",
            "start": "2008-01-01", "end": "2009-06-30",
        }],
        "candidates": [
            _candidate("kr_dd_ladder_2", "낙폭 2단계 (KR)", side="drawdown", basket="KR", diff=.02),
            _candidate("us_hot_1", "과열 역방향", side="overheat", basket="US_TECH", diff=-.05, n=10),
        ],
        "warnings": ["합성 경고"],
    }
    config = {
        "schema_version": 1, "attempt_count": 7,
        "history": [
            {"date": "2026-08-01", "action": "add", "id": "old", "reason": "먼저"},
            {"date": "2026-09-04", "action": "add", "id": "kr_dd_ladder_2", "reason": "최근"},
        ],
        "candidates": [],
    }
    leaderboard_path = root / research_page.LEADERBOARD_RELATIVE
    config_path = root / research_page.CANDIDATES_RELATIVE
    leaderboard_path.parent.mkdir(parents=True)
    config_path.parent.mkdir(parents=True)
    leaderboard_path.write_text(json.dumps(leaderboard, ensure_ascii=False), encoding="utf-8")
    config_path.write_text(json.dumps(config, ensure_ascii=False), encoding="utf-8")
    research_page._RESEARCH_CACHE.clear()
    research_page._FORWARD_CACHE.clear()
    research_page._reset_experiment_session()


def _write_forward_fixture(root: Path) -> list[pd.Timestamp]:
    dates = list(pd.date_range("2026-01-02", periods=100, freq="B"))
    frame = pd.DataFrame({
        "date": dates, "symbol": "KOSPI200", "close": [100.0 + index for index in range(100)],
    })
    path = root / "data/normalized/kr_index_daily/data.parquet"
    path.parent.mkdir(parents=True)
    frame.to_parquet(path, index=False)
    signals = [{
        "as_of": dates[index].date().isoformat(), "candidate_id": "kr_dd_ladder_2",
        "rules_version": "1234567890abcdef", "score": 1, "level": 1,
        "exposure": .87, "close": 1.0, "basket": "KR",
    } for index in range(5)]
    signals.append({
        "as_of": dates[-1].date().isoformat(), "candidate_id": "kr_dd_ladder_2",
        "rules_version": "1234567890abcdef", "score": 2, "level": 2,
        "exposure": .75, "close": 999.0, "basket": "KR",
    })
    signal_path = root / research_page.FORWARD_RELATIVE
    signal_path.parent.mkdir(parents=True)
    signal_path.write_text("\n".join(json.dumps(row) for row in signals), encoding="utf-8")
    return dates


def test_research_payload_shape_directional_sorting_history_and_status() -> None:
    root = _root()
    _write_research_fixture(root)

    payload = research_page.build_research_payload(root)

    assert payload["status"] == "READY"
    assert payload["rules_version"] == "1234567890abcdef"
    assert payload["warning_count"] == 1
    assert [row["id"] for row in payload["candidates"]] == ["us_hot_1", "kr_dd_ladder_2"]
    assert payload["candidates"][0]["direction_hint"] == "낮을수록 좋음"
    assert payload["candidates"][0]["warn_small_sample"] is True
    assert payload["candidates"][0]["cycles"][0]["label"] == "2008 금융위기"
    assert payload["generated_at_display"] == "09-04 06:51"
    assert payload["candidates"][1]["definition_text"] == (
        "252일 낙폭 ≤ -20% · 60일 이격 ≤ -10% → 각 1점, 단계 0~2"
    )
    assert [row["id"] for row in payload["history"]] == ["kr_dd_ladder_2", "old"]
    assert payload["current_status"] == [
        "규칙 현재 상태 · 낙폭 2단계 (KR): 1/2단계 · 노출 87% · 과거 동일 단계 60일 +7.5%",
    ]
    json.dumps(payload, ensure_ascii=False, allow_nan=False)


def test_definition_sentences_cover_ladder_vol_target_and_hybrid() -> None:
    ladder = {
        "type": "ladder",
        "indicators": [
            {"key": "rsi14", "op": ">=", "threshold": 70},
            {"key": "volidx_pct", "op": "<=", "threshold": .2},
        ],
        "levels": 2,
    }
    vol_target = {"type": "vol_target", "target_vol": .15, "window": 20}

    assert research_page._definition_text(ladder) == (
        "RSI14 ≥ 70 · 변동성지수 백분위(VIX/VKOSPI) ≤ 20% → 각 1점, 단계 0~2"
    )
    assert research_page._definition_text(vol_target) == (
        "20일 실현 변동성 기준 목표 15% · 노출 = min(1, 목표/실현)"
    )
    assert research_page._definition_text({
        "type": "hybrid", "ladder": ladder, "vol_target": vol_target,
    }) == (
        "RSI14 ≥ 70 · 변동성지수 백분위(VIX/VKOSPI) ≤ 20% → 각 1점, 단계 0~2"
        " + 20일 실현 변동성 기준 목표 15% · 노출 = min(1, 목표/실현)"
    )


def test_unsigned_quantities_use_unsigned_percent_formatter() -> None:
    script = (
        Path(__file__).parents[3] / "src/stock_web/static/research.js"
    ).read_text(encoding="utf-8")

    for expression in (
        "holdout.hit_60, 0", "holdout.vol_60", "current.exposure, 0",
        "analog.hit_60, 0", "row.exposure, 0", "value)",
    ):
        assert f"pctUnsigned({expression}" in script


def test_research_payload_cache_is_invalidated_by_either_file_mtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _root()
    _write_research_fixture(root)
    real_read = research_page._read_json
    calls: list[Path] = []
    monkeypatch.setattr(
        research_page, "_read_json", lambda path: calls.append(path) or real_read(path),
    )

    research_page.build_research_payload(root)
    research_page.build_research_payload(root)
    assert len(calls) == 2

    config_path = root / research_page.CANDIDATES_RELATIVE
    current = config_path.stat().st_mtime_ns
    os.utime(config_path, ns=(current + 1_000_000_000, current + 1_000_000_000))
    research_page.build_research_payload(root)
    assert len(calls) == 4


def test_forward_returns_use_exact_normalized_sessions_and_expose_summary() -> None:
    root = _root()
    _write_research_fixture(root)
    _write_forward_fixture(root)

    payload = research_page.build_forward_payload(root)
    candidate = payload["groups"][0]["candidates"][0]
    newest = candidate["rows"][0]
    oldest = candidate["rows"][-1]

    assert payload["status"] == "READY"
    assert oldest["reference_close"] == 100.0
    assert oldest["signal_close"] == 1.0
    assert oldest["return_20"] == pytest.approx(.20)
    assert oldest["return_60"] == pytest.approx(.60)
    assert oldest["return_90"] == pytest.approx(.90)
    assert oldest["status_90"] == "실현"
    assert newest["return_20"] is None and newest["status_20"] == "대기"
    assert candidate["summary"]["realised_rows"] == 5
    assert candidate["summary"]["n_90"] == 5


def test_missing_artifacts_return_korean_empty_states() -> None:
    root = _root()
    research_page._RESEARCH_CACHE.clear()
    research_page._FORWARD_CACHE.clear()

    leaderboard = research_page.build_research_payload(root)
    forward = research_page.build_forward_payload(root)

    assert leaderboard["status"] == forward["status"] == "EMPTY"
    assert leaderboard["message"] == forward["message"] == research_page.EMPTY_MESSAGE
    assert leaderboard["current_status"] == ["규칙 평가 없음"]


def test_research_routes_are_readable_from_tailnet_and_html_has_verdict_colours() -> None:
    root = _root()
    _write_research_fixture(root)
    client = ASGITestClient(create_app(root))

    page = client.get("/research", client_host="100.85.10.20")
    api = client.get("/api/research", client_host="100.85.10.20")
    forward = client.get("/api/research/forward", client_host="100.85.10.20")

    assert page.status_code == api.status_code == forward.status_code == 200
    assert "규칙 후보 검증" in page.text
    assert 'class="verdict-hit"' in page.text
    assert 'class="verdict-miss"' in page.text
    assert 'class="verdict-none"' in page.text
    assert "홀드아웃 성적을 보고 규칙을 고치면 과적합입니다" in page.text


def _experiment_url(*indicators: str, **overrides: object) -> str:
    params: list[tuple[str, object]] = [
        ("side", overrides.get("side", "drawdown")),
        ("basket", overrides.get("basket", "KR")),
        ("type", overrides.get("type", "ladder")),
        *(('ind', indicator) for indicator in indicators),
        ("levels", overrides.get("levels", len(indicators) or 1)),
        ("target_vol", overrides.get("target_vol", .15)),
        ("horizon", overrides.get("horizon", 60)),
    ]
    return "/api/research/experiment?" + urlencode(params)


def test_experiment_api_parses_definition_and_is_readable_when_relayed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _root()
    _write_research_fixture(root)
    captured: dict[str, object] = {}

    def fake_evaluate(
        _root: Path, definition: dict[str, object], basket: str, side: str,
        horizons: tuple[int, ...] = (20, 60, 90),
    ) -> dict[str, object]:
        captured.update(definition=definition, basket=basket, side=side, horizons=horizons)
        return _candidate("experiment", "규칙 실험", side=side, basket=basket, diff=.01)

    monkeypatch.setattr(rule_leaderboard, "evaluate_definition", fake_evaluate)
    client = ASGITestClient(create_app(root))
    response = client.get(
        _experiment_url("drawdown252:<=:-0.2", "disp60:<=:-0.1", levels=2),
        client_host="100.85.10.20",
    )

    assert response.status_code == 200
    payload = response.json()
    assert captured == {
        "definition": {
            "type": "ladder",
            "indicators": [
                {"key": "drawdown252", "op": "<=", "threshold": -.2},
                {"key": "disp60", "op": "<=", "threshold": -.1},
            ],
            "levels": 2,
        },
        "basket": "KR", "side": "drawdown", "horizons": (20, 60, 90),
    }
    assert payload["experiment_count"] == 1
    assert payload["horizon"] == 60
    assert payload["can_register"] is False
    assert payload["caution"] == research_page.EXPERIMENT_CAUTION


def test_experiment_api_returns_korean_400_for_bad_op_and_missing_indicator() -> None:
    root = _root()
    _write_research_fixture(root)
    client = ASGITestClient(create_app(root))

    bad_op = client.get(_experiment_url("drawdown252:>=:-0.2"), client_host="127.0.0.1")
    missing = client.get(_experiment_url(levels=1), client_host="127.0.0.1")

    assert bad_op.status_code == missing.status_code == 400
    assert "연산자는 <=만" in bad_op.json()["error"]
    assert missing.json()["error"] == "지표를 하나 이상 선택해 주세요."


def test_experiment_api_rate_limits_each_client_after_ten_evaluations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _root()
    _write_research_fixture(root)
    monkeypatch.setattr(
        rule_leaderboard, "evaluate_definition",
        lambda _root, _definition, basket, side: _candidate(
            "experiment", "규칙 실험", side=side, basket=basket, diff=.01,
        ),
    )
    client = ASGITestClient(create_app(root))
    url = _experiment_url("drawdown252:<=:-0.2")

    responses = [client.get(url, client_host="127.0.0.1") for _ in range(11)]

    assert all(response.status_code == 200 for response in responses[:10])
    assert responses[10].status_code == 429
    assert "1분에 10회" in responses[10].json()["error"]


def test_candidate_post_is_loopback_only_records_attempt_and_regenerates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _root()
    _write_research_fixture(root)
    calls: list[Path] = []

    def fake_runner(project_root: Path) -> tuple[Path, Path, dict[str, object]]:
        calls.append(project_root)
        latest = project_root / research_page.LEADERBOARD_RELATIVE
        payload = json.loads(latest.read_text(encoding="utf-8"))
        payload["rules_version"] = rules_version(project_root)
        payload["attempt_count"] = 8
        latest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return latest, latest, payload

    monkeypatch.setattr(rule_leaderboard, "run_rule_leaderboard", fake_runner)
    client = ASGITestClient(create_app(root))
    body = {
        "name": "나의 낙폭 규칙", "side": "drawdown", "basket": "KR",
        "definition": {
            "type": "ladder",
            "indicators": [{"key": "drawdown252", "op": "<=", "threshold": -.25}],
            "levels": 1,
        },
        "reason": "직접 실험 후 등록",
    }

    denied = client.post("/api/research/candidates", json=body, client_host="100.85.10.20")
    response = client.post("/api/research/candidates", json=body, client_host="127.0.0.1")

    assert denied.status_code == 403
    assert response.status_code == 200 and response.json()["status"] == "ready"
    registry = json.loads((root / research_page.CANDIDATES_RELATIVE).read_text(encoding="utf-8"))
    assert registry["attempt_count"] == 8
    assert registry["history"][-1]["action"] == "add"
    assert registry["history"][-1]["reason"] == "직접 실험 후 등록"
    assert registry["candidates"][-1]["status"] == "experimental"
    assert calls == [root.resolve()]


def test_candidate_post_returns_queued_when_regeneration_exceeds_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _root()
    _write_research_fixture(root)
    release = threading.Event()
    finished = threading.Event()

    def slow_runner(_project_root: Path) -> None:
        release.wait(2)
        finished.set()

    monkeypatch.setattr(rule_leaderboard, "run_rule_leaderboard", slow_runner)
    monkeypatch.setattr(research_page, "_REGENERATION_WAIT_SECONDS", .01)
    client = ASGITestClient(create_app(root))
    body = {
        "name": "느린 재생성 후보", "side": "drawdown", "basket": "KR",
        "definition": {
            "type": "ladder",
            "indicators": [{"key": "disp60", "op": "<=", "threshold": -.1}],
            "levels": 1,
        },
        "reason": "백그라운드 경로 검증",
    }

    try:
        response = client.post(
            "/api/research/candidates", json=body, client_host="127.0.0.1",
        )
        assert response.status_code == 200
        assert response.json()["status"] == "queued"
        assert response.json()["rules_version"] == rules_version(root)
    finally:
        release.set()
        assert finished.wait(2)


def test_research_template_contains_collapsed_experiment_and_three_presets() -> None:
    text = (
        Path(__file__).parents[3] / "src/stock_web/templates/research.html"
    ).read_text(encoding="utf-8")

    assert '<details class="card research-experiment-card" id="rule-experiment">' in text
    assert "규칙 직접 시험해보기 ▾" in text
    assert "현재 관심종목 조건(RSI≤30 · 60일선 −10% · 고점 −30%)" in text
    assert 'data-preset="drawdown-2"' in text
    assert 'data-preset="vol-target-15"' in text


def test_every_dashboard_navigation_places_research_after_data() -> None:
    template_root = Path(__file__).parents[3] / "src/stock_web/templates"
    for name in ("home.html", "market.html", "stocks.html", "account.html", "data.html", "placeholder.html"):
        text = (template_root / name).read_text(encoding="utf-8")
        nav = text.split('<nav class="nav">', 1)[1].split("</nav>", 1)[0]
        assert nav.index('href="/research"') > nav.index('href="/data"')


def test_research_template_declares_every_id_the_script_binds() -> None:
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[3] / "src/stock_web"
    script = (root / "static/research.js").read_text(encoding="utf-8")
    template = (root / "templates/research.html").read_text(encoding="utf-8")
    used = set(re.findall(r'\$\("([a-zA-Z0-9_-]+)"\)', script))
    present = set(re.findall(r'id="([a-zA-Z0-9_-]+)"', template))
    assert used - present == set(), sorted(used - present)
