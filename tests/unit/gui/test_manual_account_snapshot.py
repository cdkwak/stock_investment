from copy import deepcopy
from dataclasses import replace
import math

import pytest

from stock_data.gui.manual_account_snapshot import (
    parse_manual_account_snapshot,
    validate_manual_account_snapshot,
)


def _payload():
    return {
        "schema_version": 1,
        "source_sheet": "아빠",
        "snapshot_date": "2026-02-03",
        "currency": "KRW",
        "holdings": [
            {
                "section": "ISA", "name": "Fixture Alpha", "ticker": "111111",
                "quantity": 3, "average_cost": 100.1, "purchase_total": 300.3,
            },
            {
                "section": "종합", "name": "Fixture Beta", "ticker": "222222",
                "quantity": 2, "average_cost": 0, "purchase_total": 0,
            },
            {
                "section": "종합", "name": "Fixture Gamma", "ticker": "333333",
                "quantity": 1, "average_cost": None, "purchase_total": None,
            },
        ],
    }


def test_manual_snapshot_preserves_exact_authorized_basis_values():
    snapshot = parse_manual_account_snapshot(_payload())

    assert snapshot.source_sheet == "아빠"
    assert snapshot.snapshot_date == "2026-02-03"
    assert snapshot.currency == "KRW"
    assert [(row.section, row.ticker) for row in snapshot.holdings] == [
        ("ISA", "111111"), ("종합", "222222"), ("종합", "333333"),
    ]
    assert snapshot.holdings[0].purchase_total == 300.3
    assert snapshot.holdings[1].average_cost == 0
    assert snapshot.holdings[1].purchase_total == 0
    assert snapshot.holdings[2].average_cost is None
    assert snapshot.holdings[2].purchase_total is None


def test_typed_manual_snapshot_revalidation_preserves_parser_result():
    snapshot = parse_manual_account_snapshot(_payload())

    assert validate_manual_account_snapshot(snapshot) == snapshot


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_sheet", "창대"),
        ("source_sheet", "시트3"),
        ("source_sheet", "시트4"),
        ("currency", "USD"),
        ("snapshot_date", "2026-02-03T00:00:00+09:00"),
        ("snapshot_date", "2026-2-3"),
        ("snapshot_date", "2026-02-04"),
    ],
)
def test_manual_snapshot_rejects_other_tabs_currency_or_nonexact_date(field, value):
    payload = _payload()
    payload[field] = value
    with pytest.raises((TypeError, ValueError)):
        parse_manual_account_snapshot(payload)


@pytest.mark.parametrize("section", ["", "연금", "isa", None])
def test_manual_snapshot_rejects_invalid_section(section):
    payload = _payload()
    payload["holdings"][0]["section"] = section
    with pytest.raises(ValueError, match="section"):
        parse_manual_account_snapshot(payload)


@pytest.mark.parametrize("private_field", ["account_id", "owner_name", "spreadsheet_id"])
def test_manual_snapshot_rejects_extra_private_shaped_fields(private_field):
    payload = _payload()
    payload[private_field] = "must-not-be-accepted"
    with pytest.raises(ValueError, match="keys"):
        parse_manual_account_snapshot(payload)


def test_manual_snapshot_rejects_silent_purchase_total_reconciliation():
    payload = _payload()
    payload["holdings"][0]["purchase_total"] = 300.31
    with pytest.raises(ValueError, match="does not reconcile"):
        parse_manual_account_snapshot(payload)
    assert payload["holdings"][0]["purchase_total"] == 300.31


@pytest.mark.parametrize("schema_version", [True, 1.0, "1", 2])
def test_manual_snapshot_rejects_noninteger_or_unsupported_schema_version(
    schema_version,
):
    payload = _payload()
    payload["schema_version"] = schema_version
    with pytest.raises(ValueError, match="schema version"):
        parse_manual_account_snapshot(payload)


def test_manual_snapshot_rejects_row_extras_and_duplicate_section_ticker():
    extra = _payload()
    extra["holdings"][0]["account_number"] = "private"
    with pytest.raises(ValueError, match="holding keys"):
        parse_manual_account_snapshot(extra)

    duplicate = _payload()
    duplicate["holdings"].append(deepcopy(duplicate["holdings"][0]))
    with pytest.raises(ValueError, match="duplicated"):
        parse_manual_account_snapshot(duplicate)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_sheet", "unauthorized"),
        ("snapshot_date", "2099-01-01"),
        ("snapshot_date", "not-a-date"),
        ("currency", "USD"),
        ("holdings", ()),
    ],
)
def test_typed_manual_snapshot_revalidates_snapshot_boundary(field, value):
    snapshot = parse_manual_account_snapshot(_payload())

    with pytest.raises((TypeError, ValueError)):
        validate_manual_account_snapshot(replace(snapshot, **{field: value}))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("section", "연금"),
        ("ticker", "NOT-A-TICKER"),
        ("quantity", -1.0),
        ("quantity", math.nan),
        ("average_cost", -1.0),
        ("average_cost", math.inf),
        ("purchase_total", -1.0),
        ("purchase_total", math.nan),
        ("purchase_total", 999.0),
    ],
)
def test_typed_manual_snapshot_revalidates_holding_boundary(field, value):
    snapshot = parse_manual_account_snapshot(_payload())
    invalid = replace(snapshot.holdings[0], **{field: value})

    with pytest.raises((TypeError, ValueError)):
        validate_manual_account_snapshot(replace(
            snapshot,
            holdings=(invalid, *snapshot.holdings[1:]),
        ))
