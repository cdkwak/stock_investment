from __future__ import annotations

from datetime import date
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from stock_web.api import account_page, home_cards, home_data, regime, stocks_page
from tests.unit.web import new_temp_root


def _write_parquet(root: Path, relative: str, frame: pd.DataFrame) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)


def _write_mock_account(path: Path, *, currency: str, symbol: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": 2,
        "state": "LOCAL_MOCK",
        "as_of": "2026-09-04T07:00:00+09:00",
        "last_reconciled_at": "2026-09-04T07:01:00+09:00",
        "currency": currency,
        "total_assets": 100.0,
        "securities_value": 100.0,
        "cash_balance": 0.0,
        "available_cash": None,
        "realized_pnl": None,
        "unrealized_pnl": 0.0,
        "positions": [{
            "symbol": symbol, "name": "보유 종목", "quantity": 1.0,
            "market_value": 100.0, "realized_pnl": None, "unrealized_pnl": 0.0,
        }],
        "asset_history": [],
    }), encoding="utf-8")


def test_credit_and_lending_include_basis_dates_and_lag_notes() -> None:
    root = new_temp_root()
    values = [float(index * 100) for index in range(1, 26)]
    _write_parquet(
        root,
        "data/normalized/kr_stock_lending_market_daily/year=2026/data.parquet",
        pd.DataFrame({
            "date": pd.date_range("2026-08-01", periods=25),
            "executed_shares": range(25), "returned_shares": range(25),
            "balance_shares": range(25), "balance_amount": values,
        }),
    )
    _write_parquet(
        root,
        "data/normalized/kr_credit_balance_daily/year=2026/data.parquet",
        pd.DataFrame({
            "date": ["2026-08-22", "2026-08-23"],
            "credit_financing_total": [33.2e12, 33.4e12],
        }),
    )

    result = home_cards.build_lending(root)

    assert result is not None
    assert result["as_of"] == "2026-08-25"
    assert result["lag_note"] == "공공데이터포털 대차잔고는 1거래일 뒤 발표"
    assert result["credit"] == {
        "as_of": "2026-08-23",
        "lag_note": "KOFIA 신용잔고는 2거래일 뒤 발표",
    }
    assert result["balance_amount"] == 2500.0
    assert result["d1_pct"] == pytest.approx((2500 / 2400 - 1) * 100)
    assert result["d5_pct"] == pytest.approx((2500 / 2000 - 1) * 100)
    assert result["d20_pct"] == pytest.approx((2500 / 500 - 1) * 100)
    assert result["d20_note"] is None
    assert result["trend_20d"] == values[-20:]
    assert home_cards.build_lending(new_temp_root()) is None


def test_schedule_reads_sent_front_matter_and_extracts_night_events() -> None:
    root = new_temp_root()
    brief_root = root / "artifacts/local_user/briefs"
    brief_root.mkdir(parents=True)
    (brief_root / "2026-09-04-morning.md").write_text(
        "---\nkind: morning\ngenerated_at_kst: 2026-09-04T07:30:00+09:00\nsent: true\n---\n"
        "# 아침 브리핑\n\n시장 본문\n🌙 오늘 밤\n- 21:30 미국 8월 고용보고서\n※ 유의\n",
        encoding="utf-8",
    )
    (brief_root / "2026-09-04-close.md").write_text(
        "---\nkind: close\ngenerated_at_kst: 2026-09-04T16:10:00+09:00\nsent: yes\n---\n"
        "장 마감 브리핑\n📅 일정\n22:00 미국 서비스업 지수\n"
        + "\n".join(f"본문 {index}" for index in range(42))
        + "\n출처: 로컬\n",
        encoding="utf-8",
    )

    result = home_cards.build_schedule(root, today=date(2026, 9, 4))

    assert [(brief["kind"], brief["time"]) for brief in result["briefs"]] == [
        ("close", "16:10"), ("morning", "07:30"),
    ]
    assert result["briefs"][0]["title"] == "장 마감 브리핑"
    assert result["briefs"][0]["body"].endswith("출처: 로컬")
    assert len(result["briefs"][0]["body"].splitlines()) == 40
    assert result["events"] == [
        {"time": "22:00", "text": "미국 서비스업 지수"},
        {"time": "21:30", "text": "미국 8월 고용보고서"},
    ]
    assert result["note"] == ""


def test_watchlist_matches_kb_prefixed_kr_symbol_and_us_ticker_without_quantities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = new_temp_root()
    _write_mock_account(
        root / "data/normalized/toss_account_snapshot/latest.json",
        currency="USD", symbol="SPY",
    )
    _write_mock_account(
        root / "data/local/account_snapshots/kb_self.json",
        currency="KRW", symbol="A123320",
    )
    monkeypatch.setattr(stocks_page, "build_home_watchlist", lambda _root: {
        "rows": [
            {"name": "KR ETF", "symbol": "123320", "held": False},
            {"name": "US ETF", "symbol": "spy", "held": False},
            {"name": "관심", "symbol": "QQQ", "held": False},
        ],
        "held_count": 0, "watch_count": 3,
    })
    monkeypatch.setattr(account_page, "build_account_page_data", lambda _root: {
        "manual_accounts": {"accounts": []},
    })

    result = home_cards.build_watchlist(root)

    assert [row["held"] for row in result["rows"]] == [True, True, False]
    assert result["held_count"] == 2
    assert all("quantity" not in row for row in result["rows"])
    assert all(row["weight_pct"] is None for row in result["rows"])


def test_watchlist_adds_plain_column_investor_flow_session_sums(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = new_temp_root()
    dates = pd.date_range("2026-08-01", periods=20)
    units = pd.Series(range(1, 21), dtype="int64") * 100_000_000
    _write_parquet(
        root,
        "data/normalized/kr_equity_investor_flow_daily/data.parquet",
        pd.DataFrame({
            "date": dates, "symbol": ["005930"] * 20,
            "foreign_net": units,
            "institution_net": -units * 2,
            "individual_net": [300_000_000] * 20,
            "other_corp_net": units - units,
            "total_net": -units + 300_000_000,
            "source": ["fixture"] * 20,
            "captured_at": pd.to_datetime(["2026-09-04T00:00:00Z"] * 20),
        }),
    )
    monkeypatch.setattr(stocks_page, "build_home_watchlist", lambda _root: {
        "rows": [
            {"name": "삼성전자", "symbol": "005930", "held": False},
            {"name": "미국 ETF", "symbol": "QQQ", "held": False},
        ],
        "held_count": 0, "watch_count": 2,
    })
    monkeypatch.setattr(account_page, "build_account_page_data", lambda _root: {
        "manual_accounts": {"accounts": []},
    })

    rows = home_cards.build_watchlist(root)["rows"]

    assert rows[0]["investor"] == {
        "as_of": "2026-08-20",
        "foreign_1d": 2_000_000_000,
        "institution_1d": -4_000_000_000,
        "individual_1d": 300_000_000,
        "foreign_5d": 9_000_000_000,
        "institution_5d": -18_000_000_000,
        "individual_5d": 1_500_000_000,
        "foreign_20d": 21_000_000_000,
        "institution_20d": -42_000_000_000,
        "individual_20d": 6_000_000_000,
    }
    assert "investor" not in rows[1]


def test_account_extras_use_existing_source_and_cash_flow_fields_only() -> None:
    payload = {
        "summary": {"sources": [{
            "name": "Toss", "as_of": "2026-09-04T07:00:00+09:00",
            "as_of_label": "09-04 07:00", "included": True,
            "note": "식별자 없는 로컬 스냅샷",
        }, {
            "name": "KB", "as_of": "2026-09-03T07:10:00+09:00",
            "as_of_label": "09-03 07:10", "included": False, "note": "제외",
        }]},
        "cash_flows": {"entries": [{
            "date": f"2026-09-{day:02d}", "account": "Toss", "memo": f"입금 {day}",
            "amount_krw": day * 1000,
        } for day in range(6, 0, -1)]},
    }

    result = home_cards.account_extras(payload)

    assert result["summary_rows"][0] == {
        "label": "Toss", "as_of": "09-04 07:00", "included": True,
        "note": "식별자 없는 로컬 스냅샷",
    }
    assert len(result["recent_cashflows"]) == 5
    assert result["recent_cashflows"][0] == {
        "date": "2026-09-06", "label": "Toss · 입금 6", "amount_krw": 6000,
    }


def test_regime_rows_have_evidence_flags_hints_and_percentile_labels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dates = pd.date_range("2025-09-01", periods=252)
    index_frame = pd.DataFrame({
        "date": dates, "close": range(100, 352), "disparity60": [101.0] * 252,
    })

    class Query:
        def tail(self, dataset: str, **_kwargs: object) -> pd.DataFrame:
            if dataset.endswith("kr_credit_balance_daily"):
                return pd.DataFrame({"date": dates, "credit_financing_total": range(252)})
            if dataset.endswith("kr_market_investor_trading_daily"):
                return pd.DataFrame({
                    "date": dates[-3:], "market": ["KOSPI"] * 3,
                    "foreigner_buy_amount": [1, 1, 1],
                    "foreigner_sell_amount": [2, 2, 2],
                })
            return pd.DataFrame({
                "date": dates[-64:], "dgs10": [4.0] * 64, "dgs2": [3.5] * 64,
            })

    class Index:
        def series(self, *_args: object) -> pd.DataFrame:
            return index_frame

        def asset_series(self, *_args: object) -> pd.DataFrame:
            return index_frame

    class Service:
        def __init__(self, _root: Path) -> None:
            self.query = Query()
            self.index = Index()

        def volatility(self, **_kwargs: object) -> dict[str, object]:
            return {
                "VKOSPI": {"percentile_250d": 25.0},
                "VIX": {"percentile_250d": 30.0},
            }

        def market_valuation_views(self) -> dict[str, object]:
            return {"KOSPI": SimpleNamespace(rolling_windows=(
                SimpleNamespace(window_years=5, per_percentile=20.0),
            ))}

    monkeypatch.setattr(regime, "DashboardService", Service)
    monkeypatch.setenv("STOCK_WEB_RULES_PATH", str(new_temp_root() / "missing.md"))

    result = regime.build_regime(new_temp_root(), {})
    rows = result["markets"][0]["evidence"]
    by_label = {row["label"]: row for row in rows}

    assert by_label["VKOSPI 250일 백분위"]["hint"] == "낮을수록 안정"
    assert by_label["KRX PER 5년 백분위"]["hint"] == "낮을수록 저평가"
    assert by_label["신용잔고 1년 백분위"]["hint"] == "높을수록 과열"
    assert by_label["과매도 강도"]["hint"] == "높을수록 과매도"
    assert by_label["실적 모멘텀"]["evidence"] is False
    assert by_label["KOSPI RSI14"]["evidence"] is True


def test_derivatives_translate_provider_warning_and_hide_us_missing_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from stock_data.gui import health_service, services, us_option_pcr_adapter, vix_futures_adapter

    long_warning = "Provider warning requires source verification before this metric can be displayed"
    metric = SimpleNamespace(
        displays_value=False, value=None, as_of=None, unavailable_reason=long_warning,
    )
    monkeypatch.setattr(health_service, "DailyHealthArtifactService", lambda _root: SimpleNamespace(load=lambda: None))
    monkeypatch.setattr(
        services, "DashboardService",
        lambda _root: SimpleNamespace(dashboard_metrics=lambda _view: {"KOSPI200_BASIS": metric}),
    )
    monkeypatch.setattr(
        vix_futures_adapter, "build_vix_futures_dashboard_view",
        lambda: SimpleNamespace(metric=SimpleNamespace(unavailable_reason=long_warning)),
    )
    monkeypatch.setattr(
        us_option_pcr_adapter, "current_us_option_pcr_scope_views",
        lambda: [SimpleNamespace(reason=long_warning)],
    )

    result = home_data.build_derivatives(new_temp_root())

    assert result["groups"][0]["rows"][0][1] == "출처 검증 전 · 미표시"
    assert [row[1] for row in result["groups"][1]["rows"]] == ["미표시", "미표시"]


def test_home_derivatives_show_retained_cboe_pcr_only_in_private_mode() -> None:
    root = new_temp_root()
    _write_parquet(
        root, "data/normalized/cboe_daily_pcr_daily/year=2026/data.parquet",
        pd.DataFrame({
            "date": [date(2026, 9, 4)] * 2,
            "scope": ["TOTAL", "INDEX"],
            "volume_pcr": [1.25, 0.95],
        }),
    )

    private_rows = home_data.build_derivatives(root, public_mode=False)["groups"][1]["rows"]
    public_rows = home_data.build_derivatives(root, public_mode=True)["groups"][1]["rows"]

    assert ["Cboe 거래량 PCR (거래소 합계)", "1.25 · 지수 0.95 · 09-04"] in private_rows
    assert all("Cboe" not in row[0] for row in public_rows)


def test_korean_treasury_tile_falls_back_to_newer_toss_curve() -> None:
    root = new_temp_root()
    _write_parquet(
        root,
        "data/normalized/bok_ecos_kr_treasury_yield_source_observation/year=2026/data.parquet",
        pd.DataFrame({
            "date": [pd.Timestamp("2026-08-12"), pd.Timestamp("2026-08-13")] * 2,
            "tenor": ["3Y", "3Y", "10Y", "10Y"],
            "yield_percent": [3.0, 3.1, 3.4, 3.5],
        }),
    )
    for instrument, values in (
        ("KR_BOND_3Y", [3.20, 3.25]), ("KR_BOND_10Y", [3.60, 3.70]),
    ):
        _write_parquet(
            root,
            f"data/normalized/kr_treasury_yield_daily/instrument={instrument}/year=2026/data.parquet",
            pd.DataFrame({
                "date": [pd.Timestamp("2026-09-01"), pd.Timestamp("2026-09-02")],
                "instrument": [instrument, instrument], "close": values,
            }),
        )

    tile = next(
        item for item in home_data.build_tiles(root)
        if item["name"] == "한국 3Y · 10Y"
    )

    assert tile["value"] == "3Y 3.25% · 10Y 3.70%"
    assert tile["change_label"] == "3Y +5bp · 10Y +10bp"
    assert tile["source_label"] == "Toss 국채 09-02"
