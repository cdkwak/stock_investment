from datetime import datetime, timezone
import json
from pathlib import Path

import pandas as pd
import pytest

from stock_data.contracts.toss_short_watchlist import (
    TOSS_EQUITY_SHORT_WATCHLIST_DAILY,
    TOSS_SHORT_SOURCE_SCOPE,
    TOSS_SHORT_WATCHLIST_VERSION,
)
from stock_data.orchestration.toss_short_watchlist_daily import (
    CALL_BUDGET,
    pre_network_action,
    reconcile_official_overlap,
    refresh_toss_short_watchlist_daily,
    request_plan,
    stage_exact_watchlist,
    validate_watchlist_dataset,
)
from stock_data.providers.tossinvest import TossInvestAPIResponse, TossInvestRateLimit
from stock_data.providers.tossinvest.historical import normalize_short_selling
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic


OBSERVED = datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc)


def _source(symbol: str, day: str = "2026-08-19") -> pd.DataFrame:
    records = [{
        "date": day,
        "updatedAt": f"{day}T18:14:05+09:00",
        "shortSellingVolume": "100",
        "shortSellingAmount": "1000000",
        "shortSellingVolumeRate": "0.01",
        "shortSellingAmountRate": "0.02",
    }]
    return normalize_short_selling(records, symbol=symbol, collected_at=OBSERVED)


def _staged(day: str = "2026-08-19") -> pd.DataFrame:
    return stage_exact_watchlist(
        {"005930": _source("005930", day), "000660": _source("000660", day)},
        target_date=day,
    )


def _official(day: str = "2026-08-19") -> pd.DataFrame:
    return pd.DataFrame([
        {"date": day, "market": "KOSPI", "symbol": symbol,
         "short_volume": 125, "short_trading_value": 1250000}
        for symbol in ("005930", "000660")
    ])


def test_contract_and_plan_are_fixed_provider_specific_and_retry_zero() -> None:
    assert TOSS_EQUITY_SHORT_WATCHLIST_DAILY.status == "reviewed_offline_fixed_watchlist_only"
    assert TOSS_EQUITY_SHORT_WATCHLIST_DAILY.name == "toss_equity_short_watchlist_daily"
    assert TOSS_EQUITY_SHORT_WATCHLIST_DAILY.source.startswith("tossinvest_open_api")
    assert "market" in TOSS_EQUITY_SHORT_WATCHLIST_DAILY.column_names
    plan = request_plan("2026-08-19")
    assert [(item.symbol, item.market) for item in plan] == [
        ("005930", "KOSPI"), ("000660", "KOSPI")
    ]
    assert all(item.params == {"count": 1, "until": "2026-08-19"} for item in plan)
    assert CALL_BUDGET.oauth_calls_max == 1
    assert CALL_BUDGET.market_calls_max == 2
    assert CALL_BUDGET.calls_per_symbol == 1
    assert CALL_BUDGET.retries == 0


def test_both_symbols_stage_as_one_duplicate_free_exact_date_scope() -> None:
    staged = _staged()
    assert list(staged.columns) == list(TOSS_EQUITY_SHORT_WATCHLIST_DAILY.column_names)
    assert len(staged) == 2
    assert not staged.duplicated(["date", "market", "symbol"]).any()
    assert staged["source_scope"].eq(TOSS_SHORT_SOURCE_SCOPE).all()
    assert staged["watchlist_version"].eq(TOSS_SHORT_WATCHLIST_VERSION).all()
    assert staged["short_selling_volume"].eq(100).all()
    assert staged["short_selling_amount"].eq(1000000).all()


@pytest.mark.parametrize(
    "frames, message",
    [
        ({"005930": _source("005930")}, "exactly the fixed symbols"),
        (
            {"005930": _source("005930"), "000660": _source("000660", "2026-08-18")},
            "source date differs",
        ),
        (
            {"005930": _source("005930"), "000660": _source("005930")},
            "symbol differs",
        ),
    ],
)
def test_partial_wrong_date_or_wrong_symbol_fails_closed(frames, message) -> None:
    with pytest.raises(ValueError, match=message):
        stage_exact_watchlist(frames, target_date="2026-08-19")


def test_same_date_replay_is_api_zero_only_after_checkpoint_and_retained_validation() -> None:
    checkpoint = {
        "dataset": TOSS_EQUITY_SHORT_WATCHLIST_DAILY.name,
        "watchlist_version": TOSS_SHORT_WATCHLIST_VERSION,
        "status": "SUCCEEDED",
        "completed_date": "2026-08-19",
    }
    assert pre_network_action(
        checkpoint, target_date="2026-08-19", retained=_staged()
    ) == "NOOP_ALREADY_SUCCEEDED"
    with pytest.raises(ValueError, match="requires retained"):
        pre_network_action(checkpoint, target_date="2026-08-19")


def test_incomplete_same_date_transaction_requires_recovery_before_network() -> None:
    checkpoint = {
        "dataset": TOSS_EQUITY_SHORT_WATCHLIST_DAILY.name,
        "watchlist_version": TOSS_SHORT_WATCHLIST_VERSION,
        "status": "PROMOTING",
        "completed_date": "2026-08-19",
    }
    assert pre_network_action(
        checkpoint, target_date="2026-08-19"
    ) == "RECOVERY_REQUIRED_PRE_NETWORK"


def test_overlap_records_units_and_non_equivalent_post_nxt_scope() -> None:
    result = reconcile_official_overlap(_staged(), _official(), target_date="2026-08-19")
    assert len(result) == 2
    assert result["volume_unit"].eq("shares").all()
    assert result["amount_unit"].eq("KRW").all()
    assert result["volume_difference"].eq(-25).all()
    assert result["amount_difference"].eq(-250000).all()
    assert not result["scope_comparable"].any()
    assert result["comparison_reason"].eq(
        "NON_EQUIVALENT_KRX_ONLY_VS_KRX_NXT_COMBINED"
    ).all()


def test_overlap_before_nxt_regime_is_labelled_same_krx_only_scope() -> None:
    result = reconcile_official_overlap(
        _staged("2025-03-03"), _official("2025-03-03"), target_date="2025-03-03"
    )
    assert result["scope_comparable"].all()
    assert result["comparison_reason"].eq("SAME_KRX_ONLY_SCOPE").all()


def test_overlap_rejects_missing_official_member_instead_of_aggregating() -> None:
    with pytest.raises(ValueError, match="every fixed member once"):
        reconcile_official_overlap(
            _staged(), _official().iloc[:1], target_date="2026-08-19"
        )


class FakeLiveClient:
    def __init__(self, dates: dict[str, str] | None = None):
        self.dates = dates or {"005930": "2026-08-19", "000660": "2026-08-19"}
        self.token_request_count = 0
        self.market_request_count = 0
        self.requests: list[tuple[str, dict]] = []

    def get_market_data(self, path, params=None):
        if self.token_request_count == 0:
            self.token_request_count = 1
        self.market_request_count += 1
        self.requests.append((path, dict(params or {})))
        symbol = path.split("/")[4]
        day = self.dates[symbol]
        payload = {
            "result": {
                "records": [{
                    "date": day,
                    "updatedAt": f"{day}T18:14:05+09:00",
                    "shortSellingVolume": "100",
                    "shortSellingAmount": "1000000",
                    "shortSellingVolumeRate": "0.01",
                    "shortSellingAmountRate": "0.02",
                }],
                "nextUntil": "2026-08-18",
            }
        }
        return TossInvestAPIResponse(
            200, payload, TossInvestRateLimit("STOCK_TRADING_TREND", 10, 9, 1)
        )


def _write_official(root: Path, day: str = "2026-08-19") -> Path:
    path = (
        root / "data/normalized/kr_short_selling_trading_daily"
        / "market=KOSPI/year=2026/data.parquet"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    _official(day).to_parquet(path, index=False)
    return path


def test_live_boundary_promotes_two_symbols_then_replays_pre_network_api_zero(tmp_path) -> None:
    official_path = _write_official(tmp_path)
    official_before = official_path.read_bytes()
    client = FakeLiveClient()
    result = refresh_toss_short_watchlist_daily(
        tmp_path, intended_date="2026-08-19", client=client
    )
    assert result == {
        "status": "SUCCEEDED",
        "intended_date": "2026-08-19",
        "token_calls": 1,
        "market_calls": 2,
        "promoted_rows": 2,
        "retained_rows": 2,
        "overlap_rows": 2,
        "overlap_scope": "NON_EQUIVALENT_KRX_ONLY_VS_KRX_NXT_COMBINED",
    }
    assert client.requests == [
        ("/api/v1/stocks/005930/short-selling", {"count": 1, "until": "2026-08-19"}),
        ("/api/v1/stocks/000660/short-selling", {"count": 1, "until": "2026-08-19"}),
    ]
    assert len(list((tmp_path / "data/landing/tossinvest/getStockShortSelling").rglob("*.json"))) == 2
    live = read_dataset(
        tmp_path / "data/normalized/toss_equity_short_watchlist_daily",
        TOSS_EQUITY_SHORT_WATCHLIST_DAILY,
        validate_watchlist_dataset,
    )
    assert len(live) == 2
    checkpoint = json.loads(
        (tmp_path / "data/state/toss_equity_short_watchlist_daily.json").read_text()
    )
    assert checkpoint["status"] == "SUCCEEDED"
    assert checkpoint["completed_symbols"] == ["000660", "005930"]
    assert len(checkpoint["overlap"]) == 2
    assert official_path.read_bytes() == official_before
    replay = refresh_toss_short_watchlist_daily(
        tmp_path, intended_date="2026-08-19", client=None
    )
    assert replay["status"] == "NOOP_ALREADY_SUCCEEDED"
    assert replay["token_calls"] == replay["market_calls"] == 0


def test_wrong_second_symbol_date_retains_landing_but_promotes_nothing(tmp_path) -> None:
    _write_official(tmp_path)
    client = FakeLiveClient({"005930": "2026-08-19", "000660": "2026-08-18"})
    with pytest.raises(ValueError, match="source date differs"):
        refresh_toss_short_watchlist_daily(
            tmp_path, intended_date="2026-08-19", client=client
        )
    assert client.market_request_count == 2
    assert len(list((tmp_path / "data/landing/tossinvest/getStockShortSelling").rglob("*.json"))) == 2
    assert not (tmp_path / "data/normalized/toss_equity_short_watchlist_daily").exists()
    assert not (tmp_path / "data/state/toss_equity_short_watchlist_daily.json").exists()
    journal = json.loads(
        (tmp_path / "data/state/toss_equity_short_watchlist_daily_journal.json").read_text()
    )
    assert journal["status"] == "FAILED_ROLLED_BACK"
    assert journal["market_calls"] == 2


def test_promotion_exception_restores_exact_prior_root_and_checkpoint(tmp_path) -> None:
    _write_official(tmp_path)
    live_root = tmp_path / "data/normalized/toss_equity_short_watchlist_daily"
    prior = _staged("2026-08-18")
    write_dataset_atomic(
        prior, live_root, TOSS_EQUITY_SHORT_WATCHLIST_DAILY,
        validate_watchlist_dataset,
    )
    prior_bytes = next(live_root.rglob("data.parquet")).read_bytes()
    with pytest.raises(RuntimeError, match="promotion fixture failure"):
        refresh_toss_short_watchlist_daily(
            tmp_path,
            intended_date="2026-08-19",
            client=FakeLiveClient(),
            promotion_hook=lambda phase: (_ for _ in ()).throw(
                RuntimeError("promotion fixture failure")
            ),
        )
    restored_path = next(live_root.rglob("data.parquet"))
    assert restored_path.read_bytes() == prior_bytes
    restored = read_dataset(
        live_root, TOSS_EQUITY_SHORT_WATCHLIST_DAILY, validate_watchlist_dataset
    )
    assert restored["date"].astype(str).eq("2026-08-18").all()
    assert not (tmp_path / "data/state/toss_equity_short_watchlist_daily.json").exists()
