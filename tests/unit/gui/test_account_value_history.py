from __future__ import annotations

import json
from pathlib import Path

import pytest

from stock_data.gui.account_value_history import (
    AccountValueHistoryPoint,
    AccountValueHistorySeries,
    kb_account_value_observation,
    load_account_value_history,
    persist_account_value_observation,
    toss_account_value_observation,
    validate_account_value_observation,
)
from stock_data.gui.account_snapshot_service import (
    AccountPortfolioEntryView,
    AccountPortfolioView,
    AccountSnapshotState,
    AccountSnapshotView,
    build_account_portfolio_presentation,
)


def _toss(observed_at: str, *, market: str, cash: str | None) -> dict:
    return {
        "provider": "tossinvest_open_api",
        "collected_at": observed_at,
        "summaries": [{
            "currency": "KRW",
            "market_value": market,
            "purchase_amount": "900",
            "profit_loss": "100",
        }],
        "buying_power": (
            None if cash is None else [{
                "currency": "KRW", "cash_buying_power": cash,
            }]
        ),
    }


def _write(root: Path, source_id: str, name: str, payload: dict) -> None:
    path = root / source_id / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_toss_history_separates_observable_sum_from_legacy_securities_only() -> None:
    current = toss_account_value_observation(_toss(
        "2026-08-27T07:00:00+09:00", market="1000", cash="250",
    ))
    legacy = toss_account_value_observation(_toss(
        "2026-08-26T07:00:00+09:00", market="900", cash=None,
    ))

    assert current["currencies"][0]["metric"] == "OBSERVABLE_COMPONENT_SUM"
    assert float(current["currencies"][0]["value"]) == 1250.0
    assert current["currencies"][0]["total_assets"] is None
    assert legacy["currencies"][0]["metric"] == "SECURITIES_VALUE"
    assert float(legacy["currencies"][0]["value"]) == 900.0


def test_kb_history_uses_only_exact_provider_total_assets() -> None:
    payload = kb_account_value_observation({
        "provider": "kbsec_open_api",
        "collected_at": "2026-08-27T07:01:00+09:00",
        "total_assets": "1500",
        "securities_value": "1200",
        "purchase_amount": "1000",
        "unrealized_pnl": "200",
    })

    row = payload["currencies"][0]
    assert row["metric"] == "TOTAL_ASSETS"
    assert row["value"] == row["total_assets"] == "1500"
    assert row["cash_buying_power"] is None


def test_history_contract_binds_source_currency_and_metric() -> None:
    invalid = kb_account_value_observation({
        "provider": "kbsec_open_api",
        "collected_at": "2026-08-27T07:01:00+09:00",
        "total_assets": "1500",
        "securities_value": "1200",
        "purchase_amount": "1000",
        "unrealized_pnl": "200",
    })
    invalid["currencies"][0].update({
        "currency": "USD",
        "metric": "SECURITIES_VALUE",
        "value": "1200",
        "total_assets": None,
    })

    with pytest.raises(ValueError, match="KB account history identity"):
        validate_account_value_observation(invalid)


def test_toss_observation_rejects_mixed_schema_generation_metrics() -> None:
    observation = toss_account_value_observation({
        "provider": "tossinvest_open_api",
        "collected_at": "2026-08-27T07:00:00+09:00",
        "summaries": [
            {"currency": "KRW", "market_value": "1000", "purchase_amount": "900", "profit_loss": "100"},
            {"currency": "USD", "market_value": "100", "purchase_amount": "90", "profit_loss": "10"},
        ],
        "buying_power": [
            {"currency": "KRW", "cash_buying_power": "250"},
            {"currency": "USD", "cash_buying_power": "25"},
        ],
    })
    observation["currencies"][1]["metric"] = "SECURITIES_VALUE"
    observation["currencies"][1]["value"] = "100"
    observation["currencies"][1]["cash_buying_power"] = None

    with pytest.raises(ValueError, match="schema generation"):
        validate_account_value_observation(observation)


def test_history_loader_orders_points_and_fails_closed_on_duplicate(tmp_path: Path) -> None:
    first = toss_account_value_observation(_toss(
        "2026-08-26T07:00:00+09:00", market="900", cash="100",
    ))
    second = toss_account_value_observation(_toss(
        "2026-08-27T07:00:00+09:00", market="1000", cash="250",
    ))
    _write(tmp_path, "toss_self", "b.json", second)
    _write(tmp_path, "toss_self", "a.json", first)

    series = load_account_value_history(tmp_path)

    assert len(series) == 1
    assert [point.value for point in series[0].points] == [1000.0, 1250.0]
    _write(tmp_path, "toss_self", "duplicate.json", first)
    with pytest.raises(ValueError, match="duplicated"):
        load_account_value_history(tmp_path)


def test_history_loader_treats_different_offsets_for_same_instant_as_duplicate(
    tmp_path: Path,
) -> None:
    first = toss_account_value_observation(_toss(
        "2026-08-27T07:00:00+09:00", market="1000", cash="250",
    ))
    same_instant = json.loads(json.dumps(first))
    same_instant["observed_at"] = "2026-08-26T22:00:00+00:00"
    _write(tmp_path, "toss_self", "first.json", first)
    _write(tmp_path, "toss_self", "same-instant.json", same_instant)

    with pytest.raises(ValueError, match="duplicated"):
        load_account_value_history(tmp_path)


def test_bootstrap_persistence_is_atomic_and_idempotent(tmp_path: Path) -> None:
    observation = toss_account_value_observation(_toss(
        "2026-08-27T07:00:00+09:00", market="1000", cash="250",
    ))

    assert persist_account_value_observation(tmp_path, observation) == "CREATED"
    assert persist_account_value_observation(tmp_path, observation) == "NOOP"
    files = list((
        tmp_path / "data/local/account_value_history/toss_self"
    ).glob("*.json"))
    assert len(files) == 1
    assert not any((tmp_path / "data/staging/account_value_history").glob("*"))


def test_presentation_keeps_account_scale_history_source_and_currency_scoped() -> None:
    portfolio = AccountPortfolioView(
        entries=(AccountPortfolioEntryView(
            "toss_self", "Toss", AccountSnapshotView(
                state=AccountSnapshotState.TOSS_READ_ONLY,
                as_of="2026-08-27T07:00:00+09:00",
                freshness="AS_RETRIEVED",
            ),
        ),),
        user_fund_totals=(),
        value_histories=(AccountValueHistorySeries(
            source_id="toss_self",
            currency="KRW",
            metric="OBSERVABLE_COMPONENT_SUM",
            points=(
                AccountValueHistoryPoint(
                    "2026-08-26T07:00:00+09:00", 1000.0, 900.0, 100.0,
                ),
                AccountValueHistoryPoint(
                    "2026-08-27T07:00:00+09:00", 1250.0, 1000.0, 250.0,
                ),
            ),
        ),),
    )

    view = build_account_portfolio_presentation(portfolio)

    assert len(view.histories) == 1
    assert view.histories[0].metric == "OBSERVABLE_COMPONENT_SUM"
    assert [point.total_assets for point in view.histories[0].points] == [1000.0, 1250.0]
    assert "PERFORMANCE" in view.histories[0].interpretation


def test_presentation_suppresses_source_history_after_current_snapshot() -> None:
    portfolio = AccountPortfolioView(
        entries=(AccountPortfolioEntryView(
            "toss_self", "Toss", AccountSnapshotView(
                state=AccountSnapshotState.TOSS_READ_ONLY,
                as_of="2026-08-27T07:00:00+09:00",
                freshness="AS_RETRIEVED",
            ),
        ),),
        user_fund_totals=(),
        value_histories=(AccountValueHistorySeries(
            source_id="toss_self",
            currency="KRW",
            metric="OBSERVABLE_COMPONENT_SUM",
            points=(
                AccountValueHistoryPoint(
                    "2026-08-26T22:00:00+00:00", 1000.0, 900.0, 100.0,
                ),
                AccountValueHistoryPoint(
                    "2099-01-01T00:00:00+00:00", 1250.0, 1000.0, 250.0,
                ),
            ),
        ),),
    )

    view = build_account_portfolio_presentation(portfolio)

    assert view.histories == ()
    assert view.history_reason == "ACCOUNT_VALUE_HISTORY_AFTER_CURRENT_SNAPSHOT"
