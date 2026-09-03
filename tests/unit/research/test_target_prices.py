from __future__ import annotations

from datetime import date, datetime, timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys
from uuid import uuid4

import pandas as pd
import pytest

from stock_data.contracts.research_target_prices import (
    RESEARCH_TARGET_PRICE_CONSENSUS,
)
from stock_data.orchestration.daily_operations import DATASET_UNIVERSE
from stock_data.orchestration.dataset_universe import (
    AutomationPolicy,
    ConsumerEligibility,
    DatasetRefreshClass,
    GuiUse,
    PredictivePitStatus,
)
from stock_data.research.target_prices import (
    KOREAN_UNAVAILABLE_SOURCE,
    TargetPriceConsensusError,
    YAHOO_SOURCE,
    append_target_price_vintages_atomic,
    korean_unavailable_row,
    load_watchlist,
    parse_yahoo_financial_data,
    read_target_price_consensus,
    rows_to_frame,
    validate_target_price_consensus,
)


@pytest.fixture
def yahoo_quote_summary_payload() -> dict[str, object]:
    return {
        "quoteSummary": {
            "result": [{
                "financialData": {
                    "targetMeanPrice": {"raw": 225.5, "fmt": "225.50"},
                    "targetHighPrice": {"raw": 275.0, "fmt": "275.00"},
                    "targetLowPrice": {"raw": 180.0, "fmt": "180.00"},
                    "numberOfAnalystOpinions": {"raw": 42, "fmt": "42"},
                    "recommendationMean": {"raw": 1.8, "fmt": "1.80"},
                    "financialCurrency": "USD",
                }
            }],
            "error": None,
        }
    }


@pytest.fixture
def research_tmp_path() -> Path:
    path = Path(".tmp/agents/target_price_unit_tests") / uuid4().hex
    path.mkdir(parents=True)
    try:
        yield path
    finally:
        shutil.rmtree(path)


def test_fixture_quote_summary_parse_preserves_consensus_fields(
    yahoo_quote_summary_payload: dict[str, object],
) -> None:
    retrieved_at = datetime(2026, 9, 3, 1, 2, 3, tzinfo=timezone.utc)

    row = parse_yahoo_financial_data(
        yahoo_quote_summary_payload,
        symbol="nvda",
        market="US",
        currency="USD",
        run_date=date(2026, 9, 3),
        retrieved_at=retrieved_at,
    )

    assert row == {
        "date": "2026-09-03",
        "symbol": "NVDA",
        "market": "US",
        "source": YAHOO_SOURCE,
        "target_mean": 225.5,
        "target_high": 275.0,
        "target_low": 180.0,
        "analyst_count": 42,
        "recommendation_mean": 1.8,
        "currency": "USD",
        "retrieved_at": retrieved_at,
        "terms_ref": "docs/data/sources/TARGET_PRICE_CONSENSUS.md#yahoo-finance-us",
    }


def test_contract_validation_and_atomic_append_preserve_prior_vintage(
    research_tmp_path: Path,
    yahoo_quote_summary_payload: dict[str, object],
) -> None:
    first_clock = datetime(2026, 9, 3, 2, tzinfo=timezone.utc)
    us = parse_yahoo_financial_data(
        yahoo_quote_summary_payload,
        symbol="NVDA",
        market="US",
        currency="USD",
        run_date=date(2026, 9, 3),
        retrieved_at=first_clock,
    )
    kr_security = load_watchlist(_write_watchlist(
        research_tmp_path / "kr.json",
        [{"market": "KOSPI", "symbol": "005930", "name": "삼성전자", "isin": "KR7005930003"}],
    ))[0]
    kr = korean_unavailable_row(
        kr_security,
        run_date=date(2026, 9, 3),
        retrieved_at=first_clock,
    )
    first = rows_to_frame([us, kr])

    assert tuple(first.columns) == RESEARCH_TARGET_PRICE_CONSENSUS.column_names
    validate_target_price_consensus(first)
    root = research_tmp_path / "normalized"
    append_target_price_vintages_atomic(first, root)

    second = dict(us)
    second["date"] = "2026-09-04"
    second["target_mean"] = 230.0
    second["retrieved_at"] = datetime(2026, 9, 4, 2, tzinfo=timezone.utc)
    append_target_price_vintages_atomic(rows_to_frame([second]), root)
    stored = read_target_price_consensus(root)

    assert stored[["date", "symbol"]].to_dict("records") == [
        {"date": "2026-09-03", "symbol": "005930"},
        {"date": "2026-09-03", "symbol": "NVDA"},
        {"date": "2026-09-04", "symbol": "NVDA"},
    ]
    assert pd.isna(stored.loc[stored["symbol"].eq("005930"), "target_mean"].item())
    assert stored.loc[stored["symbol"].eq("NVDA"), ["date", "target_mean"]].to_dict("records") == [
        {"date": "2026-09-03", "target_mean": 225.5},
        {"date": "2026-09-04", "target_mean": 230.0},
    ]
    assert stored.loc[stored["symbol"].eq("005930"), "source"].item() == KOREAN_UNAVAILABLE_SOURCE
    with pytest.raises(TargetPriceConsensusError, match="refusing to overwrite"):
        append_target_price_vintages_atomic(first.iloc[[0]].copy(), root)


def _write_watchlist(path: Path, items: list[dict[str, object]]) -> Path:
    path.write_text(
        json.dumps({"lists": [{"name": "synthetic", "items": items}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def test_cli_dry_run_prints_exact_synthetic_watchlist_requests(
    research_tmp_path: Path,
) -> None:
    watchlist = _write_watchlist(research_tmp_path / "watchlists.json", [
        {
            "market": "US ETF", "symbol": "SPY", "name": "SPDR S&P 500",
            "isin": "US78462F1030", "currency": "USD",
        },
        {
            "market": "KOSPI", "symbol": "005930", "name": "삼성전자",
            "isin": "KR7005930003", "currency": "KRW",
        },
        {
            "market": "NASDAQ", "symbol": "NVDA", "name": "NVIDIA",
            "isin": "US67066G1040", "currency": "USD",
        },
    ])
    project_root = Path(__file__).resolve().parents[3]
    completed = subprocess.run(
        [
            sys.executable,
            str(project_root / "scripts/research/collect_target_prices.py"),
            "--project-root", str(research_tmp_path),
            "--watchlist", str(watchlist),
            "--run-date", "2026-09-03",
            "--dry-run",
        ],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    plan = json.loads(completed.stdout)

    assert plan["dry_run"] is True
    assert plan["network_call_count"] == 2
    assert plan["requests"] == [
        {
            "currency": "USD",
            "headers": {"User-Agent": "stock-investment-rev1/0.1"},
            "market": "US ETF",
            "method": "GET",
            "params": {"modules": "financialData"},
            "symbol": "SPY",
            "timeout_seconds": 30,
            "url": "https://query1.finance.yahoo.com/v10/finance/quoteSummary/SPY",
        },
        {
            "currency": "USD",
            "headers": {"User-Agent": "stock-investment-rev1/0.1"},
            "market": "NASDAQ",
            "method": "GET",
            "params": {"modules": "financialData"},
            "symbol": "NVDA",
            "timeout_seconds": 30,
            "url": "https://query1.finance.yahoo.com/v10/finance/quoteSummary/NVDA",
        },
    ]
    assert plan["unavailable"] == [{
        "market": "KOSPI",
        "status": "출처 없음 — 표시 불가",
        "symbol": "005930",
    }]
    assert not (research_tmp_path / "data").exists()


def test_dataset_universe_is_manual_display_reference_only() -> None:
    spec = DATASET_UNIVERSE["research_target_price_consensus"]

    assert spec.primary_classification is DatasetRefreshClass.RESEARCH_ONLY
    assert spec.automation_policy is AutomationPolicy.RESEARCH_ONLY
    assert spec.automation_enabled is False
    assert spec.gui_use is GuiUse.DESCRIPTIVE
    assert spec.display_consumer_eligibility is ConsumerEligibility.LIMITED
    assert spec.predictive_pit_status is PredictivePitStatus.RESEARCH_ONLY
    assert spec.predictive_consumer_eligibility is ConsumerEligibility.BLOCKED
