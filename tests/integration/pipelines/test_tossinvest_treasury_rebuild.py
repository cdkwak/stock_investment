from __future__ import annotations

from datetime import datetime, timezone
import json

import pandas as pd
import pyarrow.parquet as pq

from stock_data.contracts.tossinvest_historical import KR_TREASURY_YIELD_DAILY
from stock_data.pipelines.tossinvest_historical import (
    _normalize_partition_dates,
    rebuild_treasury_from_landing_atomic,
)
from stock_data.providers.tossinvest.historical import normalize_treasury_yield


def _candle():
    return {
        "timestamp": "2021-04-12T00:00:00+09:00",
        "openPrice": "1.5",
        "highPrice": "1.6",
        "lowPrice": "1.4",
        "closePrice": "1.55",
        "volume": "0",
    }


def test_rebuild_treasury_uses_landing_and_preserves_unknown_availability(tmp_path):
    target = "KR_BOND_10Y"
    collected_at = datetime(2026, 8, 11, 3, 0, tzinfo=timezone.utc)
    landing = (
        tmp_path / "data/landing/tossinvest/getMarketIndicatorCandles"
        / target / "20260811T030000000000Z_p00000.json"
    )
    landing.parent.mkdir(parents=True)
    landing.write_text(json.dumps({
        "collected_at": collected_at.isoformat(),
        "source": "tossinvest_open_api",
        "operation": "getMarketIndicatorCandles",
        "target": target,
        "cursor_parameter": "before",
        "cursor": None,
        "rate_limit": {
            "group": "MARKET_INDICATOR_CHART",
            "limit": 5,
            "remaining": 4,
            "reset_seconds": 1,
            "retry_after_seconds": None,
        },
        "raw_response": {"result": {"candles": [_candle()], "nextBefore": None}},
    }), encoding="utf-8")
    state = tmp_path / "data/state/toss_kr_treasury_yield_daily.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({
        "dataset": "kr_treasury_yield_daily",
        "status": "complete",
        "completed_targets": [target],
        "valid_empty_targets": [],
        "failed_targets": {},
        "progress": {},
        "market_calls": 1,
        "token_calls": 1,
    }), encoding="utf-8")

    old = normalize_treasury_yield(
        [_candle()], instrument=target, collected_at=collected_at
    )
    old["availability_date"] = old["date"]
    old = _normalize_partition_dates(old)
    path = (
        tmp_path / "data/normalized/kr_treasury_yield_daily"
        / "instrument=KR_BOND_10Y/year=2021/data.parquet"
    )
    path.parent.mkdir(parents=True)
    stored = old.copy()
    stored["date"] = pd.to_datetime(stored["date"]).dt.date
    stored.to_parquet(path, index=False)

    backup = rebuild_treasury_from_landing_atomic(
        tmp_path,
        instruments=(target,),
        expected_files=1,
        expected_rows=1,
        expected_partitions=1,
    )

    rebuilt_path = (
        tmp_path / "data/normalized/kr_treasury_yield_daily"
        / "instrument=KR_BOND_10Y/year=2021/data.parquet"
    )
    rebuilt = pd.read_parquet(rebuilt_path)
    assert len(rebuilt) == 1
    assert rebuilt["availability_date"].isna().all()
    assert rebuilt["updated_at"].isna().all()
    assert backup.exists()
    arrow = pq.ParquetFile(rebuilt_path).schema_arrow
    assert str(arrow.field("availability_date").type) == "string"
    assert str(arrow.field("collected_at").type) == "timestamp[ns, tz=UTC]"
