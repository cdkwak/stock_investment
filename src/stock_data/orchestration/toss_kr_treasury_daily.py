from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from stock_data.contracts.tossinvest_historical import KR_TREASURY_YIELD_DAILY
from stock_data.pipelines.tossinvest_historical import (
    TREASURY_INSTRUMENTS,
    _atomic_json,
    _extract,
)
from stock_data.providers.tossinvest import TossInvestClient, normalize_treasury_yield
from stock_data.providers.tossinvest.historical import TREASURY_YIELD_OPERATION
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic
from stock_data.validation.tossinvest_historical import validate_toss_historical


def _read(root: Path) -> pd.DataFrame:
    return read_dataset(
        root,
        KR_TREASURY_YIELD_DAILY,
        lambda frame: validate_toss_historical(frame, KR_TREASURY_YIELD_DAILY),
    )


def _retained_dates(frame: pd.DataFrame, target: str) -> dict[str, set[str]]:
    return {
        instrument: set(
            frame.loc[
                frame["instrument"].astype(str).eq(instrument)
                & frame["date"].astype(str).le(target),
                "date",
            ].astype(str)
        )
        for instrument in TREASURY_INSTRUMENTS
    }


def refresh_toss_kr_treasury_daily(
    project_root: Path,
    *,
    intended_date: str | date,
    client: TossInvestClient | None = None,
) -> dict[str, Any]:
    """Append all complete missing dates through one T+1 target with six calls.

    The six instruments form one atomic promotion.  Provider values are kept as
    Toss OHLC observations and are never reclassified as official BOK/KOFIA
    yields.  Availability remains ``AS_RETRIEVED`` at the scheduler boundary.
    """

    target = pd.Timestamp(intended_date).date().isoformat()
    live = project_root / "data/normalized" / KR_TREASURY_YIELD_DAILY.name
    existing = _read(live)
    retained = _retained_dates(existing, target)
    if all(target in values for values in retained.values()):
        return {
            "status": "already_complete",
            "intended_date": target,
            "token_calls": 0,
            "market_calls": 0,
            "promoted_rows": 0,
        }
    if client is None:
        raise ValueError("Toss client is required for an uncompleted treasury date")

    initial_token_calls = client.token_request_count
    initial_market_calls = client.market_request_count
    candidates: list[pd.DataFrame] = []
    date_sets: dict[str, set[str]] = {}
    landing_files: list[str] = []
    for instrument in TREASURY_INSTRUMENTS:
        response = client.get_market_data(
            f"/api/v1/market-indicators/{instrument}/candles",
            params={"interval": "1d", "count": 200},
        )
        observed = datetime.now(timezone.utc)
        relative = (
            Path("data/landing/tossinvest")
            / TREASURY_YIELD_OPERATION
            / instrument
            / f"daily_{target}_{observed.strftime('%Y%m%dT%H%M%S%fZ')}.json"
        )
        _atomic_json(
            project_root / relative,
            {
                "collected_at": observed.isoformat(),
                "source": "tossinvest_open_api",
                "operation": TREASURY_YIELD_OPERATION,
                "target": instrument,
                "intended_date": target,
                "request": {"interval": "1d", "count": 200},
                "raw_response": response.payload,
            },
        )
        landing_files.append(relative.as_posix())
        rows, _next_cursor = _extract(response.payload, "candles", "nextBefore")
        normalized = normalize_treasury_yield(
            rows, instrument=instrument, collected_at=observed
        )
        missing = normalized.loc[
            normalized["date"].astype(str).le(target)
            & ~normalized["date"].astype(str).isin(retained[instrument])
        ].copy()
        dates = set(missing["date"].astype(str))
        if target not in dates:
            raise RuntimeError(
                f"Toss {instrument} treasury response does not contain the intended date"
            )
        date_sets[instrument] = dates
        candidates.append(missing)

    distinct_sets = {tuple(sorted(values)) for values in date_sets.values()}
    if len(distinct_sets) != 1:
        raise RuntimeError("Toss treasury instruments do not expose one complete date set")

    incoming = pd.concat(candidates, ignore_index=True).sort_values(
        list(KR_TREASURY_YIELD_DAILY.sort_key), kind="stable"
    ).reset_index(drop=True)
    validate_toss_historical(incoming, KR_TREASURY_YIELD_DAILY)
    combined = pd.concat([existing, incoming], ignore_index=True)
    combined = combined.drop_duplicates(
        list(KR_TREASURY_YIELD_DAILY.primary_key), keep="last"
    ).sort_values(list(KR_TREASURY_YIELD_DAILY.sort_key), kind="stable").reset_index(
        drop=True
    )
    validate_toss_historical(combined, KR_TREASURY_YIELD_DAILY)
    write_dataset_atomic(
        combined,
        live,
        KR_TREASURY_YIELD_DAILY,
        lambda frame: validate_toss_historical(frame, KR_TREASURY_YIELD_DAILY),
    )
    verified = _read(live)
    if not all(
        target in values for values in _retained_dates(verified, target).values()
    ):
        raise RuntimeError("Toss treasury atomic promotion did not retain all six instruments")

    state_path = project_root / "data/state/toss_kr_treasury_daily_incremental.json"
    _atomic_json(
        state_path,
        {
            "dataset": KR_TREASURY_YIELD_DAILY.name,
            "status": "complete",
            "availability": "AS_RETRIEVED",
            "latest_date": target,
            "completed_dates": sorted(next(iter(distinct_sets))),
            "landing_files": landing_files,
            "updated_at_utc": datetime.now(timezone.utc).isoformat(),
        },
    )
    return {
        "status": "complete",
        "intended_date": target,
        "token_calls": client.token_request_count - initial_token_calls,
        "market_calls": client.market_request_count - initial_market_calls,
        "promoted_rows": len(incoming),
        "source_rows": len(combined),
    }


__all__ = ["refresh_toss_kr_treasury_daily"]
