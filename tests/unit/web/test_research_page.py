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
from stock_web.api.research_scenario import select_best_in_scenario
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
        "independent_events": 3, "cycles_with_signal": 2, "signals_outside_cycles": 1,
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
    assert payload["legacy_numbers"] is True
    assert "재구축 이전" in payload["legacy_reason"]
    assert payload["result_cards"] == []
    assert payload["tab_status"]["rules"] == {
        "candidate_count": 2, "adopted_count": 0,
    }
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
        "후보 규칙 — 채택 전 · 낙폭 2단계 (KR): 1/2단계 · 노출 87% · 과거 동일 단계 60일 +7.5%",
        "채택된 규칙 없음 · 위 줄은 rule_candidates.json 후보의 전방검증 상태이며 채택 여부는 "
        "투자 규칙.md가 정한다(채택 시 status=adopted)",
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


def test_research_payload_does_not_mark_post_rebuild_artifact_legacy() -> None:
    root = _root()
    _write_research_fixture(root)
    path = root / research_page.LEADERBOARD_RELATIVE
    document = json.loads(path.read_text(encoding="utf-8"))
    document["generated_at"] = "2026-09-07T00:00:00+00:00"
    for candidate in document["candidates"]:
        candidate["product_share_at_max"] = None
        candidate["effective_exposure_max"] = None
    path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
    research_page._RESEARCH_CACHE.clear()

    payload = research_page.build_research_payload(root)

    assert payload["legacy_numbers"] is False
    assert payload["legacy_reason"] is None


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


def test_research_template_keeps_direct_experiment_in_decision_wait_state() -> None:
    text = (
        Path(__file__).parents[3] / "src/stock_web/templates/research.html"
    ).read_text(encoding="utf-8")

    assert '<details class="card research-experiment-card" id="rule-experiment"' in text
    assert "규칙 직접 시험해보기 ▾" in text
    assert "후보 A–D 순위와 임계값이 결정되기 전에는 새 평가 수치를 만들지 않습니다." in text
    assert 'data-preset="drawdown-2"' in text
    assert 'data-preset="vol-target-15"' in text
    assert 'id="experiment-evaluate" disabled' in text


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


def _compound_row(*, drawdown: float = -.2) -> dict[str, object]:
    metric = {
        "start": "2000-01-03", "end": "2015-12-31", "observations": 100,
        "final_wealth_multiple": 4.1, "baseline_final_wealth_multiple": 3.2,
        "relative_to_baseline": 1.28125, "cagr": .09, "max_drawdown": -.24,
        "trades": 4, "turnover": 2.0, "transaction_cost": .002,
        "period": "fit", "final_wealth_edge": .9,
    }
    return {
        "row_kind": "strategy", "basket": "KR", "underlying": "KOSPI",
        "drawdown_threshold": drawdown, "disp60_threshold": -.1,
        "levels": 2, "leverage_multiple": 2, "base_exposure": 1.0,
        "exit": "a", "cost_enabled": True, "product_variant": "synthetic",
        "fit": metric,
        "holdout": {**metric, "start": "2016-01-04", "end": "2026-09-04", "period": "holdout"},
        "full": {**metric, "period": "full"}, "actual_product_basis": None,
        "curve_tags": ["current_rule"],
        "equity_curve_weekly": [
            {"date": "2000-01-07", "wealth": 1.0, "weight": 0.0},
            {"date": "2015-12-25", "wealth": 4.1, "weight": 1.0},
        ],
        "cycles": [{
            "episode": 1, "entry_date": "2008-10-01", "max_level_reached": 2,
            "signal_end_date": "2009-01-02", "exit_date": "2009-01-05",
            "contribution_to_wealth": .2, "baseline_contribution": .1,
        }],
    }


def _write_compound_fixture(root: Path) -> None:
    output = root / research_page.COMPOUND_RELATIVE
    output.mkdir(parents=True, exist_ok=True)
    baseline = {
        "row_kind": "baseline", "basket": "KR", "underlying": "KOSPI",
        "fit": {"final_wealth_multiple": 3.2},
        "equity_curve_weekly": [
            {"date": "2000-01-07", "wealth": 1.0, "weight": 1.0},
            {"date": "2015-12-25", "wealth": 3.2, "weight": 1.0},
        ],
        "curve_tags": ["baseline"], "cycles": [],
    }
    (output / "grid_kr_kospi.json").write_text(
        json.dumps([_compound_row(), baseline], ensure_ascii=False), encoding="utf-8",
    )
    (output / "summary.json").write_text(json.dumps({
        "schema_version": 1, "experiment": "compound-ladder/v2", "quick": False,
        "fit_window": {"end": "2015-12-31"},
        "holdout_window": {"start": "2016-01-01"},
        "grid_artifacts": ["artifacts/research/compound_ladder/grid_kr_kospi.json"],
    }), encoding="utf-8")


def _write_crisis_overlay_fixture(root: Path) -> dict[str, object]:
    payload = {
        "schema_version": 2,
        "generated_at": "2026-09-05T00:00:00+00:00",
        "offset_start": -60, "offset_end": 250,
        "episodes": [{
            "id": "KR_2008-01-22", "market": "KR", "cycle": "2008–09 금융위기",
            "label": "2008 · KR", "type": "recession-type",
            "signal_date": "2008-01-22", "hold_start_date": "2008-01-08",
            "hold_start_offset": -1, "is_holdout": False,
        }],
        "assets": [{"id": "equity_reference", "label": "주식 기준"}],
        "normalisations": [
            {"id": "hold_start", "label": "보유시작 = 100"},
            {"id": "signal", "label": "신호일 = 100"},
        ],
        "series": {
            "hold_start": {"KR_2008-01-22": {"equity_reference": [100.0, 88.11]}},
            "signal": {"KR_2008-01-22": {"equity_reference": [99.0, 100.0]}},
        },
        "signal_values": {"KR_2008-01-22": {"equity_reference": 80.0}},
        "dates": {"KR_2008-01-22": ["2008-01-22", "2008-01-23"]},
        "yields": {"KR_2008-01-22": [4.0, 3.9]},
        "levels": {"KR_2008-01-22": [2, 2]},
        "ladder": {
            "hold_start": {"KR": {"KOSPI": {"median": [100.0, 101.0], "worst": "2008"}}},
            "signal": {"KR": {"KOSPI": {"median": [100.0, 101.0], "worst": "2008"}}},
        },
    }
    path = root / research_page.CRISIS_OVERLAY_RELATIVE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload


def test_crisis_overlay_endpoint_returns_fixture_and_missing_is_korean_404(
) -> None:
    tmp_path = _root()
    client = ASGITestClient(create_app(tmp_path))
    missing = client.get("/api/research/crisis-overlay", client_host="127.0.0.1")

    expected = _write_crisis_overlay_fixture(tmp_path)
    response = client.get("/api/research/crisis-overlay", client_host="127.0.0.1")

    assert missing.status_code == 404
    assert "미계산" in missing.json()["error"]
    assert response.status_code == 200
    assert response.json()["episodes"] == expected["episodes"]
    assert response.json()["series"] == expected["series"]
    assert response.json()["schema_version"] == 2
    assert response.json()["holdout_views"] == 0
    assert response.json()["signal_definition"] is None
    assert response.json()["legacy_numbers"] is True
    assert "signal_definition" in response.json()["legacy_reason"]


def test_crisis_overlay_post_rebuild_fields_clear_legacy_marker() -> None:
    root = _root()
    payload = _write_crisis_overlay_fixture(root)
    payload["generated_at"] = "2026-09-07T00:00:00+00:00"
    payload["signal_definition"] = {
        "kind": "B", "source": "caller",
        "drawdown_threshold": -.24, "disp60_threshold": -.08,
        "product_share_at_max": .45, "levels": 3, "base_exposure": .75,
    }
    path = root / research_page.CRISIS_OVERLAY_RELATIVE
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    result = research_page.build_crisis_overlay_payload(root)

    assert result["legacy_numbers"] is False
    assert result["legacy_reason"] is None


def _compound_combination(**overrides: object) -> dict[str, object]:
    return {
        "basket": "KR", "product": "kospi", "product_variant": "synthetic_2x",
        "drawdown_threshold": -.2, "disp60_threshold": -.1,
        "levels": 2, "leverage_multiple": 2, "exit": "a", "cost_enabled": True,
        **overrides,
    }


def test_compound_grid_endpoint_returns_fixture_rows_and_missing_is_korean_404(
) -> None:
    tmp_path = _root()
    _write_compound_fixture(tmp_path)
    client = ASGITestClient(create_app(tmp_path))

    catalog = client.get("/api/research/compound/grid", client_host="127.0.0.1")
    response = client.get(
        "/api/research/compound/grid?basket=KR&product=kospi",
        client_host="127.0.0.1",
    )
    missing = client.get(
        "/api/research/compound/grid?basket=KR&product=missing",
        client_host="127.0.0.1",
    )
    missing_row = client.post(
        "/api/research/compound/holdout-view",
        json=_compound_combination(drawdown_threshold=-.25),
        client_host="127.0.0.1",
    )

    assert catalog.status_code == response.status_code == 200
    assert catalog.json()["catalog"][0]["label"] == "KR · KOSPI"
    assert response.json()["rows"] == [_compound_row()]
    assert response.json()["baseline"]["row_kind"] == "baseline"
    assert response.json()["cached_values"]["drawdown_thresholds"] == [-.2]
    assert response.json()["legacy_numbers"] is True
    assert "effective_exposure_max" in response.json()["legacy_reason"]
    assert missing.status_code == 404
    assert "미계산 조합" in missing.json()["error"]
    assert missing_row.status_code == 404
    assert "미계산 조합" in missing_row.json()["error"]


def test_compound_grid_post_rebuild_fields_clear_legacy_marker() -> None:
    root = _root()
    _write_compound_fixture(root)
    output = root / research_page.COMPOUND_RELATIVE
    grid_path = output / "grid_kr_kospi.json"
    rows = json.loads(grid_path.read_text(encoding="utf-8"))
    for row in rows:
        row["product_share_at_max"] = .45 if row["row_kind"] == "strategy" else None
        row["effective_exposure_max"] = 1.45 if row["row_kind"] == "strategy" else 1.0
    grid_path.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    summary_path = output / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    summary["generated_at"] = "2026-09-07T00:00:00+00:00"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False), encoding="utf-8")

    result = research_page.build_compound_grid_payload(root, basket="KR", product="kospi")

    assert result["legacy_numbers"] is False
    assert result["legacy_reason"] is None


def test_compound_holdout_view_counter_increments_session_and_persists(
) -> None:
    tmp_path = _root()
    _write_research_fixture(tmp_path)
    _write_compound_fixture(tmp_path)
    client = ASGITestClient(create_app(tmp_path))

    first = client.post(
        "/api/research/compound/holdout-view", json=_compound_combination(),
        client_host="100.85.10.20",
    )
    second = client.post(
        "/api/research/compound/holdout-view", json=_compound_combination(),
        client_host="100.85.10.20",
    )

    assert first.status_code == second.status_code == 200
    assert first.json()["persistent_views"] == first.json()["session_views"] == 1
    assert second.json()["persistent_views"] == second.json()["session_views"] == 2
    registry = json.loads(
        (tmp_path / research_page.CANDIDATES_RELATIVE).read_text(encoding="utf-8")
    )
    assert registry["attempt_count"] == 9
    event = registry["history"][-1]
    recorded = json.loads(event["reason"])
    assert recorded["combination"]["levels"] == 2
    assert recorded["viewed_at"] == second.json()["viewed_at"]


def test_crisis_overlay_holdout_view_uses_same_counter(
) -> None:
    tmp_path = _root()
    _write_research_fixture(tmp_path)
    _write_crisis_overlay_fixture(tmp_path)
    client = ASGITestClient(create_app(tmp_path))
    body = {"kind": "crisis_overlay", "mode": "asset", "asset": "tlt"}

    first = client.post(
        "/api/research/compound/holdout-view", json=body, client_host="100.85.10.20",
    )
    second = client.post(
        "/api/research/compound/holdout-view", json=body, client_host="100.85.10.20",
    )

    assert first.status_code == second.status_code == 200
    assert first.json()["persistent_views"] == first.json()["session_views"] == 1
    assert second.json()["persistent_views"] == second.json()["session_views"] == 2
    registry = json.loads(
        (tmp_path / research_page.CANDIDATES_RELATIVE).read_text(encoding="utf-8")
    )
    event = json.loads(registry["history"][-1]["reason"])
    assert event["kind"] == "crisis_overlay"
    assert event["selection"] == {"asset": "tlt", "mode": "asset"}


def test_compound_registration_builds_forward_definition_and_metadata() -> None:
    payload = research_page.build_compound_candidate_registration({
        "name": "내 조합", "reason": "fit 고원",
        "compound": _compound_combination(
            drawdown_threshold=-.25, disp60_threshold=-.15, levels=4,
            leverage_multiple=3, exit="c", cost_enabled=False,
        ),
    })

    assert payload["definition"] == {
        "type": "ladder",
        "indicators": [
            {"key": "drawdown252", "op": "<=", "threshold": -.25},
            {"key": "disp60", "op": "<=", "threshold": -.15},
        ],
        "levels": 4,
    }
    assert payload["metadata"] == {
        "exit": "c", "multiple": 3, "cost": False,
        "product_basis": "synthetic_2x",
        "source": "compound_ladder_ui",
    }
    assert payload["_registry_definition"]["levels"] == 2


def test_compound_run_cost_flag_is_preserved_in_grid_and_cli_hint() -> None:
    spec = research_page.normalise_compound_run({
        "baskets": ["KR"], "product": "synthetic_2x", "cost_enabled": False,
        "ranges": {
            "drawdown_threshold": "-.24", "disp60_threshold": "-.08",
            "product_share_at_max": ".45", "levels": "2",
            "base_exposure": ".75", "leverage_multiple": "2",
        },
    })

    assert spec["grid"]["cost_enabled"] == (False,)
    command = research_page._compound_command(spec)
    assert "cost_enabled" in command and "False" in command


@pytest.mark.parametrize(("missing", "label"), (
    ("drawdown_threshold", "낙폭 임계값"),
    ("disp60_threshold", "이격도 임계값"),
    ("product_share_at_max", "최고 단계 레버리지 상품 비중"),
    ("levels", "분할 수"),
    ("base_exposure", "기본 노출"),
))
def test_compound_run_never_supplies_an_undecided_ladder_default(
    missing: str, label: str,
) -> None:
    ranges = {
        "drawdown_threshold": "-.24", "disp60_threshold": "-.08",
        "product_share_at_max": ".45", "levels": "2",
        "base_exposure": ".75", "leverage_multiple": "2",
    }
    del ranges[missing]

    with pytest.raises(research_page.ResearchInputError, match=label):
        research_page.normalise_compound_run({
            "baskets": ["KR"], "product": "synthetic_2x",
            "cost_enabled": True, "ranges": ranges,
        })


def test_select_best_in_scenario_requires_complete_and_unmixed_scenario() -> None:
    base = {
        "cost_enabled": True, "exit": "a", "base_exposure": 1.0,
        "product_variant": "synthetic", "fit": {"relative_to_baseline": 1.1},
    }
    with pytest.raises(ValueError, match="scenario must fix cost/tax/exit/base_exposure"):
        select_best_in_scenario([base], scenario={"cost_enabled": True})

    taxed = [{**base, "tax_rate": 0.22}]
    with pytest.raises(ValueError, match="scenario must fix cost/tax/exit/base_exposure"):
        select_best_in_scenario(
            taxed,
            scenario={
                "cost_enabled": True, "exit": "a", "base_exposure": 1.0,
                "product_variant": "synthetic",
            },
        )

    mixed = [base, {**base, "cost_enabled": 1, "fit": {"relative_to_baseline": 1.2}}]
    with pytest.raises(ValueError, match="scenario must fix cost/tax/exit/base_exposure"):
        select_best_in_scenario(
            mixed,
            scenario={
                "cost_enabled": True, "exit": "a", "base_exposure": 1.0,
                "product_variant": "synthetic",
            },
        )


def test_select_best_in_scenario_returns_maximum_only_inside_scenario() -> None:
    rows = [
        {
            "cost_enabled": cost, "exit": "a", "base_exposure": 1.0,
            "product_variant": "synthetic", "fit": {"relative_to_baseline": value},
        }
        for cost, value in ((True, 1.1), (True, 1.3), (False, 9.9))
    ]
    best = select_best_in_scenario(
        rows,
        scenario={
            "cost_enabled": True, "exit": "a", "base_exposure": 1.0,
            "product_variant": "synthetic",
        },
    )

    assert best["fit"]["relative_to_baseline"] == 1.3


def test_compound_registration_posts_through_existing_candidate_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmp_path = _root()
    _write_research_fixture(tmp_path)
    monkeypatch.setattr(rule_leaderboard, "run_rule_leaderboard", lambda _root: None)
    client = ASGITestClient(create_app(tmp_path))

    response = client.post(
        "/api/research/candidates",
        json={
            "name": "복리 사다리 후보", "reason": "fit 고원",
            "compound": _compound_combination(levels=4, exit="d"),
        },
        client_host="127.0.0.1",
    )

    assert response.status_code == 200
    registry = json.loads(
        (tmp_path / research_page.CANDIDATES_RELATIVE).read_text(encoding="utf-8")
    )
    candidate = registry["candidates"][-1]
    assert candidate["definition"]["indicators"] == [
        {"key": "drawdown252", "op": "<=", "threshold": -.2},
        {"key": "disp60", "op": "<=", "threshold": -.1},
    ]
    assert candidate["definition"]["levels"] == 2
    assert '"exit":"d"' in candidate["reason"]
    assert '"product_basis":"synthetic_2x"' in candidate["reason"]
    assert '"source":"compound_ladder_ui"' in candidate["reason"]


def test_compound_run_is_single_background_job_and_reports_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmp_path = _root()
    started = threading.Event()
    release = threading.Event()

    def fake_engine(
        _root: Path, baskets: tuple[str, ...], grid: dict[str, tuple[object, ...]],
    ) -> None:
        assert baskets == ("KR",)
        assert grid["levels"] == (2, 4)
        started.set()
        assert release.wait(2)

    monkeypatch.setattr(research_page, "_run_compound_engine", fake_engine)
    client = ASGITestClient(create_app(tmp_path))
    body = {
        "baskets": ["KR"], "product": "synthetic_2x", "cost_enabled": True,
        "ranges": {
            "drawdown_threshold": "-.24", "disp60_threshold": "-.08",
            "product_share_at_max": ".45", "levels": "2,4",
            "base_exposure": ".75", "leverage_multiple": "2",
        },
    }
    try:
        first = client.post(
            "/api/research/compound/run", json=body, client_host="127.0.0.1",
        )
        assert started.wait(2)
        second = client.post(
            "/api/research/compound/run", json=body, client_host="127.0.0.1",
        )
        status = client.get("/api/research/compound/run", client_host="127.0.0.1")
        assert first.status_code == 202
        assert second.status_code == 409
        assert status.json()["running"] is True
        assert any("START" in line for line in status.json()["progress_lines"])
    finally:
        release.set()
        for _ in range(100):
            if not research_page.build_compound_run_status(tmp_path)["running"]:
                break
            threading.Event().wait(.01)
    assert research_page.build_compound_run_status(tmp_path)["last_error"] is None


def test_compound_panel_and_all_routes_are_hidden_in_guest_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmp_path = _root()
    _write_research_fixture(tmp_path)
    _write_compound_fixture(tmp_path)
    _write_crisis_overlay_fixture(tmp_path)
    monkeypatch.setenv("STOCK_WEB_PUBLIC_MODE", "1")
    client = ASGITestClient(create_app(tmp_path))

    page = client.get("/research", client_host="127.0.0.1")
    grid = client.get("/api/research/compound/grid", client_host="127.0.0.1")
    overlay = client.get("/api/research/crisis-overlay", client_host="127.0.0.1")
    holdout = client.post(
        "/api/research/compound/holdout-view", json=_compound_combination(),
        client_host="127.0.0.1",
    )
    run = client.post(
        "/api/research/compound/run", json={"baskets": ["KR"]},
        client_host="127.0.0.1",
    )

    assert page.status_code == 200 and 'id="compound-lab"' not in page.text
    assert 'id="crisis-overlay"' not in page.text
    assert grid.status_code == overlay.status_code == holdout.status_code == run.status_code == 404


def test_compound_panel_static_contract_uses_cached_frame_render_and_existing_chart() -> None:
    web = Path(__file__).parents[3] / "src/stock_web"
    template = (web / "templates/research.html").read_text(encoding="utf-8")
    script = (web / "static/research.js").read_text(encoding="utf-8")
    style = (web / "static/research.css").read_text(encoding="utf-8")

    assert "파라미터 손잡이" in template
    assert "적합 구간(~2015)은 자유롭게 탐색" in template
    assert "홀드아웃 열람 0회 (이 세션 0회)" in template
    assert "명령줄로 돌리기" in template
    assert "미정 · 사용자 결정 대기" in template
    assert "cache: new Map()" in script
    assert "requestAnimationFrame(renderCompound)" in script
    assert "window.SIChart.renderLineChart" in script
    assert "setTimeout(pollCompoundRun, 2000)" in script
    assert 'fetch("/api/research/candidates"' in script
    assert "compound.underlyings" in script
    assert 'id="compound-exit-compare"' in template
    assert "renderCompoundExitCompare(combination)" in script
    assert "출구 5개 나란히" in script
    assert "1배 그냥 보유 (기준선)" in script
    assert "홀드아웃(2016~)에서는 뒤집힘" in script
    assert "신호 ${esc(episode.signal_date)}" in script
    assert "고원 판정 (적합 구간)" in template
    assert "이 조합의 고원 판정은 아직 계산 안 됨" in script
    assert "전 구간 기준선 미달" in script
    assert "이 조합에서는 파라미터가 결과를 바꾸지 않음" in script
    assert 'return "전액 손실"' in script
    assert "보정 추가 드래그" in script and "숫자는 합성과 동일" in script
    assert "보정 없음(양수 갭 · 합성 그대로)" in script
    assert "function selectBestInScenario(rows, scenario, metricFn)" in script
    assert "best = selectBestInScenario(rows, scenarioValues" in script
    assert "scenario must fix cost/tax/exit/base_exposure" in script
    assert "실제 상품 보정 값 없음 · 합성 값을 표시하지 않음" in script
    assert 'const fit = compoundMetric(row, combination.product_variant, "fit")' in script
    assert 'const basis = variant === "actual_adjusted" ? ((row || {}).actual_product_basis || null) : row' in script
    assert 'variant === "actual_adjusted" ? []' not in script
    assert "item.holdout_relative_to_baseline" in script
    assert "item.holdout_baseline_final_wealth_multiple" in script
    assert '"거래비용 포함" : "거래비용 제외"' in script
    assert 'product_basis' in Path(__file__).parents[3].joinpath("src/stock_web/api/research_page.py").read_text(encoding="utf-8")
    assert "cost_enabled: [payload.cost_enabled]" in script
    assert "compoundDefaults" not in script
    assert ".compound-lab-grid { display: grid; grid-template-columns:" in style
    assert ".compound-lab-grid { grid-template-columns: 1fr; }" in style


def test_crisis_overlay_panel_declares_alignment_presets_and_holdout_gate() -> None:
    web = Path(__file__).parents[3] / "src/stock_web"
    template = (web / "templates/research.html").read_text(encoding="utf-8")
    script = (web / "static/research.js").read_text(encoding="utf-8")
    style = (web / "static/research.css").read_text(encoding="utf-8")

    assert 'id="crisis-overlay"' in template
    assert "한 자산 × 여러 위기" in template and "한 위기 × 여러 자산" in template
    for label in ("주식 vs 10년 금리", "30년물(TLT 대용) 위기별", "리츠 위기별", "낙폭 사다리: KOSPI 5개 사이클"):
        assert label in template
    assert ".venv\\Scripts\\python.exe scripts/research/run_crisis_overlay.py --project-root ." in template
    assert 'id="crisis-basis"' in template and "보유시작 = 100" in template
    assert 'fetch("/api/research/crisis-overlay")' in script
    assert 'kind: "crisis_overlay"' in script
    assert "signal_values" in script and "crisis-signal-line" in script
    assert '(payload.series || {})[crisisBasis()]' in script
    assert "crisis-y-title" in script and "crisis-basis-caption" in template
    assert "updateHoldoutCounters(payload);" in script
    assert 'if ($("crisis-view-count"))' in script and 'if ($("compound-view-count"))' in script
    assert "crisis-check-band" in script and "row.date || \"중앙\"" in script
    assert ".crisis-chart-shell" in style and ".crisis-tooltip" in style
