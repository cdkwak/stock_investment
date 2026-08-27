from scripts.manual.backfill.backfill_ls_t8428_raw import (
    CONTINUATION_KEY, CONTINUATION_KEY_SHA256, MAX_PAGES, ROOT, START_CURSOR,
    digest, load_adopted_pages, plan,
)
import hashlib


def test_plan_is_exact_bounded_raw_only():
    value = plan()
    assert value["tr_code"] == "t8428" and value["max_pages"] == MAX_PAGES == 12
    assert value["start_cursor"] == START_CURSOR == "20160613"
    assert value["oauth_cap"] == 1 and value["retry_count"] == 0
    assert value["normalized_writes"] is False and len(digest(value)) == 64


def test_retained_continuation_key_binding():
    assert hashlib.sha256(CONTINUATION_KEY.encode()).hexdigest() == CONTINUATION_KEY_SHA256


def test_adopted_pages_reconcile():
    pages = load_adopted_pages(ROOT)
    assert len(pages) == 5 and all(len(page) == 500 for page in pages)
