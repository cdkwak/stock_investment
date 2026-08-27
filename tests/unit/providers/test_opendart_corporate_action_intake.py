from __future__ import annotations

import json

import pytest

from stock_data.providers.opendart_corporate_action_intake import (
    FilingCursor,
    OpenDartIntakeError,
    conservative_availability,
    identity_observation,
    merge_pages_after_cursor,
    parse_list_page,
)


def _row(receipt: str, day: str, name: str = "무상증자결정") -> dict[str, object]:
    return {
        "corp_cls": "K", "corp_name": "Fixture", "corp_code": "01160363",
        "stock_code": "247540", "report_nm": name, "rcept_no": receipt,
        "flr_nm": "Fixture", "rcept_dt": day, "rm": "",
    }


def _page(number: int, total_page: int, rows: list[dict[str, object]], total: int):
    return json.dumps({
        "status": "000", "message": "ok", "page_no": number,
        "page_count": 1, "total_count": total, "total_page": total_page,
        "list": rows,
    }, ensure_ascii=False).encode()


def test_contiguous_pages_dedupe_and_advance_date_receipt_cursor():
    first = parse_list_page(
        _page(1, 2, [_row("20220614000068", "20220614")], 2),
        captured_at_utc="2026-08-20T13:00:00+00:00",
    )
    second = parse_list_page(
        _page(2, 2, [_row("20220615000001", "20220615", "합병결정")], 2),
        captured_at_utc="2026-08-20T13:00:01+00:00",
    )
    rows, cursor = merge_pages_after_cursor(
        (first, second), FilingCursor("20220614", "20220614000068")
    )
    assert [row["rcept_no"] for row in rows] == ["20220615000001"]
    assert rows[0]["event_family"] == "merger"
    assert cursor == FilingCursor("20220615", "20220615000001")


def test_duplicate_receipt_conflict_and_incomplete_pagination_fail_closed():
    first = parse_list_page(
        _page(1, 2, [_row("20220614000068", "20220614")], 1),
        captured_at_utc="2026-08-20T13:00:00+00:00",
    )
    conflicting = parse_list_page(
        _page(2, 2, [_row("20220614000068", "20220614", "합병결정")], 1),
        captured_at_utc="2026-08-20T13:00:01+00:00",
    )
    with pytest.raises(OpenDartIntakeError, match="conflicting"):
        merge_pages_after_cursor((first, conflicting), None)
    with pytest.raises(OpenDartIntakeError, match="complete and contiguous"):
        merge_pages_after_cursor((conflicting,), None)


def test_revision_parent_is_never_inferred_and_knowledge_time_is_explicit():
    page = parse_list_page(
        _page(1, 1, [{**_row("20220614000068", "20220614"), "rm": "정"}], 1),
        captured_at_utc="2026-08-20T13:00:00+00:00",
    )
    row = page.rows[0]
    assert row["original_receipt_no"] is None
    assert row["revises_receipt_no"] is None
    assert row["revision_parent_status"] == "UNVERIFIED_NO_EXPLICIT_PARENT"
    availability = conservative_availability(row)
    assert availability["observation_time_utc"] == "2026-08-20T13:00:00+00:00"
    assert availability["usable_from"] == "2022-06-15"
    assert availability["available_at_utc"] == "2026-08-20T13:00:00+00:00"


def test_identity_is_current_observation_with_null_effective_edges():
    page = parse_list_page(
        _page(1, 1, [_row("20220614000068", "20220614")], 1),
        captured_at_utc="2026-08-20T13:00:00+00:00",
    )
    identity = identity_observation(page.rows[0])
    assert identity["stock_code"] == "247540"
    assert identity["market"] is None
    assert identity["security_class"] is None
    assert identity["valid_from"] is None
    assert identity["identity_status"] == "CURRENT_AT_CAPTURE_EFFECTIVE_DATES_UNVERIFIED"


def test_valid_empty_page_is_zero_row_terminal_observation():
    page = parse_list_page(
        b'{"status":"013","message":"no data"}',
        captured_at_utc="2026-08-20T13:00:00+00:00",
    )
    assert page.total_count == 0 and page.rows == ()
