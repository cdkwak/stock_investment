from __future__ import annotations

from datetime import date
import hashlib
import json

import pandas as pd

from stock_data.pipelines.short_selling_backfill import (
    RawResponse,
    run_short_selling_batch,
)


def _body(rows: list[dict[str, str]]) -> bytes:
    return json.dumps({"OutBlock_1": rows}, separators=(",", ":")).encode()


def _balance_body(symbol: str) -> bytes:
    return _body(
        [
            {
                "ISU_CD": symbol,
                "ISU_ABBRV": f"name-{symbol}",
                "BAL_QTY": "100",
                "LIST_SHRS": "1000",
                "BAL_AMT": "200",
                "MKTCAP": "2000",
                "BAL_RTO": "10.0",
            }
        ]
    )


def _investor_body(*days: date) -> bytes:
    return _body(
        [
            {
                "TRD_DD": day.strftime("%Y/%m/%d"),
                "STR_CONST_VAL1": "10",
                "STR_CONST_VAL2": "20",
                "STR_CONST_VAL3": "30",
                "STR_CONST_VAL4": "40",
                "STR_CONST_VAL5": "100",
            }
            for day in days
        ]
    )


class _NoWait:
    def wait(self) -> float:
        return 0.0


class _Client:
    def __init__(self, bodies: list[bytes], ledger, seen: list[str]):
        self._bodies = iter(bodies)
        self._ledger = ledger
        self._seen = seen
        self.raw_count = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def fetch(self, scope):
        body = next(self._bodies)
        self.raw_count += 1
        self._seen.append(scope.scope_id)
        self._ledger.append(
            "HTTP_RESPONSE",
            raw_sequence=self.raw_count,
            method="POST",
            url="https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd",
            status_code=200,
            response_bytes=len(body),
            response_sha256=hashlib.sha256(body).hexdigest(),
            authentication=False,
        )
        return RawResponse(200, body, "application/json", self.raw_count)


def _run(tmp_path, dataset, days, bodies, seen, max_calls):
    return run_short_selling_batch(
        dataset=dataset,
        trading_dates=days,
        max_business_calls=max_calls,
        project_root=tmp_path,
        client_factory=lambda ledger: _Client(bodies, ledger, seen),
        throttle=_NoWait(),
    )


def test_balance_is_landing_first_partitioned_and_exactly_resumable(tmp_path):
    days = (date(2016, 6, 30),)
    seen: list[str] = []
    first = _run(tmp_path, "balance", days, [_balance_body("005930")], seen, 1)
    assert first.completed_now == 1
    assert json.loads(first.checkpoint_path.read_text())["status"] == "BATCH_LIMIT_REACHED"
    assert (
        tmp_path / "data/landing/pykrx/short_selling/balance/20160630_KOSPI.json"
    ).is_file()

    second = _run(tmp_path, "balance", days, [_balance_body("035720")], seen, 1)
    assert second.previously_completed_scopes == 1
    assert json.loads(second.checkpoint_path.read_text())["status"] == "BATCH_COMPLETE"
    assert seen == ["20160630_KOSPI", "20160630_KOSDAQ"]
    for market in ("KOSPI", "KOSDAQ"):
        stored = pd.read_parquet(
            tmp_path
            / "data/normalized/kr_short_selling_balance_daily"
            / f"market={market}"
            / "year=2016/data.parquet"
        )
        assert len(stored) == 1

    resumed = _run(tmp_path, "balance", days, [], seen, 1)
    assert resumed.requested_business_calls == 0
    assert resumed.previously_completed_scopes == 2


def test_investor_requires_all_dates_and_persists_four_source_scopes(tmp_path):
    days = (date(2026, 8, 6), date(2026, 8, 7))
    seen: list[str] = []
    result = _run(
        tmp_path,
        "investor",
        days,
        [_investor_body(*days)] * 4,
        seen,
        4,
    )
    assert result.completed_now == 4
    assert result.requested_business_calls == 4
    assert result.normalized_rows == 40
    checkpoint = json.loads(result.checkpoint_path.read_text())
    assert checkpoint["status"] == "BATCH_COMPLETE"
    assert len(checkpoint["completed"]) == 4
    assert len(seen) == len(set(seen)) == 4

    frames = [
        pd.read_parquet(path)
        for path in (
            tmp_path / "data/normalized/kr_short_selling_investor_daily"
        ).rglob("data.parquet")
    ]
    stored = pd.concat(frames, ignore_index=True)
    assert len(stored) == 40
    assert not stored.duplicated(["date", "market", "investor_type", "metric"]).any()
    assert set(stored["date"].astype(str)) == {"2026-08-06", "2026-08-07"}
    assert set(stored["market"]) == {"KOSPI", "KOSDAQ"}
    assert set(stored["metric"]) == {"volume", "trading_value"}


