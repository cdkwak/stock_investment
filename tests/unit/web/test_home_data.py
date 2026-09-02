from __future__ import annotations

import json
from pathlib import Path

import pytest

from stock_web.api import home_data
from stock_web.api.regime import (
    build_rules,
    global_risk_temperature,
    oversold_strength,
    temperature_label,
)
from tests.unit.web import make_project, new_temp_root


def test_regime_formulas_match_hand_calculations() -> None:
    score = oversold_strength(15.0, -10.0, 100.0)
    assert score is not None
    assert score[0] == 10.0
    assert dict(score[1]) == {"RSI": 4.0, "이격": 3.0, "변동성": 3.0}
    assert oversold_strength(50.0, 0.0, 50.0) == (
        0.0, (("RSI", 0.0), ("이격", 0.0), ("변동성", 0.0)),
    )
    assert oversold_strength(None, 0.0, 50.0) is None
    assert temperature_label(71.0, 0.1) == "과열"
    assert temperature_label(29.0, -0.1) == "침체"
    assert temperature_label(50.0, 0.0) == "중립"
    assert global_risk_temperature(-0.1, -0.3, -30.0, 0.0) == "침체"
    assert global_risk_temperature(0.7, 0.3, 0.0, 0.0) == "과열"


def test_home_payload_is_json_clean_and_missing_sections_explain_why(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = make_project(new_temp_root())
    monkeypatch.setenv("STOCK_WEB_RULES_PATH", str(root / "missing-rules.md"))
    home_data._HOME_CACHE.clear()

    payload = home_data.build_home_payload(root)

    json.dumps(payload, ensure_ascii=False, allow_nan=False)
    sections = payload["sections"]
    assert sections["account"]["reason"] == "읽을 수 있는 로컬 계좌 스냅샷이 없습니다."
    assert sections["schedule"]["reason"] == "로컬 일정 파일이 없습니다."
    assert sections["health"]["current"] >= 1
    assert len(sections["regime"]["markets"]) == 3
    assert sections["regime"]["rules"] is None
    assert "brief" not in sections
    assert sections["scanner"]["status"] == "READY"
    assert sections["scanner"]["count"] == 1
    assert sections["flows"]["rows"]


def test_home_payload_cache_is_keyed_by_root_and_lasts_sixty_seconds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = new_temp_root().resolve()
    calls: list[Path] = []
    clock = iter((100.0, 120.0, 161.0))
    monkeypatch.setattr(home_data.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(
        home_data, "_build_home_payload_uncached",
        lambda path: calls.append(path) or {"root": str(path), "call": len(calls)},
    )
    home_data._HOME_CACHE.clear()

    first = home_data.build_home_payload(root)
    second = home_data.build_home_payload(root)
    third = home_data.build_home_payload(root)

    assert first is second
    assert third["call"] == 2
    assert calls == [root, root]


def test_rules_compare_only_against_user_supplied_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = new_temp_root()
    rules = root / "rules.md"
    rules.write_text(
        "| 항목 | 값 | 이유 |\n"
        "|---|---|---|\n"
        "| 레버리지 ETF 최대 비중 | 25% | |\n"
        "| 과열 판정 시 레버리지 상한 | 20% | |\n"
        "| 최소 현금 비중 | 10% | |\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("STOCK_WEB_RULES_PATH", str(rules))

    result = build_rules(
        {
            "leveraged_weight_pct": 30.0, "effective_exposure_pct": 50.0,
            "cash_pct": 5.0, "short_treasury_pct": 2.0,
        },
        [{"temperature": "과열"}],
    )

    assert result is not None
    assert result["rows"][0] == ["레버리지 ETF 비중 (명목)", "30%", "/ 한도 25%"]
    assert "사용자 한도" in result["warning"]
    assert "사용자 레버리지 상한" in result["warning"]
    assert "사용자 최소값" in result["warning"]
    assert result["source"] == "rules.md"


def test_optional_local_artifacts_are_projected_without_inventing_content() -> None:
    root = new_temp_root()
    calendar = root / "data/local/calendar/events.json"
    calendar.parent.mkdir(parents=True)
    calendar.write_text(
        json.dumps({"items": [{"when": "09:00", "what": "테스트 일정", "importance": 2}]}),
        encoding="utf-8",
    )
    brief = root / "artifacts/telegram/morning_brief.json"
    brief.parent.mkdir(parents=True)
    brief.write_text(json.dumps({
        "lines": ["첫 줄", "둘째 줄"], "generated_at": "2026-09-17T08:00:00+09:00",
        "source": "local test",
    }), encoding="utf-8")
    assert home_data.build_schedule(root)["items"][0]["what"] == "테스트 일정"
    assert home_data.build_brief(root)["lines"] == ["첫 줄", "둘째 줄"]
    assert home_data.build_scanner(root)["status"] == "UNAVAILABLE"
    assert home_data.build_scanner(root)["top"] == []
