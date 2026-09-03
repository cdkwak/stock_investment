from __future__ import annotations

import json
from pathlib import Path

import time

import pandas as pd
import pytest

from stock_web.api import home_data
from stock_web.api.regime import (
    build_rules,
    global_risk_temperature,
    oversold_strength,
    temperature_label,
)
from tests.unit.web import make_project, new_temp_root


def _write_parquet(root: Path, relative: str, frame: pd.DataFrame) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)


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
    now = [100.0]
    monkeypatch.setattr(home_data.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(
        home_data, "_build_home_payload_uncached",
        lambda path: calls.append(path) or {"root": str(path), "call": len(calls)},
    )
    home_data._HOME_CACHE.clear()

    first = home_data.build_home_payload(root)
    now[0] = 120.0
    second = home_data.build_home_payload(root)
    assert first is second and calls == [root]

    # After the TTL the stale document is returned at once and one background rebuild runs.
    now[0] = 161.0
    third = home_data.build_home_payload(root)
    assert third is first
    deadline = time.time() + 5
    while home_data._HOME_REFRESHING and time.time() < deadline:
        time.sleep(0.01)
    assert calls == [root, root]
    fourth = home_data.build_home_payload(root)
    assert fourth["call"] == 2


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
    brief_payload = home_data.build_brief(root)
    assert brief_payload["lines"] == ["첫 줄", "둘째 줄"]
    assert brief_payload["meta"] == "09-17 08:00 · local test"
    assert home_data.build_scanner(root)["status"] == "UNAVAILABLE"
    assert home_data.build_scanner(root)["top"] == []


def test_usdkrw_tile_keeps_intraday_value_and_labels_bok_and_fred(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = new_temp_root()
    _write_parquet(
        root, "data/normalized/bok_ecos_usd_krw_daily/year=2026/data.parquet",
        pd.DataFrame({
            "date": [pd.Timestamp("2026-09-03")],
            "rate_krw_per_usd": [1_337.50],
        }),
    )
    _write_parquet(
        root, "data/normalized/fred_usd_fx_daily/year=2026/data.parquet",
        pd.DataFrame({
            "date": [pd.Timestamp("2026-08-28")], "dexkous": [1_330.25],
        }),
    )

    def intraday(_root: Path, name: str):
        if name != "USD/KRW":
            return None
        return {
            "source": "Yahoo KRW=X",
            "window": "24h",
            "points": [
                {"t": "2026-09-02T06:00:00Z", "v": 1330.0},
                {"t": "2026-09-03T05:00:00Z", "v": 1338.0},
                {"t": "2026-09-03T06:00:00Z", "v": 1339.25},
            ],
        }

    monkeypatch.setattr(home_data, "load_intraday_series", intraday)
    tile = next(item for item in home_data.build_tiles(root) if item["name"] == "USD/KRW")

    assert tile["value"] == "1,339.25"
    assert tile["sub_note"] == (
        "BOK 매매기준율 09-03: 1,337.50 · FRED 08-28"
    )
    assert tile["spark_source"] == "Yahoo KRW=X"


def test_home_account_fx_prefers_newer_bok_and_keeps_source_as_appended_field() -> None:
    root = new_temp_root()
    _write_parquet(
        root, "data/normalized/fred_usd_fx_daily/year=2026/data.parquet",
        pd.DataFrame({"date": [pd.Timestamp("2026-08-28")], "dexkous": [1330.0]}),
    )
    _write_parquet(
        root, "data/normalized/bok_ecos_usd_krw_daily/year=2026/data.parquet",
        pd.DataFrame({
            "date": [pd.Timestamp("2026-09-03")],
            "rate_krw_per_usd": [1337.5],
        }),
    )

    frame, value, observed, source = home_data._latest_fx(root)

    assert not frame.empty
    assert (value, observed, source) == (
        1337.5, "2026-09-03", "BOK 매매기준율 09-03",
    )
