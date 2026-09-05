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
import requests

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
    AVAILABLE,
    KOREAN_UNAVAILABLE_SOURCE,
    NO_COVERAGE,
    NOT_APPLICABLE_ETF,
    NOT_COLLECTED,
    TARGET_PRICE_CONSENSUS,
    UNAVAILABLE_SOURCE,
    TargetPriceConsensusError,
    YAHOO_SOURCE,
    append_target_price_vintages_atomic,
    build_request_plan,
    collect_yahoo_rows,
    korean_unavailable_row,
    load_watchlist,
    parse_yahoo_financial_data,
    read_target_price_consensus,
    rows_to_frame,
    validate_target_price_consensus,
)
from stock_data.storage.contract_parquet import write_dataset_atomic


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
        "status": AVAILABLE,
        "target_mean": 225.5,
        "target_high": 275.0,
        "target_low": 180.0,
        "analyst_count": 42,
        "recommendation_mean": 1.8,
        "currency": "USD",
        "retrieved_at": retrieved_at,
        "terms_ref": "docs/data/sources/TARGET_PRICE_CONSENSUS.md#yahoo-finance-us",
    }


def test_legacy_row_without_status_is_inferred_as_available(
    yahoo_quote_summary_payload: dict[str, object],
) -> None:
    row = parse_yahoo_financial_data(
        yahoo_quote_summary_payload, symbol="NVDA", market="US", currency="USD",
        run_date=date(2026, 9, 3),
        retrieved_at=datetime(2026, 9, 3, tzinfo=timezone.utc),
    )
    row.pop("status")

    frame = rows_to_frame([row])

    assert frame["status"].item() == AVAILABLE


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
        [{
            "market": "KRX", "symbol": "123320", "name": "TIGER 레버리지",
            "isin": "KR7123320005", "security_type": "ETF",
        }],
    ))[0]
    kr = korean_unavailable_row(
        kr_security,
        run_date=date(2026, 9, 3),
        retrieved_at=first_clock,
    )
    first = rows_to_frame([us, kr])

    assert tuple(first.columns) == TARGET_PRICE_CONSENSUS.column_names
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
        {"date": "2026-09-03", "symbol": "123320"},
        {"date": "2026-09-03", "symbol": "NVDA"},
        {"date": "2026-09-04", "symbol": "NVDA"},
    ]
    assert pd.isna(stored.loc[stored["symbol"].eq("123320"), "target_mean"].item())
    assert stored.loc[stored["symbol"].eq("NVDA"), ["date", "target_mean"]].to_dict("records") == [
        {"date": "2026-09-03", "target_mean": 225.5},
        {"date": "2026-09-04", "target_mean": 230.0},
    ]
    unavailable = stored.loc[stored["symbol"].eq("123320")].iloc[0]
    assert unavailable["source"] == KOREAN_UNAVAILABLE_SOURCE
    assert unavailable["status"] == UNAVAILABLE_SOURCE
    with pytest.raises(TargetPriceConsensusError, match="refusing to overwrite"):
        append_target_price_vintages_atomic(first.iloc[[0]].copy(), root)


def test_v1_dataset_is_backward_read_with_inferred_status(
    research_tmp_path: Path,
    yahoo_quote_summary_payload: dict[str, object],
) -> None:
    row = parse_yahoo_financial_data(
        yahoo_quote_summary_payload, symbol="NVDA", market="US", currency="USD",
        run_date=date(2026, 9, 3),
        retrieved_at=datetime(2026, 9, 3, tzinfo=timezone.utc),
    )
    row.pop("status")
    legacy = pd.DataFrame(
        [row], columns=RESEARCH_TARGET_PRICE_CONSENSUS.column_names,
    )
    legacy["retrieved_at"] = pd.to_datetime(legacy["retrieved_at"], utc=True)
    root = research_tmp_path / "v1"
    write_dataset_atomic(
        legacy, root, RESEARCH_TARGET_PRICE_CONSENSUS, lambda _frame: None,
    )

    upgraded = read_target_price_consensus(root)

    assert tuple(upgraded.columns) == TARGET_PRICE_CONSENSUS.column_names
    assert upgraded["status"].item() == AVAILABLE


def _write_watchlist(path: Path, items: list[dict[str, object]]) -> Path:
    path.write_text(
        json.dumps({"lists": [{"name": "synthetic", "items": items}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


class _FakeResponse:
    def __init__(
        self, status_code: int, *, payload: object | None = None,
        content: bytes | None = None, content_type: str = "application/json",
    ) -> None:
        self.status_code = status_code
        self._payload = payload
        self.content = (
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if content is None and payload is not None else content or b""
        )
        self.headers = {"Content-Type": content_type}

    @property
    def text(self) -> str:
        return self.content.decode("utf-8")

    def json(self) -> object:
        if self._payload is not None:
            return self._payload
        return json.loads(self.content)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"fixture HTTP {self.status_code}")


class _FakeSession:
    def __init__(self, responses: list[_FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, object]] = []
        self.cookies = requests.cookies.RequestsCookieJar()

    def get(
        self, url: str, *, params: dict[str, str], headers: dict[str, str],
        timeout: int,
    ) -> _FakeResponse:
        self.calls.append({
            "url": url, "params": dict(params), "headers": dict(headers),
            "timeout": timeout,
        })
        response = self.responses.pop(0)
        if url == "https://fc.yahoo.com":
            self.cookies.set("A3", "private-cookie-fixture", domain=".yahoo.com")
        return response


def _planned_request(
    tmp_path: Path, *, market: str, symbol: str, security_type: str | None = None,
):
    item: dict[str, object] = {
        "market": market, "symbol": symbol,
        "currency": "KRW" if market in {"KOSPI", "KOSDAQ", "KRX"} else "USD",
    }
    if security_type is not None:
        item["security_type"] = security_type
    securities = load_watchlist(_write_watchlist(tmp_path / f"{symbol}.json", [item]))
    return build_request_plan(securities)[0]


def test_crumb_handshake_precedes_data_and_crumb_is_not_in_call_records(
    research_tmp_path: Path,
    yahoo_quote_summary_payload: dict[str, object],
) -> None:
    securities = load_watchlist(_write_watchlist(research_tmp_path / "handshake.json", [
        {"market": "NASDAQ", "symbol": "NVDA", "currency": "USD"},
        {"market": "NASDAQ", "symbol": "AMD", "currency": "USD"},
    ]))
    session = _FakeSession([
        _FakeResponse(404, content=b"cookie bootstrap", content_type="text/html"),
        _FakeResponse(200, content=b"fixture-crumb/1", content_type="text/plain"),
        _FakeResponse(200, payload=yahoo_quote_summary_payload),
        _FakeResponse(200, payload=yahoo_quote_summary_payload),
    ])
    landing = research_tmp_path / "landing"
    sleep_delays: list[float] = []

    rows = collect_yahoo_rows(
        build_request_plan(securities), run_date=date(2026, 9, 5),
        landing_run_root=landing,
        session=session, sleep=sleep_delays.append, min_interval_seconds=1,
    )

    assert [call["url"] for call in session.calls] == [
        "https://fc.yahoo.com",
        "https://query2.finance.yahoo.com/v1/test/getcrumb",
        "https://query2.finance.yahoo.com/v10/finance/quoteSummary/NVDA",
        "https://query2.finance.yahoo.com/v10/finance/quoteSummary/AMD",
    ]
    assert all(call["params"] == {
        "modules": "financialData", "crumb": "fixture-crumb/1",
    } for call in session.calls[2:])
    assert len(sleep_delays) == 3
    assert all(0 < delay <= 1 for delay in sleep_delays)
    assert [row["status"] for row in rows] == [AVAILABLE, AVAILABLE]
    records = "\n".join(
        path.read_text(encoding="utf-8") for path in landing.rglob("call.json")
    )
    assert "fixture-crumb/1" not in records
    assert "private-cookie-fixture" not in records
    assert "Set-Cookie" not in records


def test_korean_market_identity_maps_to_yahoo_exchange_suffix(
    research_tmp_path: Path,
) -> None:
    watchlist = load_watchlist(_write_watchlist(research_tmp_path / "kr-map.json", [
        {"market": "KOSPI", "symbol": "005930", "currency": "KRW"},
        {"market": "KOSDAQ", "symbol": "035720", "currency": "KRW"},
        {"market": "KRX", "symbol": "123320", "currency": "KRW", "security_type": "ETF"},
    ]))

    plan = build_request_plan(watchlist)

    assert [(row.symbol, row.provider_symbol) for row in plan] == [
        ("005930", "005930.KS"), ("035720", "035720.KQ"),
    ]
    assert korean_unavailable_row(
        watchlist[2], run_date=date(2026, 9, 5),
        retrieved_at=datetime(2026, 9, 5, tzinfo=timezone.utc),
    )["status"] == UNAVAILABLE_SOURCE


def test_korean_valid_payload_is_available_krw(
    yahoo_quote_summary_payload: dict[str, object],
) -> None:
    payload = json.loads(json.dumps(yahoo_quote_summary_payload))
    payload["quoteSummary"]["result"][0]["financialData"]["financialCurrency"] = "KRW"

    row = parse_yahoo_financial_data(
        payload, symbol="005930", market="KOSPI", currency="KRW",
        run_date=date(2026, 9, 5),
        retrieved_at=datetime(2026, 9, 5, tzinfo=timezone.utc),
    )

    assert (row["status"], row["currency"], row["analyst_count"]) == (
        AVAILABLE, "KRW", 42,
    )
    validate_target_price_consensus(rows_to_frame([row]))


def test_etf_http_404_is_not_applicable_instead_of_failure(
    research_tmp_path: Path,
) -> None:
    request = _planned_request(
        research_tmp_path, market="US ETF", symbol="SOXL", security_type="ETF",
    )
    session = _FakeSession([
        _FakeResponse(404, content=b"cookie bootstrap", content_type="text/html"),
        _FakeResponse(200, content=b"fixture-crumb", content_type="text/plain"),
        _FakeResponse(404, payload={
            "quoteSummary": {"result": None, "error": {"code": "Not Found"}},
        }),
    ])

    rows = collect_yahoo_rows(
        [request], run_date=date(2026, 9, 5),
        landing_run_root=research_tmp_path / "landing-404",
        session=session, min_interval_seconds=0,
    )

    assert rows[0]["status"] == NOT_APPLICABLE_ETF
    assert all(rows[0][field] is None for field in (
        "target_mean", "target_high", "target_low", "analyst_count",
        "recommendation_mean",
    ))


def test_non_fund_http_failure_stops_before_the_next_security(
    research_tmp_path: Path,
) -> None:
    securities = load_watchlist(_write_watchlist(research_tmp_path / "two.json", [
        {"market": "NASDAQ", "symbol": "NVDA", "currency": "USD"},
        {"market": "NASDAQ", "symbol": "AMD", "currency": "USD"},
    ]))
    session = _FakeSession([
        _FakeResponse(404, content=b"cookie bootstrap", content_type="text/html"),
        _FakeResponse(200, content=b"fixture-crumb", content_type="text/plain"),
        _FakeResponse(500, content=b"upstream failed", content_type="text/plain"),
    ])

    with pytest.raises(TargetPriceConsensusError, match="quoteSummary HTTP 500"):
        collect_yahoo_rows(
            build_request_plan(securities), run_date=date(2026, 9, 5),
            landing_run_root=research_tmp_path / "landing-failure",
            session=session, min_interval_seconds=0,
        )

    assert len(session.calls) == 3


def test_empty_financial_data_for_etf_is_not_applicable() -> None:
    row = parse_yahoo_financial_data(
        {"quoteSummary": {"result": [{"financialData": {}}], "error": None}},
        symbol="SOXL", market="US ETF", currency="USD",
        run_date=date(2026, 9, 5),
        retrieved_at=datetime(2026, 9, 5, tzinfo=timezone.utc),
        is_fund_product=True,
    )

    assert row["status"] == NOT_APPLICABLE_ETF


@pytest.mark.parametrize("analyst_value", [None, {"raw": 0, "fmt": "0"}])
def test_zero_or_missing_analyst_count_is_no_coverage(
    analyst_value: object,
) -> None:
    row = parse_yahoo_financial_data(
        {"quoteSummary": {"result": [{"financialData": {
            "numberOfAnalystOpinions": analyst_value,
            "financialCurrency": "USD",
        }}], "error": None}},
        symbol="NVDA", market="NASDAQ", currency="USD",
        run_date=date(2026, 9, 5),
        retrieved_at=datetime(2026, 9, 5, tzinfo=timezone.utc),
    )

    assert row["status"] == NO_COVERAGE
    assert row["analyst_count"] in (None, 0)


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
    assert plan["network_call_count"] == 5
    assert plan["handshake"] == [
        {"capture": True, "method": "GET", "url": "https://fc.yahoo.com"},
        {
            "capture": True, "method": "GET",
            "url": "https://query2.finance.yahoo.com/v1/test/getcrumb",
        },
    ]
    assert plan["requests"] == [
        {
            "currency": "USD",
            "headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            "is_fund_product": True,
            "market": "US ETF",
            "method": "GET",
            "params": {"modules": "financialData"},
            "provider_symbol": "SPY",
            "status": NOT_COLLECTED,
            "symbol": "SPY",
            "timeout_seconds": 30,
            "url": "https://query2.finance.yahoo.com/v10/finance/quoteSummary/SPY",
        },
        {
            "currency": "KRW",
            "headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            "is_fund_product": False,
            "market": "KOSPI",
            "method": "GET",
            "params": {"modules": "financialData"},
            "provider_symbol": "005930.KS",
            "status": NOT_COLLECTED,
            "symbol": "005930",
            "timeout_seconds": 30,
            "url": "https://query2.finance.yahoo.com/v10/finance/quoteSummary/005930.KS",
        },
        {
            "currency": "USD",
            "headers": {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            "is_fund_product": False,
            "market": "NASDAQ",
            "method": "GET",
            "params": {"modules": "financialData"},
            "provider_symbol": "NVDA",
            "status": NOT_COLLECTED,
            "symbol": "NVDA",
            "timeout_seconds": 30,
            "url": "https://query2.finance.yahoo.com/v10/finance/quoteSummary/NVDA",
        },
    ]
    assert plan["unavailable"] == []
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
