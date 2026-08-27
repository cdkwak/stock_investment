from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from stock_data.orchestration.kr_index_daily_live import (
    IndexDailyLiveCaptureError,
    capture_one_finalized_date,
)
from stock_data.providers.pykrx.kr_index_daily import PYKRX_COLUMN_MAP
from stock_data.providers.pykrx.safety import PykrxRequestPolicy


KST = ZoneInfo("Asia/Seoul")


def _raw(day: str = "2026-08-10") -> pd.DataFrame:
    values = [100.0, 102.0, 99.0, 101.0, 10, 1000, 10000]
    return pd.DataFrame(
        {column: [value] for column, value in zip(PYKRX_COLUMN_MAP, values)},
        index=pd.to_datetime([day]),
    )


def _policy(sleeps: list[float]) -> PykrxRequestPolicy:
    ticks = iter([0.0, 0.0, 2.0, 2.0, 4.0, 4.0])
    return PykrxRequestPolicy(
        min_interval_seconds=2,
        max_consecutive_requests=3,
        max_consecutive_failures=1,
        sleep_fn=sleeps.append,
        monotonic_fn=lambda: next(ticks),
    )


def test_three_retry_zero_calls_land_before_offline_promotion(tmp_path):
    calls = []
    sleeps = []

    class Stock:
        def get_index_ohlcv(self, start, end, ticker):
            calls.append((start, end, ticker))
            return _raw()

    result = capture_one_finalized_date(
        "2026-08-10",
        finalized_at=datetime(2026, 8, 10, 16, tzinfo=KST),
        finality_confirmed=True,
        run_id="krx-20260810",
        landing_root=tmp_path / "landing",
        state_root=tmp_path / "state",
        stock_module=Stock(),
        policy=_policy(sleeps),
        now=datetime(2026, 8, 18, 1, tzinfo=KST),
    )

    assert calls == [
        ("20260810", "20260810", "1001"),
        ("20260810", "20260810", "2001"),
        ("20260810", "20260810", "1028"),
    ]
    assert result.business_calls == 3 and result.retry_count == 0
    assert result.kr_index_landing.is_file() and result.kospi200_landing.is_file()
    assert not (tmp_path / "normalized").exists()
    checkpoint = json.loads(result.checkpoint_path.read_text(encoding="utf-8"))
    assert checkpoint["status"] == "COMPLETE"
    assert len(checkpoint["raw"]) == 3 and len(checkpoint["normalized"]) == 2
    assert all(len(row["sha256"]) == 64 for row in checkpoint["raw"] + checkpoint["normalized"])


def test_failure_is_not_retried_and_partial_landing_is_retained(tmp_path):
    calls = []

    class Stock:
        def get_index_ohlcv(self, start, end, ticker):
            calls.append(ticker)
            if ticker == "2001":
                raise RuntimeError("blocked secret=do-not-log")
            return _raw()

    with pytest.raises(IndexDailyLiveCaptureError, match="retry-zero"):
        capture_one_finalized_date(
            "2026-08-10",
            finalized_at=datetime(2026, 8, 10, 16, tzinfo=KST),
            finality_confirmed=True,
            run_id="stopped",
            landing_root=tmp_path / "landing",
            state_root=tmp_path / "state",
            stock_module=Stock(),
            policy=PykrxRequestPolicy(min_interval_seconds=0, max_consecutive_requests=3),
            now=datetime(2026, 8, 18, 1, tzinfo=KST),
        )
    assert calls == ["1001", "2001"]
    assert (tmp_path / "landing" / "stopped" / "source" / "kospi.parquet").is_file()
    checkpoint = json.loads(
        (tmp_path / "state" / "kr_index_daily_live" / "stopped" / "checkpoint.json")
        .read_text(encoding="utf-8")
    )
    assert checkpoint["status"] == "STOPPED"
    assert checkpoint["business_calls"] == 2
    assert "do-not-log" not in json.dumps(checkpoint)


@pytest.mark.parametrize(
    ("finality_confirmed", "finalized_at", "match"),
    [
        (False, datetime(2026, 8, 10, 16, tzinfo=KST), "confirmation"),
        (True, datetime(2026, 8, 10, 16), "timezone-aware"),
    ],
)
def test_explicit_finality_is_required(
    tmp_path: Path, finality_confirmed: bool, finalized_at: datetime, match: str
):
    with pytest.raises(IndexDailyLiveCaptureError, match=match):
        capture_one_finalized_date(
            "2026-08-10",
            finalized_at=finalized_at,
            finality_confirmed=finality_confirmed,
            run_id="blocked",
            landing_root=tmp_path / "landing",
            state_root=tmp_path / "state",
            stock_module=object(),
        )


def test_existing_run_is_immutable(tmp_path):
    (tmp_path / "landing" / "same").mkdir(parents=True)
    with pytest.raises(IndexDailyLiveCaptureError, match="already has"):
        capture_one_finalized_date(
            "2026-08-10",
            finalized_at=datetime(2026, 8, 10, 16, tzinfo=KST),
            finality_confirmed=True,
            run_id="same",
            landing_root=tmp_path / "landing",
            state_root=tmp_path / "state",
            stock_module=object(),
            now=datetime(2026, 8, 18, 1, tzinfo=KST),
        )
