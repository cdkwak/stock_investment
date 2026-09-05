"""Resumable Landing-first backfill for two separate BOK ECOS daily rates."""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Callable
from uuid import uuid4

import pandas as pd
import requests


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from stock_data.contracts.bok_ecos_market_rates import (
    BOK_ECOS_KR_MARKET_RATE_DAILY,
)
from stock_data.providers.bok_ecos_market_rates_daily import (
    BokEcosMarketRatesProviderError,
    INFO_NO_DATA,
    MAX_WINDOW_DAYS,
    SERIES_SPECS,
    capture_range,
)
from stock_data.storage.contract_arrow import dataframe_to_contract_table
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic
from stock_data.validation.data_v1 import validate_data_v1


DATASET_ID = BOK_ECOS_KR_MARKET_RATE_DAILY.name
NORMALIZED_RELATIVE = Path("data/normalized") / DATASET_ID
STATE_RELATIVE = Path("data/state") / f"{DATASET_ID}_backfill.json"
MIN_CALL_INTERVAL_SECONDS = 0.55
SERIES = tuple(SERIES_SPECS)


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("date must be YYYY-MM-DD") from error


def plan_windows(
    start: date,
    end: date,
    *,
    max_window_days: int = MAX_WINDOW_DAYS,
) -> tuple[tuple[date, date], ...]:
    """Return contiguous inclusive windows, each small enough for <=400 rows."""
    if start > end:
        raise ValueError("start must not be after end")
    if max_window_days < 1 or max_window_days > MAX_WINDOW_DAYS:
        raise ValueError("max_window_days must be between 1 and 400")
    windows: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        window_end = min(end, cursor + timedelta(days=max_window_days - 1))
        windows.append((cursor, window_end))
        cursor = window_end + timedelta(days=1)
    return tuple(windows)


def validate_market_rates(frame: pd.DataFrame) -> None:
    validate_data_v1(frame, BOK_ECOS_KR_MARKET_RATE_DAILY, allow_empty=False)
    if set(frame["series"].astype(str)) - set(SERIES):
        raise ValueError("BOK market-rate series identity differs")
    if (pd.to_numeric(frame["rate_percent"], errors="coerce") < 0).any():
        raise ValueError("BOK market rate must be nonnegative")
    for series, spec in SERIES_SPECS.items():
        rows = frame.loc[frame["series"] == series]
        if rows.empty:
            continue
        if set(rows["item_code"].astype(str)) != {str(spec["item_code"])}:
            raise ValueError(f"BOK market-rate item identity differs for {series}")
    if set(frame["stat_code"].astype(str)) != {"817Y002"}:
        raise ValueError("BOK market-rate table identity differs")
    if set(frame["unit"].astype(str)) != {"연%"}:
        raise ValueError("BOK market-rate unit differs")
    if set(frame["source"].astype(str)) != {"BOK_ECOS"}:
        raise ValueError("BOK market-rate source identity differs")
    if set(frame["source_operation"].astype(str)) != {"StatisticSearch"}:
        raise ValueError("BOK market-rate operation identity differs")


def _read_existing(project_root: Path) -> pd.DataFrame:
    try:
        return read_dataset(
            project_root / NORMALIZED_RELATIVE,
            BOK_ECOS_KR_MARKET_RATE_DAILY,
            validate_market_rates,
        )
    except FileNotFoundError:
        return pd.DataFrame(columns=BOK_ECOS_KR_MARKET_RATE_DAILY.column_names)


def _merge_append_only(existing: pd.DataFrame, incoming: pd.DataFrame) -> pd.DataFrame:
    if existing.empty:
        merged = incoming.copy()
    else:
        prior = existing.set_index(["date", "series"], drop=False)
        accepted: list[int] = []
        identity_fields = (
            "rate_percent", "item_code", "stat_code", "unit", "source",
            "source_operation",
        )
        for index, row in incoming.iterrows():
            key = (str(row["date"]), str(row["series"]))
            if key not in prior.index:
                accepted.append(index)
                continue
            retained = prior.loc[key]
            if isinstance(retained, pd.DataFrame):
                raise ValueError(f"duplicate retained BOK market-rate key: {key}")
            if any(retained[field] != row[field] for field in identity_fields):
                raise ValueError(
                    f"append-only BOK market-rate conflict for {key[0]} {key[1]}"
                )
        merged = pd.concat([existing, incoming.loc[accepted]], ignore_index=True)
    merged["date"] = pd.to_datetime(merged["date"], errors="raise").dt.date
    merged = merged.sort_values(["date", "series"], kind="stable").reset_index(drop=True)
    validate_market_rates(merged)
    return merged


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _load_completed(path: Path) -> set[str]:
    if not path.exists():
        return set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("dataset") != DATASET_ID or payload.get("schema_version") != 1:
        raise ValueError("BOK market-rate backfill state identity differs")
    completed = payload.get("completed_windows")
    if not isinstance(completed, list) or not all(isinstance(item, str) for item in completed):
        raise ValueError("BOK market-rate backfill state is malformed")
    return set(completed)


def _window_key(series: str, start: date, end: date) -> str:
    return f"{series}:{start.isoformat()}:{end.isoformat()}"


def summarize(frame: pd.DataFrame) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for series in SERIES:
        rows = frame.loc[frame["series"] == series]
        if rows.empty:
            result[series] = {"first": None, "last": None, "rows": 0}
        else:
            dates = pd.to_datetime(rows["date"], errors="raise")
            result[series] = {
                "first": dates.min().date().isoformat(),
                "last": dates.max().date().isoformat(),
                "rows": int(len(rows)),
            }
    return result


def verify_retained(project_root: Path) -> dict[str, dict[str, object]]:
    frame = _read_existing(Path(project_root).resolve())
    if frame.empty:
        raise ValueError("BOK market-rate Normalized dataset is empty")
    validate_market_rates(frame)
    if set(frame["series"]) != set(SERIES):
        raise ValueError("BOK market-rate Normalized dataset does not contain both series")
    restored = dataframe_to_contract_table(
        frame, BOK_ECOS_KR_MARKET_RATE_DAILY,
    ).to_pandas()
    if len(restored) != len(frame):
        raise ValueError("BOK market-rate Arrow round-trip row count differs")
    return summarize(frame)


def run_backfill(
    *,
    project_root: Path,
    start: date,
    end: date,
    api_key: str,
    session: Any | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    root = Path(project_root).resolve()
    windows = plan_windows(start, end)
    state_path = root / STATE_RELATIVE
    completed = _load_completed(state_path)
    transport = session or requests.Session()
    calls = 0
    skipped = 0
    last_call_at: float | None = None
    for series in SERIES:
        for window_start, window_end in windows:
            key = _window_key(series, window_start, window_end)
            if key in completed:
                skipped += 1
                continue
            if last_call_at is not None:
                remaining = MIN_CALL_INTERVAL_SECONDS - (time.monotonic() - last_call_at)
                if remaining > 0:
                    sleep_fn(remaining)
            captured = capture_range(
                root,
                series=series,
                start=window_start,
                end=window_end,
                api_key=api_key,
                session=transport,
            )
            last_call_at = time.monotonic()
            calls += captured.api_calls
            if captured.result_code != INFO_NO_DATA:
                existing = _read_existing(root)
                merged = _merge_append_only(existing, captured.frame)
                if len(merged) != len(existing):
                    write_dataset_atomic(
                        merged,
                        root / NORMALIZED_RELATIVE,
                        BOK_ECOS_KR_MARKET_RATE_DAILY,
                        validate_market_rates,
                    )
                    verified = _read_existing(root)
                    if not dataframe_to_contract_table(
                        verified, BOK_ECOS_KR_MARKET_RATE_DAILY,
                    ).equals(dataframe_to_contract_table(
                        merged, BOK_ECOS_KR_MARKET_RATE_DAILY,
                    )):
                        raise ValueError("BOK market-rate Normalized read-back differs")
            completed.add(key)
            current = _read_existing(root)
            _atomic_json(state_path, {
                "schema_version": 1,
                "dataset": DATASET_ID,
                "contract_version": BOK_ECOS_KR_MARKET_RATE_DAILY.version,
                "requested_start": start.isoformat(),
                "requested_end": end.isoformat(),
                "completed_windows": sorted(completed),
                "summary": summarize(current),
                "updated_at_utc": datetime.now(timezone.utc).isoformat(),
            })
    summary = verify_retained(root)
    return {
        "schema_version": 1,
        "dataset": DATASET_ID,
        "status": "COMPLETE",
        "requested_start": start.isoformat(),
        "requested_end": end.isoformat(),
        "windows_per_series": len(windows),
        "api_calls": calls,
        "windows_skipped_from_checkpoint": skipped,
        "summary": summary,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backfill separate BOK ECOS Korean daily market-rate series",
    )
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--start", type=_date, default=date(1987, 1, 1))
    parser.add_argument("--end", type=_date, default=date.today())
    parser.add_argument("--verify-only", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.project_root.resolve()
    if args.verify_only:
        print(json.dumps(verify_retained(root), ensure_ascii=False, sort_keys=True))
        print("verification passed")
        return 0
    from dotenv import load_dotenv

    load_dotenv(root / ".env", override=False)
    api_key = os.environ.get("BOK_ECOS_API_KEY", "")
    # The execution sandbox injects a non-routable HTTP proxy; direct ECOS TLS
    # is used without exposing the credential-bearing URL in errors or receipts.
    session = requests.Session()
    session.trust_env = False
    try:
        result = run_backfill(
            project_root=root,
            start=args.start,
            end=args.end,
            api_key=api_key,
            session=session,
        )
    except (BokEcosMarketRatesProviderError, OSError, TypeError, ValueError) as error:
        raise SystemExit(str(error)) from error
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
