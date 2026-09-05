from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path

import pandas as pd
import pytest

from stock_web.api import account_page, datasets, home_data, stocks_page, symbol_resolver
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


def _write_account_snapshot(
    path: Path, *, total: float, securities: float, cash: float | None,
    pnl: float | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": 2,
        "state": "LOCAL_MOCK",
        "as_of": "2026-09-03T07:00:00+09:00",
        "last_reconciled_at": "2026-09-03T07:01:00+09:00",
        "currency": "KRW",
        "total_assets": total,
        "securities_value": securities,
        "cash_balance": cash,
        "available_cash": None,
        "realized_pnl": None,
        "unrealized_pnl": pnl,
        "positions": [],
        "asset_history": [],
    }), encoding="utf-8")


def test_regime_formulas_match_hand_calculations() -> None:
    score = oversold_strength(15.0, -10.0, 100.0)
    assert score is not None
    assert score[0] == 10.0
    assert dict(score[1]) == {"RSI14": 4.0, "이격": 3.0, "변동성": 3.0}
    assert oversold_strength(50.0, 0.0, 50.0) == (
        0.0, (("RSI14", 0.0), ("이격", 0.0), ("변동성", 0.0)),
    )
    assert oversold_strength(None, 0.0, 50.0) is None
    assert temperature_label(71.0, 5.0, 50.0) == "과열"
    assert temperature_label(29.0, -5.0, 50.0) == "침체"
    assert temperature_label(71.0, 0.1) == "강세"
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
    assert sections["schedule"] == {
        "briefs": [], "events": [],
        "note": "일정 출처 없음 · 브리핑의 오늘 밤 항목만 표시",
    }
    assert sections["health"]["current"] >= 1
    assert sections["health"]["labels"]["current"] == "정시"
    assert len(sections["regime"]["markets"]) == 3
    assert sections["regime"]["rules"] is None
    assert "brief" not in sections
    assert sections["scanner"]["status"] == "READY"
    assert sections["scanner"]["count"] == 1
    assert sections["flows"]["rows"]
    assert home_data.build_chart_payload(root, symbol="KOSPI", range_key="3M")["stats"]["rsi14"] == 100.0


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


def test_home_warmup_failure_stays_in_background_and_does_not_escape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = new_temp_root().resolve()
    monkeypatch.setenv("STOCK_WEB_WARMUP", "1")
    monkeypatch.setattr(
        home_data, "build_home_payload",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError("missing")),
    )
    home_data._HOME_WARM_THREADS.clear()

    thread = home_data.warm_home_payload(root)

    assert thread is not None
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_app_startup_warmup_respects_the_environment_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi.testclient import TestClient

    from stock_web.app import create_app

    root = new_temp_root().resolve()
    calls: list[tuple[Path, bool | None]] = []
    monkeypatch.setattr(
        home_data, "warm_home_payload",
        lambda path, *, public_mode=None, **_kwargs: calls.append((path, public_mode)),
    )
    monkeypatch.setenv("STOCK_WEB_WARMUP", "1")
    with TestClient(create_app(root)):
        pass
    assert calls == [(root, False)]

    monkeypatch.setenv("STOCK_WEB_WARMUP", "0")
    with TestClient(create_app(root)):
        pass
    assert calls == [(root, False)]


def test_home_watchlist_preserves_investor_flow_from_hive_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = new_temp_root()
    dates = pd.date_range("2026-08-24", periods=6)
    _write_parquet(
        root,
        "data/normalized/kr_equity_investor_flow_daily/symbol=005930/year=2026/data.parquet",
        pd.DataFrame({
            "date": dates, "symbol": ["005930"] * 6,
            "foreign_net": [100_000_000] * 6,
            "institution_net": [-200_000_000] * 6,
            "individual_net": [50_000_000] * 6,
            "other_corp_net": [50_000_000] * 6,
            "total_net": [0] * 6, "source": ["fixture"] * 6,
            "captured_at": pd.to_datetime(["2026-09-04T00:00:00Z"] * 6),
        }),
    )
    monkeypatch.setattr(stocks_page, "build_home_watchlist", lambda _root: {
        "rows": [{"name": "삼성전자", "symbol": "005930", "held": False}],
        "held_count": 0, "watch_count": 1,
    })
    monkeypatch.setattr(account_page, "build_account_page_data", lambda _root: {
        "manual_accounts": {"accounts": []},
    })

    investor = home_data.build_watchlist(root)["rows"][0]["investor"]

    assert investor["as_of"] == "2026-08-29"
    assert investor["foreign_1d"] == 100_000_000
    assert investor["institution_5d"] == -1_000_000_000
    assert investor["individual_20d"] == 300_000_000


def test_home_skhy_row_degrades_then_reads_global_equity_and_live_quote(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The repository helper is the ACL-safe tmp_path equivalent on this Windows host.
    root = new_temp_root()
    monkeypatch.setattr(symbol_resolver, "_global_equity_registry", lambda: {
        "SKHY": {
            "korean_name": "SK하이닉스(ADR)", "official_exchange": "NASDAQ",
            "security_type": "DEPOSITARY_RECEIPT", "underlying_kr_symbol": "000660",
            "expected_currency": "USD",
        },
    })
    monkeypatch.setattr(account_page, "build_account_page_data", lambda _root: {
        "manual_accounts": {"accounts": []},
    })
    symbol_resolver._SYMBOL_INDEX_CACHE.clear()
    stocks_page._SEARCH_INDEX_CACHE.clear()
    stocks_page.add_watchlist_item(root, {
        "list_id": "favorites", "market": "US 주식", "symbol": "SKHY",
    })

    missing = home_data.build_watchlist(root)["rows"][0]
    assert missing["symbol"] == "SKHY"
    assert missing["market"] == "US 주식"
    assert missing["price"] == "자료 없음"
    assert "investor" not in missing

    dates = pd.date_range("2026-07-24", periods=30, freq="B")
    close = pd.Series([100.0 + index for index in range(30)])
    _write_parquet(
        root, "data/normalized/global_equity_price_daily/symbol=SKHY/year=2026/data.parquet",
        pd.DataFrame({
            "date": dates, "symbol": ["SKHY"] * 30, "source_ticker": ["SKHY"] * 30,
            "open": close - 1, "high": close + 2, "low": close - 2, "close": close,
            "adjusted_close": close, "volume": [1_000_000] * 30,
            "currency": ["USD"] * 30, "exchange": ["NASDAQ"] * 30,
            "provider": ["fixture"] * 30,
            "retrieved_at": pd.to_datetime(["2026-09-04T00:00:00Z"] * 30),
            "adjustment_status": ["RAW_AND_ADJUSTED_RETAINED"] * 30,
        }),
    )
    datasets._CACHE.clear()
    present = home_data.build_watchlist(root)["rows"][0]
    assert present["price"] == "129.00"
    assert present["change_pct"] == pytest.approx((129 / 128 - 1) * 100)
    assert present["drawdown_pct"] == 0.0
    assert present["rsi14"] == 100.0

    quote_path = root / "artifacts/intraday/tossinvest_us_quotes_latest.json"
    quote_path.parent.mkdir(parents=True)
    quote_path.write_text(json.dumps({
        "as_of_kst": "2026-09-04T21:15:00+09:00", "provider": "tossinvest",
        "session_hint": "pre_market", "quotes": [{
            "symbol": "SKHY", "last_price": 132.0, "currency": "USD",
            "timestamp_kst": "2026-09-04T21:14:58+09:00",
        }],
    }), encoding="utf-8")
    live = home_data.build_watchlist(root)["us_live"]

    assert live["label"] == "밤사이 미국"
    assert live["session_label"] == "프리마켓"
    assert live["as_of_label"] == "21:15"
    assert live["quotes"][0]["change_pct"] == pytest.approx((132 / 129 - 1) * 100)
    assert "artifact" not in json.dumps(live, ensure_ascii=False).casefold()


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
    assert home_data.build_schedule(root)["legacy"]["items"][0]["what"] == "테스트 일정"
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
    assert tile["sub_note"] == "BOK 매매기준율 09-03 · FRED 08-28"
    assert tile["spark_source"] == "Yahoo KRW=X"


def test_sp500_futures_tile_uses_intraday_sparkline_when_es_series_is_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = new_temp_root()
    points = [
        {"t": "2026-09-04T13:00:00+09:00", "v": 7_700.0},
        {"t": "2026-09-04T13:30:00+09:00", "v": 7_702.0},
        {"t": "2026-09-04T14:00:00+09:00", "v": 7_705.0},
    ]

    def intraday(_root: Path, name: str):
        if name != "S&P 500 선물":
            return None
        return {
            "source": "Yahoo 완료 30분봉 · ES=F",
            "window": "24h 선물 · 14:00 KST",
            "points": points,
        }

    monkeypatch.setattr(home_data, "load_intraday_series", intraday)
    tile = next(
        item for item in home_data.build_tiles(root)
        if item["name"] == "S&P 500 선물"
    )

    assert tile["spark_kind"] == "intraday"
    assert tile["spark"] == points
    assert tile["window"] == "24h 선물 · 14:00 KST"
    assert tile["spark_source"] == "Yahoo 완료 30분봉 · ES=F"


def test_rate_tile_uses_basis_points_for_change_and_ma_displacement() -> None:
    values = [4.0] * 19 + [4.04]
    tile = home_data._tile_from_series(
        "미국 10Y", None,
        pd.DataFrame({
            "date": pd.date_range("2026-08-01", periods=20),
            "dgs10": values,
        }),
        "dgs10", fmt="{:.2f}%", change_kind="bp",
    )

    assert tile["change_label"] == "+4bp"
    assert tile["ma5_label"] == "+3bp"
    assert tile["ma20_label"] == "+4bp"
    assert tile["ma5_pct"] == pytest.approx(3.2)
    assert tile["ma20_pct"] == pytest.approx(3.8)


def test_usdkrw_intraday_without_previous_session_uses_bok_for_all_comparisons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = new_temp_root()
    bok_values = list(range(1_300, 1_320))
    _write_parquet(
        root, "data/normalized/bok_ecos_usd_krw_daily/year=2026/data.parquet",
        pd.DataFrame({
            "date": pd.date_range("2026-08-10", periods=20),
            "rate_krw_per_usd": bok_values,
        }),
    )
    _write_parquet(
        root, "data/normalized/fred_usd_fx_daily/year=2026/data.parquet",
        pd.DataFrame({
            "date": pd.date_range("2026-08-30", periods=3),
            "dexkous": [1_000.0, 1_001.0, 1_002.0],
        }),
    )

    def intraday(_root: Path, name: str):
        if name != "USD/KRW":
            return None
        return {
            "source": "Yahoo KRW=X", "window": "24h",
            "points": [
                {"t": "2026-09-03T00:00:00Z", "v": 1_338.0},
                {"t": "2026-09-03T01:00:00Z", "v": 1_339.0},
                {"t": "2026-09-03T02:00:00Z", "v": 1_340.0},
            ],
        }

    monkeypatch.setattr(home_data, "load_intraday_series", intraday)
    tile = next(item for item in home_data.build_tiles(root) if item["name"] == "USD/KRW")

    assert tile["change_pct"] == pytest.approx((1_340 / 1_319 - 1) * 100)
    assert tile["ma5_pct"] == pytest.approx((1_340 / 1_317 - 1) * 100)
    assert tile["ma20_pct"] == pytest.approx((1_340 / 1_309.5 - 1) * 100)
    assert tile["daily_reference_source"] == "BOK 매매기준율"
    assert tile["daily_reference_date"] == "2026-08-29"
    assert tile["sub_note"].startswith("BOK 매매기준율 08-29")


def test_home_account_marks_unknown_cash_and_defaults_short_history_to_all() -> None:
    root = new_temp_root()
    _write_account_snapshot(
        root / "data/normalized/toss_account_snapshot/latest.json",
        total=1_000_000, securities=900_000, cash=100_000, pnl=12_000,
    )
    _write_account_snapshot(
        root / "data/local/account_snapshots/kb_self.json",
        total=2_000_000, securities=2_000_000, cash=None, pnl=8_000,
    )

    account = home_data.build_account(root)

    assert account["cash_unknown"] is True
    assert account["cash_pct"] is None
    assert account["broker_reported_pnl_krw"] == 20_000
    assert account["period_label"] == "ALL"
    assert [(row["label"], row["as_of"]) for row in account["summary_rows"][:2]] == [
        ("Toss", "09-03 07:00"), ("KB", "09-03 07:00"),
    ]
    assert account["recent_cashflows"] == []


def test_regime_cash_label_keeps_unknown_cash_separate_from_treasury_percentage() -> None:
    regime = {"rules": {"rows": [
        ["현금 · 단기국채", "표시 불가 · 0%", "/ 최소 0%"],
    ]}}

    result = home_data._normalize_regime_cash_label(
        regime, {"cash_unknown": True, "short_treasury_pct": 0.0},
    )

    assert result["rules"]["rows"][0] == [
        "현금 · 단기국채", "현금 입력 없음 · 단기국채 0%", "",
    ]


def test_home_regime_includes_active_kr_research_status_line() -> None:
    root = new_temp_root()
    path = root / "artifacts/research/rule_leaderboard/latest.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({
        "schema_version": 1,
        "candidates": [{
            "id": "kr_dd_ladder_2", "name": "낙폭 2단계 (KR)",
            "side": "drawdown", "basket": "KR", "status": "active",
            "current": {
                "date": "2026-09-03", "score": 1, "level": 1, "max_level": 2,
                "exposure": .87, "analog": {"n": 52, "mean_60": .075, "hit_60": .63},
            },
        }],
    }, ensure_ascii=False), encoding="utf-8")

    regime = home_data._attach_research_current(root, {"markets": [], "rules": None})

    assert regime["research_current"] == [
        "규칙 현재 상태 · 낙폭 2단계 (KR): 1/2단계 · 노출 87% · 과거 동일 단계 60일 +7.5%",
    ]


def test_home_javascript_formatters_cover_tiny_shares_compact_krw_and_pnl_fallback() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    app_js = Path(__file__).parents[3] / "src/stock_web/static/app.js"
    script = (
        f"global.window={{}};const f=require({json.dumps(str(app_js))});"
        "console.log(JSON.stringify({"
        "tiny:f.formatSharePercent(0.049),"
        "cash:f.formatSharePercent(0),"
        "krw:f.formatCompactKorean(650000),"
        "eokUp:f.signedEok(1250000000),"
        "eokDown:f.signedEok(-800000000),"
        "pnl:f.brokerReportedPnl({broker_reported_pnl_krw:1234},{}),"
        "week:f.aggregateCandles(["
        "{t:'2026-08-31',o:10,h:12,l:9,c:11,v:100},"
        "{t:'2026-09-01',o:11,h:14,l:10,c:13,v:200},"
        "{t:'2026-09-07',o:13,h:15,l:12,c:14,v:300}], 'week'),"
        "month:f.aggregateCandles(["
        "{t:'2026-08-31',o:10,h:12,l:9,c:11,v:100},"
        "{t:'2026-09-01',o:11,h:14,l:10,c:13,v:200}], 'month')"
        "}));"
    )
    completed = subprocess.run(
        [node, "-e", script], check=True, capture_output=True, text=True,
        encoding="utf-8",
    )

    result = json.loads(completed.stdout)
    assert result["tiny"] == "<0.1%"
    assert result["cash"] == "0%"
    assert result["krw"] == "65만"
    assert result["eokUp"] == "+12.5"
    assert result["eokDown"] == "−8.0"
    assert result["pnl"] == 1234
    assert result["week"] == [
        {"t": "2026-09-01", "o": 10, "h": 14, "l": 9, "c": 13, "v": 300},
        {"t": "2026-09-07", "o": 13, "h": 15, "l": 12, "c": 14, "v": 300},
    ]
    assert result["month"] == [
        {"t": "2026-08-31", "o": 10, "h": 12, "l": 9, "c": 11, "v": 100},
        {"t": "2026-09-01", "o": 11, "h": 14, "l": 10, "c": 13, "v": 200},
    ]


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


def test_korean_equity_reader_prefers_canonical_overlap_and_appends_only_newer_provisional() -> None:
    root = make_project(new_temp_root())
    _write_parquet(
        root,
        "data/normalized/kr_equity_price_provisional_daily/market=KOSPI/year=2026/data.parquet",
        pd.DataFrame({
            "date": pd.to_datetime(["2026-09-02", "2026-09-03"]),
            "market": ["KOSPI", "KOSPI"],
            "symbol": ["005930", "005930"],
            "open": [888, 199], "high": [889, 205], "low": [887, 195],
            "close": [888, 200], "volume": [10, 20],
            "trading_value": [8_880, 4_000],
            "source": ["pykrx", "pykrx"],
            "source_operation": [
                "stock.get_market_ohlcv_by_ticker",
                "stock.get_market_ohlcv_by_ticker",
            ],
            "source_date": pd.to_datetime(["2026-09-02", "2026-09-03"]),
            "provisional": [True, True],
            "observed_at": pd.to_datetime([
                "2026-09-02T11:31:00Z", "2026-09-03T11:31:00Z",
            ]),
        }),
    )

    frame, _name = home_data._ohlcv(root, "005930")
    payload = home_data.build_chart_payload(root, symbol="005930", range_key="3M")

    assert frame is not None
    assert frame.iloc[-2]["date"].strftime("%Y-%m-%d") == "2026-09-02"
    assert frame.iloc[-2]["close"] != 888
    assert not bool(frame.iloc[-2]["provisional"])
    assert frame.iloc[-1][["date", "close", "provisional"]].tolist() == [
        pd.Timestamp("2026-09-03"), 200, True,
    ]
    assert payload["as_of"] == "2026-09-03"
    assert payload["provisional_dates"] == ["2026-09-03"]


def test_vix_term_structure_rows_from_derived_dataset() -> None:
    import datetime as dt

    import pandas as pd

    from stock_web.api.home_cards import build_vix_term_structure_rows

    project_root = new_temp_root()
    assert build_vix_term_structure_rows(project_root) == [["VIX 기간구조", "미표시"]]
    root = project_root / "data/derived/us_vix_term_structure_daily/year=2026"
    root.mkdir(parents=True)
    pd.DataFrame({
        "date": [dt.date(2026, 9, 1), dt.date(2026, 9, 2), dt.date(2026, 9, 3)],
        "vix": [16.34, 15.20, None], "vix9d": [14.33, 12.57, 11.68],
        "vix3m": [18.33, 17.73, 17.42], "vix6m": [20.56, 20.36, 19.78],
        "skew": [149.23, 144.12, 143.09],
        "ratio_1m_3m": [0.891435, 0.857304, None], "ratio_9d_1m": [0.877, 0.827, None],
        "regime": ["contango", "contango", None], "pct_rank_252": [0.670635, 0.476190, None],
    }).to_parquet(root / "data.parquet", index=False)
    rows = build_vix_term_structure_rows(project_root)
    assert rows[0][0] == "VIX 기간구조"
    assert rows[0][1] == "콘탱고 · 1M/3M 0.86 · 1년 백분위 48% · 9D 12.6 · 1M 15.2 · 3M 17.7 · 09-02"
    assert rows[1] == ["SKEW", "143.1 · 09-03"]
