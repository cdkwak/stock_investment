from __future__ import annotations

import json

import pandas as pd

from stock_data.pipelines.stock_lending_backfill import (
    STOCK_LENDING_SPECS,
    StockLendingBackfillLocked,
    collect_stock_lending_history,
    stock_lending_run_lock,
)
import pytest


class Response:
    status_code = 200
    headers = {"X-RateLimit-Limit": "10000", "X-RateLimit-Remaining": "9998"}

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, endpoint, **kwargs):
        self.calls.append((endpoint, kwargs))
        return self.responses.pop(0)


def payload(page, total, rows):
    return {
        "response": {
            "header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE."},
            "body": {
                "numOfRows": 2,
                "pageNo": page,
                "totalCount": total,
                "items": {"item": rows},
            },
        }
    }


def row(date, symbol, executed="0"):
    return {
        "basDt": date,
        "mrktClsfNm": "코스피",
        "stckItmsCd": symbol,
        "stckItmsNm": f"종목{symbol}",
        "cclStckCnt": executed,
        "rdptStckCnt": "0",
        "balnStckCnt": "10",
        "balnStckAmt": "1000",
    }


def test_range_pagination_is_landing_first_atomic_and_resumable(tmp_path):
    responses = [
        Response(payload(1, 3, [row("20210401", "000001"), row("20210402", "000002", "4")])),
        Response(payload(2, 3, [row("20210405", "000003")])),
    ]
    session = Session(responses)
    result = collect_stock_lending_history(
        project_root=tmp_path,
        spec=STOCK_LENDING_SPECS["detail"],
        page_size=2,
        max_calls=2,
        min_interval_seconds=0,
        service_key="secret-value",
        session=session,
        sleep_fn=lambda _: None,
    )

    assert result.status == "COMPLETE"
    assert result.api_calls == 2
    assert result.source_rows == result.normalized_rows == 3
    assert result.minimum_date == "2021-04-01"
    assert result.maximum_date == "2021-04-05"
    assert result.rate_limit_remaining == "9998"
    assert [call[1]["params"]["pageNo"] for call in session.calls] == [1, 2]
    assert all(call[1]["params"]["beginBasDt"] == "20210401" for call in session.calls)
    assert all("endBasDt" not in call[1]["params"] for call in session.calls)

    landing = sorted(
        (tmp_path / "data/landing/data_go_kr/kr_stock_lending_daily/historical").rglob("*.json")
    )
    assert len(landing) == 2
    assert "secret-value" not in "".join(path.read_text(encoding="utf-8") for path in landing)
    stored = pd.read_parquet(
        tmp_path / "data/normalized/kr_stock_lending_daily/year=2021/data.parquet"
    )
    assert list(stored["executed_shares"]) == [0, 4, 0]
    assert not list((tmp_path / "data/normalized").rglob("*.tmp"))
    state = json.loads(
        (tmp_path / "data/state/kr_stock_lending_daily_historical.json").read_text("utf-8")
    )
    assert "range:20210401:open" in state["completed_partitions"]
    assert state["staged_partitions"] == []

    resumed = collect_stock_lending_history(
        project_root=tmp_path,
        spec=STOCK_LENDING_SPECS["detail"],
        page_size=2,
        max_calls=1,
        min_interval_seconds=0,
        service_key="secret-value",
        session=Session([]),
        sleep_fn=lambda _: None,
    )
    assert resumed.status == "COMPLETE" and resumed.api_calls == 0
    assert resumed.source_rows == 3 and resumed.normalized_rows == 3


def test_existing_valid_row_is_preserved_when_history_is_promoted(tmp_path):
    first = Session([Response(payload(1, 1, [row("20210401", "000001")]))])
    collect_stock_lending_history(
        project_root=tmp_path,
        spec=STOCK_LENDING_SPECS["detail"],
        page_size=2,
        max_calls=1,
        min_interval_seconds=0,
        service_key="key",
        session=first,
        sleep_fn=lambda _: None,
    )
    path = tmp_path / "data/normalized/kr_stock_lending_daily/year=2021/data.parquet"
    before = pd.read_parquet(path)

    # A second, bounded range has a distinct run marker and must merge, not replace.
    second = Session([Response(payload(1, 1, [row("20210402", "000002")]))])
    collect_stock_lending_history(
        project_root=tmp_path,
        spec=STOCK_LENDING_SPECS["detail"],
        start_date="20210402",
        end_date="20210403",
        page_size=2,
        max_calls=1,
        min_interval_seconds=0,
        service_key="key",
        session=second,
        sleep_fn=lambda _: None,
    )
    after = pd.read_parquet(path)
    assert len(before) == 1
    assert set(after["symbol"]) == {"000001", "000002"}


def test_run_lock_rejects_an_overlapping_resume_and_cleans_up(tmp_path):
    lock_path = tmp_path / "data/state/fsc_stock_lending_backfill.lock"
    with stock_lending_run_lock(tmp_path):
        assert lock_path.exists()
        with pytest.raises(StockLendingBackfillLocked):
            with stock_lending_run_lock(tmp_path):
                pass
    assert not lock_path.exists()


def test_valid_empty_finalizes_range_and_page_markers(tmp_path):
    session = Session([Response(payload(1, 0, []))])
    result = collect_stock_lending_history(
        project_root=tmp_path,
        spec=STOCK_LENDING_SPECS["detail"],
        page_size=2,
        max_calls=1,
        min_interval_seconds=0,
        service_key="key",
        session=session,
        sleep_fn=lambda _: None,
    )

    assert result.status == "VALID_EMPTY"
    assert result.source_rows == result.normalized_rows == 0
    assert result.api_calls == result.landing_pages == 1
    state = json.loads(
        (tmp_path / "data/state/kr_stock_lending_daily_historical.json").read_text(
            encoding="utf-8"
        )
    )
    assert state["valid_empty_partitions"] == [
        "range:20210401:open",
        "range:20210401:open:page:00001",
    ]
    assert state["staged_partitions"] == []
    assert state["failed_partitions"] == {}

    resumed_session = Session([])
    resumed = collect_stock_lending_history(
        project_root=tmp_path,
        spec=STOCK_LENDING_SPECS["detail"],
        page_size=2,
        max_calls=1,
        min_interval_seconds=0,
        service_key="key",
        session=resumed_session,
        sleep_fn=lambda _: None,
    )
    assert resumed.status == "VALID_EMPTY"
    assert resumed.source_rows == resumed.normalized_rows == 0
    assert resumed_session.calls == []


def test_completed_resume_reports_unknown_source_rows_for_truncated_landing(tmp_path):
    collect_stock_lending_history(
        project_root=tmp_path,
        spec=STOCK_LENDING_SPECS["detail"],
        page_size=2,
        max_calls=2,
        min_interval_seconds=0,
        service_key="key",
        session=Session([
            Response(payload(1, 3, [row("20210401", "000001"), row("20210401", "000002")])),
            Response(payload(2, 3, [row("20210402", "000003")])),
        ]),
        sleep_fn=lambda _: None,
    )
    landing_pages = sorted(
        (tmp_path / "data/landing/data_go_kr/kr_stock_lending_daily/historical")
        .rglob("*.json")
    )
    final_payload = json.loads(landing_pages[-1].read_text(encoding="utf-8"))
    final_payload[0]["response"]["body"]["items"]["item"] = []
    landing_pages[-1].write_text(
        json.dumps(final_payload, ensure_ascii=False), encoding="utf-8"
    )

    resumed_session = Session([])
    resumed = collect_stock_lending_history(
        project_root=tmp_path,
        spec=STOCK_LENDING_SPECS["detail"],
        page_size=2,
        max_calls=1,
        min_interval_seconds=0,
        service_key="key",
        session=resumed_session,
        sleep_fn=lambda _: None,
    )
    assert resumed.status == "COMPLETE"
    assert resumed.source_rows is None
    assert resumed_session.calls == []


def test_zero_total_with_nonempty_items_fails_closed(tmp_path):
    with pytest.raises(
        RuntimeError, match="stock lending landing rows differ from totalCount"
    ):
        collect_stock_lending_history(
            project_root=tmp_path,
            spec=STOCK_LENDING_SPECS["detail"],
            page_size=2,
            max_calls=1,
            min_interval_seconds=0,
            service_key="key",
            session=Session([Response(payload(1, 0, [row("20210401", "000001")]))]),
            sleep_fn=lambda _: None,
        )

    state = json.loads(
        (tmp_path / "data/state/kr_stock_lending_daily_historical.json").read_text(
            encoding="utf-8"
        )
    )
    assert state["valid_empty_partitions"] == []
    assert state["completed_partitions"] == []


def test_completed_resume_rejects_inconsistent_page_size_metadata(tmp_path):
    collect_stock_lending_history(
        project_root=tmp_path,
        spec=STOCK_LENDING_SPECS["detail"],
        page_size=2,
        max_calls=2,
        min_interval_seconds=0,
        service_key="key",
        session=Session([
            Response(payload(1, 3, [row("20210401", "000001"), row("20210401", "000002")])),
            Response(payload(2, 3, [row("20210402", "000003")])),
        ]),
        sleep_fn=lambda _: None,
    )
    landing_pages = sorted(
        (tmp_path / "data/landing/data_go_kr/kr_stock_lending_daily/historical")
        .rglob("*.json")
    )
    final_payload = json.loads(landing_pages[-1].read_text(encoding="utf-8"))
    final_payload[0]["response"]["body"]["numOfRows"] = 9999
    landing_pages[-1].write_text(
        json.dumps(final_payload, ensure_ascii=False), encoding="utf-8"
    )

    resumed_session = Session([])
    resumed = collect_stock_lending_history(
        project_root=tmp_path,
        spec=STOCK_LENDING_SPECS["detail"],
        page_size=2,
        max_calls=1,
        min_interval_seconds=0,
        service_key="key",
        session=resumed_session,
        sleep_fn=lambda _: None,
    )
    assert resumed.status == "COMPLETE"
    assert resumed.source_rows is None
    assert resumed_session.calls == []


def test_completed_resume_reports_range_source_rows_not_dataset_rows(tmp_path):
    collect_stock_lending_history(
        project_root=tmp_path,
        spec=STOCK_LENDING_SPECS["detail"],
        start_date="20210401",
        end_date="20210402",
        page_size=2,
        max_calls=1,
        min_interval_seconds=0,
        service_key="key",
        session=Session([Response(payload(1, 1, [row("20210401", "000001")]))]),
        sleep_fn=lambda _: None,
    )
    collect_stock_lending_history(
        project_root=tmp_path,
        spec=STOCK_LENDING_SPECS["detail"],
        start_date="20210402",
        end_date="20210403",
        page_size=2,
        max_calls=1,
        min_interval_seconds=0,
        service_key="key",
        session=Session([Response(payload(1, 1, [row("20210402", "000002")]))]),
        sleep_fn=lambda _: None,
    )

    resumed_session = Session([])
    resumed = collect_stock_lending_history(
        project_root=tmp_path,
        spec=STOCK_LENDING_SPECS["detail"],
        start_date="20210401",
        end_date="20210402",
        page_size=2,
        max_calls=1,
        min_interval_seconds=0,
        service_key="key",
        session=resumed_session,
        sleep_fn=lambda _: None,
    )
    assert resumed.api_calls == 0
    assert resumed.source_rows == 1
    assert resumed.normalized_rows == 2
    assert resumed_session.calls == []


def test_completed_resume_reports_unknown_source_rows_without_landing_evidence(tmp_path):
    collect_stock_lending_history(
        project_root=tmp_path,
        spec=STOCK_LENDING_SPECS["detail"],
        start_date="20210401",
        end_date="20210402",
        page_size=2,
        max_calls=1,
        min_interval_seconds=0,
        service_key="key",
        session=Session([Response(payload(1, 1, [row("20210401", "000001")]))]),
        sleep_fn=lambda _: None,
    )
    landing = next(
        (tmp_path / "data/landing/data_go_kr/kr_stock_lending_daily/historical")
        .rglob("*.json")
    )
    landing.unlink()

    resumed_session = Session([])
    resumed = collect_stock_lending_history(
        project_root=tmp_path,
        spec=STOCK_LENDING_SPECS["detail"],
        start_date="20210401",
        end_date="20210402",
        page_size=2,
        max_calls=1,
        min_interval_seconds=0,
        service_key="key",
        session=resumed_session,
        sleep_fn=lambda _: None,
    )
    assert resumed.status == "COMPLETE"
    assert resumed.source_rows is None
    assert resumed.normalized_rows == 1
    assert resumed_session.calls == []
