from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from stock_data.gui.account_snapshot_service import (
    build_account_portfolio_presentation,
    manual_account_snapshot_to_portfolio,
)
from stock_data.gui.manual_account_snapshot import parse_manual_account_snapshot
from stock_data.orchestration.manual_account_market_values import (
    refresh_manual_account_market_values,
)
from stock_data.providers.yahoo_account_prices import (
    YahooAccountPriceSymbol,
    yahoo_account_price_unavailable,
)


def _snapshot(quantity: int = 2):
    return parse_manual_account_snapshot({
        "schema_version": 1, "source_sheet": "아빠",
        "snapshot_date": "2026-02-03", "currency": "KRW",
        "holdings": [
            {"section": "ISA", "name": "Fixture Alpha", "ticker": "111111",
             "quantity": quantity, "average_cost": 100,
             "purchase_total": 100 * quantity},
            {"section": "ISA", "name": "Fixture Beta", "ticker": "222222",
             "quantity": 1, "average_cost": 300, "purchase_total": 300},
            {"section": "종합", "name": "Fixture Gamma", "ticker": "333333",
             "quantity": 1, "average_cost": None, "purchase_total": None},
        ],
    })


def _cache(tmp_path: Path):
    symbols = {
        (section, ticker): YahooAccountPriceSymbol(
            section, ticker, provider_symbol, "XKRX",
        )
        for section, ticker, provider_symbol in (
            ("ISA", "111111", "111111.KS"),
            ("ISA", "222222", "222222.KQ"),
            ("종합", "333333", "333333.KS"),
        )
    }

    def supplier(requested):
        result = {}
        for symbol in requested:
            key = (symbol.section, symbol.ticker)
            if symbol.ticker == "333333":
                result[key] = yahoo_account_price_unavailable(symbol, "PROVIDER_EMPTY")
            else:
                result[key] = {
                    "provider": "YAHOO_CHART_API",
                    "provider_symbol": symbol.provider_symbol,
                    "exchange": symbol.exchange, "currency": "KRW",
                    "unit": "KRW_PER_SHARE",
                    "price": "150" if symbol.ticker == "111111" else "100",
                    "as_of": "2026-08-26T05:00:00+00:00",
                    "captured_at": "2026-08-26T05:00:05+00:00",
                    "finality": "AS_RETRIEVED",
                }
        return result

    result = refresh_manual_account_market_values(
        _snapshot(), symbol_map=symbols, supplier=supplier,
        cache_path=tmp_path / "latest.json",
        now=datetime.fromisoformat("2026-08-26T14:00:05+09:00"),
    )
    assert result.status == "UPDATED"
    return result.cache


def test_valued_portfolio_preserves_basis_and_exposes_labelled_metrics(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot()
    portfolio = manual_account_snapshot_to_portfolio(snapshot, _cache(tmp_path))
    presentation = build_account_portfolio_presentation(portfolio)

    isa = portfolio.entries[0].snapshot
    assert isa.provider == "YAHOO_CHART_API"
    assert isa.source_mode == "SANITIZED_CURRENT_PRICE_CACHE"
    assert isa.as_of == "2026-08-26T05:00:00+00:00"
    assert isa.securities_value == 400.0 and isa.total_assets == 400.0
    assert [(row.quantity, row.average_purchase_price, row.purchase_amount) for row in isa.positions] == [
        (2.0, 100.0, 200.0), (1.0, 300.0, 300.0),
    ]
    assert [(row.current_price, row.market_value, row.unrealized_pnl) for row in isa.positions] == [
        (150.0, 300.0, 100.0), (100.0, 100.0, -200.0),
    ]
    assert [holding.weight_pct for holding in presentation.holdings[:2]] == [75.0, 25.0]
    assert [holding.return_pct for holding in presentation.holdings[:2]] == [
        50.0, pytest.approx(-200.0 / 3.0),
    ]
    assert [holding.current_price for holding in presentation.holdings[:2]] == [
        150.0, 100.0,
    ]
    assert all(
        holding.price_provider == "YAHOO_CHART_API"
        and holding.price_unit == "KRW_PER_SHARE"
        and holding.price_as_of == "2026-08-26T05:00:00+00:00"
        and holding.price_finality == "AS_RETRIEVED"
        for holding in presentation.holdings[:2]
    )
    assert presentation.holdings[2].market_value is None
    assert presentation.holdings[2].price_provider is None
    assert portfolio.entries[1].snapshot.reason == "PARTIAL_LABELLED_CURRENT_PRICE_CACHE"
    assert portfolio.entries[1].snapshot.securities_value is None
    assert portfolio.entries[1].snapshot.total_assets is None
    assert portfolio.user_fund_totals == ()


def test_cache_cannot_bind_to_changed_acquisition_basis(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    with pytest.raises(ValueError, match="does not bind"):
        manual_account_snapshot_to_portfolio(_snapshot(quantity=3), cache)


def test_cache_row_order_and_section_summary_are_revalidated(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    with pytest.raises(ValueError, match="identity/order"):
        manual_account_snapshot_to_portfolio(
            _snapshot(), replace(cache, rows=tuple(reversed(cache.rows))),
        )
    summaries = list(cache.section_summaries)
    summaries[0] = replace(summaries[0], total_rows=99)
    with pytest.raises(ValueError):
        manual_account_snapshot_to_portfolio(
            _snapshot(), replace(cache, section_summaries=tuple(summaries)),
        )


@pytest.mark.parametrize("mutation", ["market_value", "pnl", "return", "weight"])
def test_cache_join_recalculates_every_basis_derived_value(
    tmp_path: Path, mutation: str,
) -> None:
    cache = _cache(tmp_path)
    rows = list(cache.rows)
    if mutation == "market_value":
        rows[0] = replace(rows[0], market_value=rows[0].market_value + 1)
        rows[1] = replace(rows[1], market_value=rows[1].market_value - 1)
    elif mutation == "pnl":
        rows[0] = replace(rows[0], unrealized_pnl=rows[0].unrealized_pnl + 1)
    elif mutation == "return":
        rows[0] = replace(rows[0], return_pct=rows[0].return_pct + 1)
    else:
        first_weight, second_weight = rows[0].weight_pct, rows[1].weight_pct
        rows[0] = replace(rows[0], weight_pct=second_weight)
        rows[1] = replace(rows[1], weight_pct=first_weight)

    with pytest.raises(ValueError, match="derived arithmetic"):
        manual_account_snapshot_to_portfolio(
            _snapshot(), replace(cache, rows=tuple(rows)),
        )
