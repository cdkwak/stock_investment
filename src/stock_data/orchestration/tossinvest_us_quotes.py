"""Scheduler-only, one-call Toss U.S. watchlist quote collection."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, time, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from stock_data.contracts.base import ColumnContract, DatasetContract
from stock_data.contracts.global_equity import GLOBAL_EQUITY_REGISTRY
from stock_data.contracts.global_etf import GLOBAL_ETF_REGISTRY
from stock_data.orchestration.current_observation_supervisor import (
    CurrentObservationProcessLock,
)
from stock_data.orchestration.exchange_calendar import (
    ExchangeMarket,
    ExchangeTradingCalendar,
)
from stock_data.providers.tossinvest.client import TossInvestAPIResponse, TossInvestClient
from stock_data.providers.tossinvest.us_quotes import (
    TossInvestUSQuoteRateLimited,
    fetch_us_quotes,
)
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic


KST = ZoneInfo("Asia/Seoul")
US_EASTERN = ZoneInfo("America/New_York")
LANE = "TOSSINVEST_US_QUOTES_30M"
# Matches the existing Yahoo current-observation lane name and PT30M interval.
CADENCE_GROUP = "GLOBAL_30M"
TOSSINVEST_US_QUOTE_SYMBOLS = (
    "SKHY", "SOXL", "SOXX", "TQQQ", "QQQ", "EWY", "SGOV", "VGLT",
)
ARTIFACT_PATH = Path("artifacts/intraday/tossinvest_us_quotes_latest.json")
DATASET_PATH = Path("data/normalized/tossinvest_us_quote_30m")
LANDING_ROOT = Path("data/landing/tossinvest/us_quotes_30m")
LOCK_PATH = Path("data/state/tossinvest_us_quotes_30m.lock")

TOSSINVEST_US_QUOTE_30M = DatasetContract(
    name="tossinvest_us_quote_30m", version=1, status="active",
    description=(
        "As-retrieved Toss Securities U.S. watchlist quotes sampled by the bounded "
        "30-minute scheduler lane; not a bar or official close."
    ),
    source="tossinvest_open_api", layer="normalized", storage_format="parquet",
    frequency="intraday", timezone="Asia/Seoul",
    primary_key=("retrieved_at", "symbol"),
    sort_key=("date", "retrieved_at", "symbol"),
    partition_by=("date",), columns=(
        ColumnContract("date", "date32", False),
        ColumnContract("symbol", "string", False),
        ColumnContract("timestamp_kst", "string", False),
        ColumnContract("last_price", "float64", False),
        ColumnContract("currency", "string", False),
        ColumnContract("retrieved_at", "timestamp[us, UTC]", False),
    ),
)


class TossInvestUSQuoteLaneError(RuntimeError):
    pass


@dataclass
class _CapturingClient:
    client: object
    capture_response: Callable[[TossInvestAPIResponse], None]
    response: TossInvestAPIResponse | None = None
    calls: int = 0

    def get_market_data(
        self, path: str, *, params: dict[str, object] | None = None,
    ) -> TossInvestAPIResponse:
        self.calls += 1
        response = self.client.get_market_data(path, params=params)
        self.response = response
        if (
            response.http_status == 200
            and response.rate_limit.retry_after_seconds is None
        ):
            self.capture_response(response)
        return response


def registered_quote_symbols() -> tuple[str, ...]:
    registered = set(GLOBAL_EQUITY_REGISTRY) | set(GLOBAL_ETF_REGISTRY)
    return tuple(symbol for symbol in TOSSINVEST_US_QUOTE_SYMBOLS if symbol in registered)


def _eligible(now: datetime) -> bool:
    local = now.astimezone(KST).time()
    return local >= time(17, 0) or local < time(6, 0)


def session_hint(now: datetime) -> str:
    eastern = now.astimezone(US_EASTERN)
    if not ExchangeTradingCalendar(ExchangeMarket.US).is_trading_day(eastern.date()):
        return "closed"
    local = eastern.time()
    if time(4, 0) <= local < time(9, 30):
        return "pre_market"
    if time(9, 30) <= local < time(16, 0):
        return "regular"
    if time(16, 0) <= local < time(20, 0):
        return "after_hours"
    return "closed"


def validate_tossinvest_us_quote_30m(dataframe: pd.DataFrame) -> None:
    if list(dataframe.columns) != list(TOSSINVEST_US_QUOTE_30M.column_names) or dataframe.empty:
        raise TossInvestUSQuoteLaneError("Toss U.S. quote schema is invalid or empty")
    if dataframe.duplicated(list(TOSSINVEST_US_QUOTE_30M.primary_key)).any():
        raise TossInvestUSQuoteLaneError("duplicate Toss U.S. quote run/symbol key")
    if dataframe[["date", "symbol", "timestamp_kst", "currency", "retrieved_at"]].isna().any().any():
        raise TossInvestUSQuoteLaneError("Toss U.S. quote identity/provenance is null")
    allowed = set(registered_quote_symbols())
    if not set(dataframe["symbol"].astype(str)) <= allowed:
        raise TossInvestUSQuoteLaneError("Toss U.S. quote contains an unregistered symbol")
    if not dataframe["currency"].eq("USD").all():
        raise TossInvestUSQuoteLaneError("Toss U.S. quote currency differs")
    prices = pd.to_numeric(dataframe["last_price"], errors="coerce")
    if prices.isna().any() or not np.isfinite(prices.to_numpy()).all() or (prices <= 0).any():
        raise TossInvestUSQuoteLaneError("Toss U.S. quote price is invalid")
    source_times = pd.to_datetime(dataframe["timestamp_kst"], errors="coerce", utc=True)
    retrieved = pd.to_datetime(dataframe["retrieved_at"], errors="coerce", utc=True)
    dates = pd.to_datetime(dataframe["date"], errors="coerce")
    if source_times.isna().any() or retrieved.isna().any() or dates.isna().any():
        raise TossInvestUSQuoteLaneError("Toss U.S. quote timestamps are invalid")
    if not dates.dt.date.reset_index(drop=True).equals(
        source_times.dt.tz_convert(KST).dt.date.reset_index(drop=True)
    ):
        raise TossInvestUSQuoteLaneError("Toss U.S. quote date differs from timestamp_kst")


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if path.read_bytes() != body:
            raise OSError("Toss U.S. quote JSON readback differs")
    finally:
        temporary.unlink(missing_ok=True)


def _incoming_frame(quotes: list[dict[str, object]]) -> pd.DataFrame:
    frame = pd.DataFrame([{
        "date": pd.Timestamp(quote["timestamp_kst"]).tz_convert(KST).date().isoformat(),
        "symbol": quote["symbol"],
        "timestamp_kst": quote["timestamp_kst"],
        "last_price": quote["last_price"],
        "currency": quote["currency"],
        "retrieved_at": quote["retrieved_at_utc"],
    } for quote in quotes], columns=TOSSINVEST_US_QUOTE_30M.column_names)
    frame["retrieved_at"] = pd.to_datetime(frame["retrieved_at"], utc=True)
    frame["last_price"] = pd.to_numeric(frame["last_price"], errors="raise")
    validate_tossinvest_us_quote_30m(frame)
    return frame


def run_us_quote_lane(
    project_root: Path, *, now: datetime, client: object | None = None,
    dry_run: bool = False,
) -> dict[str, object]:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Toss U.S. quote lane requires a timezone-aware clock")
    root = Path(project_root).resolve()
    symbols = registered_quote_symbols()
    if symbols != TOSSINVEST_US_QUOTE_SYMBOLS:
        raise TossInvestUSQuoteLaneError("Toss U.S. quote list contains unregistered symbols")
    base = {
        "lane": LANE,
        "cadence_group": CADENCE_GROUP,
        "window_kst": "[17:00,06:00)",
        "symbols": list(symbols),
        "retry_count": 0,
    }
    if dry_run:
        return {**base, "status": "DRY_RUN_PASS", "api_calls": 0}
    if not _eligible(now):
        return {**base, "status": "WINDOW_CLOSED_API_ZERO", "api_calls": 0}
    if client is None:
        raise TossInvestUSQuoteLaneError("live Toss U.S. quote lane requires a client")

    lock = CurrentObservationProcessLock(root / LOCK_PATH)
    if not lock.acquire():
        return {**base, "status": "PROCESS_LOCKED_API_ZERO", "api_calls": 0}
    try:
        as_of_kst = now.astimezone(KST).isoformat(timespec="seconds")
        run_id = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + f"_{uuid4().hex}"
        landing = (
            root / LANDING_ROOT / now.astimezone(KST).date().isoformat()
            / run_id / "response.json"
        )

        def persist_response(response: TossInvestAPIResponse) -> None:
            raw_body = json.dumps(
                response.payload, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            _atomic_json(landing, {
                "provider": "tossinvest",
                "source_route": "/api/v1/prices",
                "symbols": list(symbols),
                "response_sha256": hashlib.sha256(raw_body).hexdigest(),
                "response": response.payload,
            })

        capture = _CapturingClient(client, persist_response)
        try:
            quotes = fetch_us_quotes(capture, symbols)
        except TossInvestUSQuoteRateLimited as error:
            return {
                **base, "status": "SKIPPED_RATE_LIMIT", "api_calls": capture.calls,
                "retry_after_seconds": error.retry_after_seconds,
            }
        if capture.calls != 1 or capture.response is None:
            raise TossInvestUSQuoteLaneError("Toss U.S. quote call accounting differs")

        retained = json.loads(landing.read_text(encoding="utf-8"))
        retained_body = json.dumps(
            retained["response"], ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if retained.get("response_sha256") != hashlib.sha256(retained_body).hexdigest():
            raise TossInvestUSQuoteLaneError("Toss U.S. quote Landing readback differs")
        if not quotes:
            return {
                **base, "status": "VALID_EMPTY_PRESERVED", "api_calls": 1,
                "landing_file": landing.relative_to(root).as_posix(),
            }

        incoming = _incoming_frame(quotes)
        dataset = root / DATASET_PATH
        try:
            existing = read_dataset(
                dataset, TOSSINVEST_US_QUOTE_30M, validate_tossinvest_us_quote_30m,
            )
        except FileNotFoundError:
            existing = pd.DataFrame(columns=TOSSINVEST_US_QUOTE_30M.column_names)
        combined = (
            incoming.copy()
            if existing.empty
            else pd.concat([existing, incoming], ignore_index=True)
        )
        combined = combined.sort_values(
            list(TOSSINVEST_US_QUOTE_30M.sort_key), kind="stable",
        ).reset_index(drop=True)
        validate_tossinvest_us_quote_30m(combined)
        write_dataset_atomic(
            combined, dataset, TOSSINVEST_US_QUOTE_30M,
            validate_tossinvest_us_quote_30m,
        )
        artifact = {
            "as_of_kst": as_of_kst,
            "provider": "tossinvest",
            "session_hint": session_hint(now),
            "quotes": [{
                "symbol": quote["symbol"],
                "last_price": quote["last_price"],
                "currency": quote["currency"],
                "timestamp_kst": quote["timestamp_kst"],
            } for quote in quotes],
        }
        _atomic_json(root / ARTIFACT_PATH, artifact)
        return {
            **base, "status": "COMPLETE", "api_calls": 1,
            "rows_appended": len(incoming),
            "landing_file": landing.relative_to(root).as_posix(),
            "artifact": ARTIFACT_PATH.as_posix(),
            "dataset": DATASET_PATH.as_posix(),
            "session_hint": artifact["session_hint"],
        }
    finally:
        lock.release()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--confirm-live", action="store_true")
    args = parser.parse_args(argv)
    now = datetime.now(timezone.utc)
    if not args.dry_run and not args.confirm_live:
        raise SystemExit("use --dry-run or explicitly pass --confirm-live")
    client = None if args.dry_run else TossInvestClient.from_environment(
        project_root=args.project_root.resolve(),
    )
    result = run_us_quote_lane(
        args.project_root, now=now, client=client, dry_run=args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ARTIFACT_PATH", "CADENCE_GROUP", "DATASET_PATH", "LANE",
    "TOSSINVEST_US_QUOTE_30M", "TOSSINVEST_US_QUOTE_SYMBOLS",
    "registered_quote_symbols", "run_us_quote_lane", "session_hint",
    "validate_tossinvest_us_quote_30m",
]
