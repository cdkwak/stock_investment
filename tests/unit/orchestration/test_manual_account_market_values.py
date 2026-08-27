from copy import deepcopy
from pathlib import Path

import pytest

from stock_data.gui.manual_account_snapshot import parse_manual_account_snapshot
from stock_data.orchestration import manual_account_market_values as subject
from stock_data.providers.yahoo_account_prices import (
    YahooAccountPriceSymbol,
    yahoo_account_price_unavailable,
)


CLOCK = subject.datetime.fromisoformat("2026-08-26T14:00:05+09:00")


def _snapshot():
    return parse_manual_account_snapshot({
        "schema_version": 1, "source_sheet": "아빠",
        "snapshot_date": "2026-02-03", "currency": "KRW",
        "holdings": [
            {"section": "ISA", "name": "Fixture Alpha", "ticker": "111111",
             "quantity": 2, "average_cost": 100, "purchase_total": 200},
            {"section": "ISA", "name": "Fixture Beta", "ticker": "222222",
             "quantity": 1, "average_cost": 300, "purchase_total": 300},
            {"section": "종합", "name": "Fixture Gamma", "ticker": "333333",
             "quantity": 1, "average_cost": None, "purchase_total": None},
        ],
    })


def _symbols():
    return {
        ("ISA", "111111"): YahooAccountPriceSymbol(
            "ISA", "111111", "111111.KS", "XKRX",
        ),
        ("ISA", "222222"): YahooAccountPriceSymbol(
            "ISA", "222222", "222222.KQ", "XKRX",
        ),
        ("종합", "333333"): YahooAccountPriceSymbol(
            "종합", "333333", "333333.KS", "XKRX",
        ),
    }


def _accepted(symbol: YahooAccountPriceSymbol, price: str):
    return {
        "provider": "YAHOO_CHART_API", "provider_symbol": symbol.provider_symbol,
        "exchange": symbol.exchange, "currency": "KRW", "unit": "KRW_PER_SHARE",
        "price": price, "as_of": "2026-08-26T05:00:00+00:00",
        "captured_at": "2026-08-26T05:00:05+00:00",
        "finality": "AS_RETRIEVED",
    }


def _supplier(calls: list[tuple[YahooAccountPriceSymbol, ...]]):
    symbols = _symbols()

    def supply(requested):
        calls.append(requested)
        values = {
            ("ISA", "111111"): _accepted(symbols[("ISA", "111111")], "150"),
            ("ISA", "222222"): _accepted(symbols[("ISA", "222222")], "100"),
            ("종합", "333333"): yahoo_account_price_unavailable(
                symbols[("종합", "333333")], "PROVIDER_EMPTY",
            ),
        }
        return {
            (item.section, item.ticker): values[(item.section, item.ticker)]
            for item in requested
        }

    return supply


def test_refresh_uses_one_injected_call_and_exact_section_currency_math(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot()
    acquisition_before = deepcopy(snapshot)
    calls = []
    path = tmp_path / "latest.json"

    result = subject.refresh_manual_account_market_values(
        snapshot, symbol_map=_symbols(), supplier=_supplier(calls),
        cache_path=path, now=CLOCK,
    )

    assert result.status == "UPDATED"
    assert len(calls) == 1 and tuple(
        (item.section, item.ticker, item.provider_symbol) for item in calls[0]
    ) == (
        ("ISA", "111111", "111111.KS"),
        ("ISA", "222222", "222222.KQ"),
        ("종합", "333333", "333333.KS"),
    )
    assert snapshot == acquisition_before
    rows = result.cache.rows
    assert [(row.status, row.reason) for row in rows] == [
        ("AVAILABLE", None), ("AVAILABLE", None),
        ("UNAVAILABLE", "PROVIDER_EMPTY"),
    ]
    assert [str(rows[index].market_value) for index in (0, 1)] == ["300.0", "100.0"]
    assert [str(rows[index].weight_pct) for index in (0, 1)] == ["75.00", "25.00"]
    assert str(rows[0].unrealized_pnl) == "100.0"
    assert str(rows[0].return_pct) == "50.0"
    assert str(rows[1].unrealized_pnl) == "-200.0"
    assert rows[2].price is None and rows[2].market_value is None
    assert result.cache.section_summaries[0].currency == "KRW"
    assert str(result.cache.section_summaries[0].market_value) == "400.0"
    assert not result.cache.section_summaries[1].complete
    assert not tuple(path.parent.glob(".*.tmp"))


def test_missing_explicit_map_is_unavailable_and_never_guessed(tmp_path: Path) -> None:
    symbols = _symbols()
    symbols.pop(("종합", "333333"))
    calls = []

    result = subject.refresh_manual_account_market_values(
        _snapshot(), symbol_map=symbols, supplier=_supplier(calls),
        cache_path=tmp_path / "latest.json", now=CLOCK,
    )

    assert result.status == "UPDATED"
    assert len(calls) == 1 and len(calls[0]) == 2
    assert result.cache.rows[2].reason == "EXPLICIT_SYMBOL_MAP_MISSING"
    assert result.cache.rows[2].provider_symbol is None


@pytest.mark.parametrize("failure", ["raised", "wrong_symbol", "extra_identity"])
def test_rejected_refresh_preserves_prior_cache_bytes(
    tmp_path: Path, failure: str,
) -> None:
    path = tmp_path / "latest.json"
    accepted = subject.refresh_manual_account_market_values(
        _snapshot(), symbol_map=_symbols(), supplier=_supplier([]),
        cache_path=path, now=CLOCK,
    )
    assert accepted.status == "UPDATED"
    before = path.read_bytes()

    def rejected(requested):
        if failure == "raised":
            raise RuntimeError("secret-bearing provider failure must not escape")
        values = _supplier([])(requested)
        if failure == "wrong_symbol":
            values[("ISA", "111111")]["provider_symbol"] = "999999.KS"
        else:
            values[("ISA", "999999")] = values[("ISA", "111111")]
        return values

    result = subject.refresh_manual_account_market_values(
        _snapshot(), symbol_map=_symbols(), supplier=rejected,
        cache_path=path, now=CLOCK,
    )

    assert result.status == "REJECTED_PRIOR_PRESERVED"
    assert result.cache == accepted.cache
    assert path.read_bytes() == before
    assert "secret-bearing" not in str(result)


def test_symbol_map_outside_basis_rejects_before_supplier(tmp_path: Path) -> None:
    mapping = _symbols()
    mapping[("ISA", "999999")] = YahooAccountPriceSymbol(
        "ISA", "999999", "999999.KS", "XKRX",
    )
    invoked = False

    def supplier(_requested):
        nonlocal invoked
        invoked = True
        return {}

    result = subject.refresh_manual_account_market_values(
        _snapshot(), symbol_map=mapping, supplier=supplier,
        cache_path=tmp_path / "latest.json", now=CLOCK,
    )
    assert result.status == "REJECTED_NO_PRIOR"
    assert not invoked and not (tmp_path / "latest.json").exists()


def test_same_ticker_in_two_sections_may_share_one_exact_provider_identity(
    tmp_path: Path,
) -> None:
    snapshot = parse_manual_account_snapshot({
        "schema_version": 1, "source_sheet": "아빠",
        "snapshot_date": "2026-02-03", "currency": "KRW",
        "holdings": [
            {"section": "ISA", "name": "Fixture Alpha", "ticker": "111111",
             "quantity": 2, "average_cost": 100, "purchase_total": 200},
            {"section": "종합", "name": "Fixture Alpha", "ticker": "111111",
             "quantity": 1, "average_cost": 100, "purchase_total": 100},
        ],
    })
    mapping = {
        (section, "111111"): YahooAccountPriceSymbol(
            section, "111111", "111111.KS", "XKRX",
        )
        for section in ("ISA", "종합")
    }
    calls = []

    def supplier(requested):
        calls.append(requested)
        return {
            (symbol.section, symbol.ticker): _accepted(symbol, "150")
            for symbol in requested
        }

    result = subject.refresh_manual_account_market_values(
        snapshot, symbol_map=mapping, supplier=supplier,
        cache_path=tmp_path / "latest.json", now=CLOCK,
    )

    assert result.status == "UPDATED"
    assert len(calls) == 1 and len(calls[0]) == 2
    assert [row.provider_symbol for row in result.cache.rows] == [
        "111111.KS", "111111.KS",
    ]


def test_atomic_write_failure_preserves_prior_cache_bytes(
    tmp_path: Path, monkeypatch,
) -> None:
    path = tmp_path / "latest.json"
    accepted = subject.refresh_manual_account_market_values(
        _snapshot(), symbol_map=_symbols(), supplier=_supplier([]),
        cache_path=path, now=CLOCK,
    )
    before = path.read_bytes()
    monkeypatch.setattr(
        subject, "_atomic_cache_write",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("write failed")),
    )

    rejected = subject.refresh_manual_account_market_values(
        _snapshot(), symbol_map=_symbols(), supplier=_supplier([]),
        cache_path=path, now=CLOCK,
    )

    assert rejected.status == "REJECTED_PRIOR_PRESERVED"
    assert rejected.cache == accepted.cache
    assert path.read_bytes() == before
