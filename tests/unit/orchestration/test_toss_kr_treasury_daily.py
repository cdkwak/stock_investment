from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd

from stock_data.contracts.tossinvest_historical import KR_TREASURY_YIELD_DAILY
from stock_data.orchestration.toss_kr_treasury_daily import (
    refresh_toss_kr_treasury_daily,
)
from stock_data.pipelines.tossinvest_historical import TREASURY_INSTRUMENTS
from stock_data.providers.tossinvest.historical import normalize_treasury_yield
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic
from stock_data.validation.tossinvest_historical import validate_toss_historical


def _candle(day: str, close: str) -> dict[str, str]:
    return {
        "timestamp": f"{day}T00:00:00+09:00",
        "openPrice": close,
        "highPrice": close,
        "lowPrice": close,
        "closePrice": close,
        "volume": "0",
    }


def _read(root):
    return read_dataset(
        root,
        KR_TREASURY_YIELD_DAILY,
        lambda frame: validate_toss_historical(frame, KR_TREASURY_YIELD_DAILY),
    )


def test_six_tenor_treasury_append_is_atomic_and_replay_is_api_zero(tmp_path) -> None:
    observed = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)
    existing = pd.concat(
        [
            normalize_treasury_yield(
                [_candle("2026-08-25", "2.50")],
                instrument=instrument,
                collected_at=observed,
            )
            for instrument in TREASURY_INSTRUMENTS
        ],
        ignore_index=True,
    ).sort_values(
        list(KR_TREASURY_YIELD_DAILY.sort_key), kind="stable"
    ).reset_index(drop=True)
    live = tmp_path / "data/normalized/kr_treasury_yield_daily"
    write_dataset_atomic(
        existing,
        live,
        KR_TREASURY_YIELD_DAILY,
        lambda frame: validate_toss_historical(frame, KR_TREASURY_YIELD_DAILY),
    )

    class Client:
        token_request_count = 0
        market_request_count = 0

        def get_market_data(self, path, *, params):
            self.market_request_count += 1
            instrument = path.split("/")[-2]
            maturity = instrument.removeprefix("KR_BOND_").removesuffix("Y")
            return SimpleNamespace(
                payload={
                    "result": {
                        "candles": [
                            _candle("2026-08-26", f"{int(maturity) / 10 + 2:.2f}"),
                            _candle("2026-08-25", "2.50"),
                        ],
                        "nextBefore": None,
                    }
                }
            )

    client = Client()
    result = refresh_toss_kr_treasury_daily(
        tmp_path, intended_date="2026-08-26", client=client
    )

    assert result["status"] == "complete"
    assert result["market_calls"] == 6
    assert result["promoted_rows"] == 6
    retained = _read(live)
    target = retained.loc[retained["date"].astype(str).eq("2026-08-26")]
    assert set(target["instrument"].astype(str)) == set(TREASURY_INSTRUMENTS)
    assert len(list((tmp_path / "data/landing/tossinvest/getMarketIndicatorCandles").glob(
        "KR_BOND_*Y/daily_*.json"
    ))) == 6

    replay = refresh_toss_kr_treasury_daily(
        tmp_path, intended_date="2026-08-26", client=None
    )
    assert replay["status"] == "already_complete"
    assert replay["market_calls"] == 0
