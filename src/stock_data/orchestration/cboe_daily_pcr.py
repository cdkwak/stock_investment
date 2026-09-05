"""Landing-first once-daily Cboe PCR lane for personal local display."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Callable
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd

from stock_data.contracts.us_option_pcr import CBOE_DAILY_PCR_DAILY
from stock_data.orchestration.current_observation_supervisor import CurrentObservationProcessLock
from stock_data.providers.cboe_daily_pcr import (
    ARCHIVE_END_DATE,
    ARCHIVE_PROVIDER,
    ARCHIVE_START_DATE,
    CBOE_ARCHIVE_BASE_URL,
    CBOE_ARCHIVE_FILES,
    CBOE_DAILY_PAGE_URL,
    PROVIDER,
    REQUIRED_SCOPES,
    SUPPORTED_SCOPES,
    download_daily_pcr,
    parse_archive_pcr,
    parse_daily_pcr,
)
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic


LANE = "CBOE_DAILY_PCR"
DATASET_PATH = Path("data/normalized/cboe_daily_pcr_daily")
LANDING_ROOT = Path("data/landing/cboe/daily_pcr")
RECEIPT_PATH = Path("artifacts/scheduler_logs/STOCK_DATA_CBOE_DAILY_PCR_last.json")
LOCK_PATH = Path("data/state/cboe_daily_pcr.lock")
ATTEMPT_ROOT = Path("data/state/cboe_daily_pcr_attempts")
ARCHIVE_LANDING_ROOT = Path("data/landing/cboe/archive_pcr")
US_EASTERN = ZoneInfo("America/New_York")


class CboeDailyPcrLaneError(RuntimeError):
    pass


def target_date(now: datetime) -> date:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Cboe daily PCR lane requires a timezone-aware clock")
    return now.astimezone(US_EASTERN).date()


def validate_cboe_daily_pcr(dataframe: pd.DataFrame) -> None:
    contract = CBOE_DAILY_PCR_DAILY
    if list(dataframe.columns) != list(contract.column_names) or dataframe.empty:
        raise CboeDailyPcrLaneError("Cboe daily PCR schema is invalid or empty")
    if dataframe.duplicated(list(contract.primary_key)).any():
        raise CboeDailyPcrLaneError("duplicate Cboe daily PCR date/scope key")
    required = ["date", "scope", "call_volume", "put_volume", "provider", "retrieved_at"]
    if dataframe[required].isna().any().any():
        raise CboeDailyPcrLaneError("Cboe daily PCR required value is null")
    if not set(dataframe["scope"].astype(str)) <= set(SUPPORTED_SCOPES):
        raise CboeDailyPcrLaneError("Cboe daily PCR scope is unsupported")
    observed_dates = pd.to_datetime(dataframe["date"], errors="coerce")
    if observed_dates.isna().any():
        raise CboeDailyPcrLaneError("Cboe daily PCR date is invalid")
    for observed_date in observed_dates.dt.date.unique():
        scopes = set(dataframe.loc[observed_dates.dt.date == observed_date, "scope"].astype(str))
        if not set(REQUIRED_SCOPES) <= scopes:
            raise CboeDailyPcrLaneError("Cboe daily PCR date misses required scopes")
    for column in ("call_volume", "put_volume"):
        values = pd.to_numeric(dataframe[column], errors="coerce")
        if values.isna().any() or (values < 0).any() or (values % 1 != 0).any():
            raise CboeDailyPcrLaneError(f"Cboe daily PCR {column} is invalid")
    for column in ("call_oi", "put_oi"):
        values = pd.to_numeric(dataframe[column], errors="coerce")
        present = values.dropna()
        if (present < 0).any() or (present % 1 != 0).any():
            raise CboeDailyPcrLaneError(f"Cboe daily PCR {column} is invalid")
    for call_column, put_column, ratio_column in (
        ("call_volume", "put_volume", "volume_pcr"),
        ("call_oi", "put_oi", "oi_pcr"),
    ):
        calls = pd.to_numeric(dataframe[call_column], errors="coerce")
        puts = pd.to_numeric(dataframe[put_column], errors="coerce")
        ratios = pd.to_numeric(dataframe[ratio_column], errors="coerce")
        if calls.isna().ne(puts.isna()).any():
            raise CboeDailyPcrLaneError(f"Cboe daily PCR {call_column}/{put_column} pair is partial")
        expected = puts / calls.mask(calls.eq(0))
        same = ratios.round(12).eq(expected.round(12)) | (ratios.isna() & expected.isna())
        if not same.all():
            raise CboeDailyPcrLaneError(f"Cboe daily PCR {ratio_column} is not put/call")
    if not set(dataframe["provider"].astype(str)) <= {PROVIDER, ARCHIVE_PROVIDER}:
        raise CboeDailyPcrLaneError("Cboe daily PCR provider identity differs")
    archive_rows = dataframe["provider"].eq(ARCHIVE_PROVIDER)
    if archive_rows.any():
        archive_dates = observed_dates.loc[archive_rows].dt.date
        if ((archive_dates < ARCHIVE_START_DATE) | (archive_dates > ARCHIVE_END_DATE)).any():
            raise CboeDailyPcrLaneError("Cboe archive row is outside the supported range")
        if dataframe.loc[archive_rows, ["call_oi", "put_oi", "oi_pcr"]].notna().any().any():
            raise CboeDailyPcrLaneError("Cboe archive open interest must be null")
    if pd.to_datetime(dataframe["retrieved_at"], errors="coerce", utc=True).isna().any():
        raise CboeDailyPcrLaneError("Cboe daily PCR retrieved_at is invalid")


def _atomic_bytes(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        if path.read_bytes() != body:
            raise OSError("Cboe Landing readback differs")
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_json(path: Path, payload: object) -> None:
    _atomic_bytes(
        path,
        (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8"),
    )


def _atomic_bytes_same_ok(path: Path, body: bytes) -> None:
    if path.exists():
        if path.read_bytes() != body:
            raise OSError(f"retained Cboe bytes differ: {path.name}")
        return
    _atomic_bytes(path, body)


def _load_existing(root: Path) -> pd.DataFrame:
    try:
        return read_dataset(root / DATASET_PATH, CBOE_DAILY_PCR_DAILY, validate_cboe_daily_pcr)
    except FileNotFoundError:
        return pd.DataFrame(columns=CBOE_DAILY_PCR_DAILY.column_names)


def _nullable_dtypes(dataframe: pd.DataFrame) -> pd.DataFrame:
    result = dataframe.copy()
    for column in ("call_oi", "put_oi"):
        result[column] = pd.array(result[column], dtype="Int64")
    result["oi_pcr"] = pd.array(result["oi_pcr"], dtype="Float64")
    return result


def _receipt(root: Path, payload: dict[str, object]) -> dict[str, object]:
    receipt = {
        **payload,
        "task_name": "STOCK_DATA_CBOE_DAILY_PCR",
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        "scheduler_process_status": (
            "SUCCESS" if str(payload.get("status", "")).startswith(("COMPLETE", "NOOP")) else "FAIL"
        ),
    }
    _atomic_json(root / RECEIPT_PATH, receipt)
    return receipt


def run_cboe_daily_pcr_lane(
    project_root: Path,
    *,
    now: datetime,
    observation_date: date | None = None,
    transport: Callable[..., object] | None = None,
    source_url: str = CBOE_DAILY_PAGE_URL,
    dry_run: bool = False,
    personal_mode: bool = False,
) -> dict[str, object]:
    root = Path(project_root).resolve()
    attempt_date = target_date(now)
    requested_or_attempt_date = observation_date or attempt_date
    base = {
        "lane": LANE,
        "dataset": CBOE_DAILY_PCR_DAILY.name,
        "target_date": requested_or_attempt_date.isoformat(),
        "due_time_kst": "06:30",
        "use": "PERSONAL_NON_COMMERCIAL_LOCAL_DISPLAY_ONLY",
        "redistribution": "FORBIDDEN",
        "predictive_use": "BLOCKED",
        "source_url": source_url,
        "api_calls": 0,
    }
    if dry_run:
        return {**base, "status": "DRY_RUN_PASS"}
    if not personal_mode:
        raise CboeDailyPcrLaneError("live Cboe collection requires personal_mode")

    lock = CurrentObservationProcessLock(root / LOCK_PATH)
    if not lock.acquire():
        return _receipt(root, {**base, "status": "PROCESS_LOCKED_API_ZERO"})
    try:
        existing = _load_existing(root)
        existing_dates = (
            set(pd.to_datetime(existing["date"]).dt.date) if not existing.empty else set()
        )
        if observation_date is not None and observation_date in existing_dates:
            return _receipt(root, {**base, "status": "NOOP_IDEMPOTENT"})
        if transport is None:
            raise CboeDailyPcrLaneError("live Cboe collection requires a transport")

        attempt_path = root / ATTEMPT_ROOT / f"{attempt_date.isoformat()}.json"
        if attempt_path.exists():
            try:
                attempted = json.loads(attempt_path.read_text(encoding="utf-8"))
                recorded_date = date.fromisoformat(str(attempted.get("selected_date")))
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                recorded_date = None
            if recorded_date is not None and recorded_date in existing_dates:
                return _receipt(root, {
                    **base, "target_date": recorded_date.isoformat(),
                    "status": "NOOP_IDEMPOTENT",
                })
            return _receipt(root, {**base, "status": "DAILY_CALL_ALREADY_CONSUMED_API_ZERO"})
        _atomic_json(attempt_path, {
            "date": attempt_date.isoformat(),
            "lane": LANE,
            "started_at_utc": now.astimezone(timezone.utc).isoformat(),
            "call_budget": 1,
        })

        try:
            downloaded = download_daily_pcr(
                transport=transport,
                source_url=source_url,
                retrieved_at=now,
            )
        except Exception as error:
            return _receipt(root, {
                **base, "api_calls": 1,
                "status": "NETWORK_ERROR_DAILY_CALL_CONSUMED",
                "error_type": type(error).__name__,
            })
        run_id = downloaded.retrieved_at.strftime("%Y%m%dT%H%M%S.%fZ")
        landing_dir = root / LANDING_ROOT / f"capture_date={attempt_date.isoformat()}" / run_id
        landing_file = landing_dir / "response.bin"
        digest = hashlib.sha256(downloaded.body).hexdigest()
        _atomic_bytes(landing_file, downloaded.body)
        _atomic_json(landing_dir / "manifest.json", {
            "requested_date": observation_date.isoformat() if observation_date else None,
            "attempt_date": attempt_date.isoformat(),
            "provider": PROVIDER,
            "retrieved_at": downloaded.retrieved_at.isoformat(),
            "source_url": downloaded.source_url,
            "http_status": downloaded.status_code,
            "content_type": downloaded.content_type,
            "sha256": digest,
            "use": base["use"],
            "redistribution": base["redistribution"],
        })
        landed = {
            **base,
            "api_calls": 1,
            "landing_file": landing_file.relative_to(root).as_posix(),
            "landing_sha256": digest,
        }
        if downloaded.status_code != 200:
            return _receipt(root, {**landed, "status": "HTTP_ERROR_LANDING_PRESERVED"})
        try:
            records = parse_daily_pcr(
                landing_file.read_bytes(), observation_date=observation_date,
                retrieved_at=downloaded.retrieved_at,
                content_type=downloaded.content_type,
            )
            selected_date = records[0]["date"]
            if not isinstance(selected_date, date):
                raise CboeDailyPcrLaneError("Cboe parser returned an invalid selectedDate")
            _atomic_json(attempt_path, {
                "date": attempt_date.isoformat(),
                "selected_date": selected_date.isoformat(),
                "lane": LANE,
                "started_at_utc": now.astimezone(timezone.utc).isoformat(),
                "call_budget": 1,
            })
            _atomic_json(landing_dir / "manifest.json", {
                "date": selected_date.isoformat(),
                "requested_date": observation_date.isoformat() if observation_date else None,
                "attempt_date": attempt_date.isoformat(),
                "provider": PROVIDER,
                "retrieved_at": downloaded.retrieved_at.isoformat(),
                "source_url": downloaded.source_url,
                "http_status": downloaded.status_code,
                "content_type": downloaded.content_type,
                "sha256": digest,
                "use": base["use"],
                "redistribution": base["redistribution"],
            })
            incoming = pd.DataFrame(records, columns=CBOE_DAILY_PCR_DAILY.column_names)
            validate_cboe_daily_pcr(incoming)
        except (CboeDailyPcrLaneError, OSError, ValueError) as error:
            return _receipt(root, {
                **landed, "status": "SCHEMA_ERROR_LANDING_PRESERVED",
                "error_type": type(error).__name__,
            })
        promoted_base = {**landed, "target_date": selected_date.isoformat()}
        if selected_date in existing_dates:
            return _receipt(root, {**promoted_base, "status": "NOOP_IDEMPOTENT"})
        combined = (
            incoming if existing.empty else pd.concat(
                [_nullable_dtypes(existing), _nullable_dtypes(incoming)], ignore_index=True,
            )
        )
        combined = combined.sort_values(list(CBOE_DAILY_PCR_DAILY.sort_key), kind="stable").reset_index(drop=True)
        validate_cboe_daily_pcr(combined)
        write_dataset_atomic(
            combined, root / DATASET_PATH, CBOE_DAILY_PCR_DAILY,
            validate_cboe_daily_pcr,
        )
        return _receipt(root, {
            **promoted_base, "status": "COMPLETE", "rows_promoted": len(incoming),
            "scopes": [scope for scope in SUPPORTED_SCOPES if scope in set(incoming["scope"])],
            "normalized_path": DATASET_PATH.as_posix(),
        })
    finally:
        lock.release()


def run_cboe_archive_backfill(
    project_root: Path,
    *,
    now: datetime,
    archive_dir: Path | None = None,
    transport: Callable[..., object] | None = None,
    confirm_live: bool = False,
    personal_mode: bool = False,
) -> dict[str, object]:
    """Land and append the five stale official archive CSVs without overwrites."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise CboeDailyPcrLaneError("Cboe archive backfill requires a timezone-aware clock")
    if not personal_mode:
        raise CboeDailyPcrLaneError("Cboe archive backfill requires personal_mode")
    if archive_dir is None and not confirm_live:
        raise CboeDailyPcrLaneError(
            "provide --backfill-archive DIRECTORY or explicitly pass --confirm-live"
        )
    if archive_dir is None and transport is None:
        raise CboeDailyPcrLaneError("live Cboe archive backfill requires a transport")

    root = Path(project_root).resolve()
    source_dir = Path(archive_dir).resolve() if archive_dir is not None else None
    if source_dir is not None and not source_dir.is_dir():
        raise CboeDailyPcrLaneError(f"Cboe archive directory is missing: {source_dir}")
    run_id = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    landing_dir = root / ARCHIVE_LANDING_ROOT / run_id
    records: list[dict[str, object]] = []
    manifest_files: list[dict[str, object]] = []
    api_calls = 0
    for scope, filename in CBOE_ARCHIVE_FILES.items():
        source_url = f"{CBOE_ARCHIVE_BASE_URL}/{filename}"
        if source_dir is not None:
            source = source_dir / filename
            try:
                body = source.read_bytes()
            except OSError as error:
                raise CboeDailyPcrLaneError(f"cannot read Cboe archive: {filename}") from error
            status_code = 200
            source_identity = f"local:{filename}"
        else:
            assert transport is not None
            response = transport(
                source_url,
                timeout=20,
                headers={"Accept": "text/csv", "User-Agent": "Mozilla/5.0"},
            )
            api_calls += 1
            status_code = int(getattr(response, "status_code"))
            body = bytes(getattr(response, "content"))
            source_identity = source_url
        retained = landing_dir / filename
        _atomic_bytes_same_ok(retained, body)
        digest = hashlib.sha256(body).hexdigest()
        manifest_files.append({
            "filename": filename,
            "scope": scope,
            "source": source_identity,
            "http_status": status_code,
            "sha256": digest,
        })
        if status_code != 200:
            _atomic_json(landing_dir / "manifest.json", {"files": manifest_files})
            raise CboeDailyPcrLaneError(f"Cboe archive HTTP error: {filename}")
        records.extend(parse_archive_pcr(retained.read_bytes(), scope=scope, retrieved_at=now))
    _atomic_json(landing_dir / "manifest.json", {
        "provider": ARCHIVE_PROVIDER,
        "retrieved_at": now.astimezone(timezone.utc).isoformat(),
        "files": manifest_files,
        "use": "PERSONAL_NON_COMMERCIAL_LOCAL_DISPLAY_ONLY",
        "redistribution": "FORBIDDEN",
    })

    incoming = pd.DataFrame(records, columns=CBOE_DAILY_PCR_DAILY.column_names)
    validate_cboe_daily_pcr(incoming)
    lock = CurrentObservationProcessLock(root / LOCK_PATH)
    if not lock.acquire():
        return {"status": "PROCESS_LOCKED", "api_calls": api_calls, "rows_added": 0}
    try:
        existing = _load_existing(root)
        existing_keys = (
            set(zip(pd.to_datetime(existing["date"]).dt.date, existing["scope"].astype(str)))
            if not existing.empty else set()
        )
        new_mask = [
            (observed, scope) not in existing_keys
            for observed, scope in zip(
                pd.to_datetime(incoming["date"]).dt.date, incoming["scope"].astype(str),
            )
        ]
        additions = incoming.loc[new_mask].copy()
        if additions.empty:
            return {
                "status": "NOOP_IDEMPOTENT", "api_calls": api_calls, "rows_added": 0,
                "rows_seen": len(incoming), "landing_dir": landing_dir.relative_to(root).as_posix(),
            }
        combined = (
            additions if existing.empty else pd.concat(
                [_nullable_dtypes(existing), _nullable_dtypes(additions)], ignore_index=True,
            )
        )
        combined = combined.sort_values(
            list(CBOE_DAILY_PCR_DAILY.sort_key), kind="stable",
        ).reset_index(drop=True)
        validate_cboe_daily_pcr(combined)
        affected_years = set(pd.to_datetime(additions["date"]).dt.year)
        affected_partitions = combined.loc[
            pd.to_datetime(combined["date"]).dt.year.isin(affected_years)
        ].copy()
        write_dataset_atomic(
            affected_partitions, root / DATASET_PATH, CBOE_DAILY_PCR_DAILY,
            validate_cboe_daily_pcr,
        )
        return {
            "status": "COMPLETE", "api_calls": api_calls, "rows_added": len(additions),
            "rows_seen": len(incoming), "landing_dir": landing_dir.relative_to(root).as_posix(),
            "normalized_path": DATASET_PATH.as_posix(),
        }
    finally:
        lock.release()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the personal-only Cboe daily PCR lane")
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--date", type=date.fromisoformat)
    parser.add_argument("--source-url", default=CBOE_DAILY_PAGE_URL)
    parser.add_argument(
        "--backfill-archive", nargs="?", const="", metavar="DIRECTORY",
        help="backfill five archive CSVs from DIRECTORY, or fetch once with --confirm-live",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--confirm-live", action="store_true")
    parser.add_argument("--personal-mode", action="store_true")
    args = parser.parse_args(argv)
    if args.backfill_archive is None and not args.dry_run and not args.confirm_live:
        raise SystemExit("use --dry-run or explicitly pass --confirm-live")
    now = datetime.now(timezone.utc)
    transport = None
    if not args.dry_run and (args.backfill_archive in (None, "")):
        import requests
        transport = requests.get
    if args.backfill_archive is not None:
        if args.dry_run:
            raise SystemExit("--dry-run is for the daily lane, not archive backfill")
        result = run_cboe_archive_backfill(
            args.project_root, now=now,
            archive_dir=Path(args.backfill_archive) if args.backfill_archive else None,
            transport=transport, confirm_live=args.confirm_live,
            personal_mode=args.personal_mode,
        )
    else:
        result = run_cboe_daily_pcr_lane(
            args.project_root, now=now, observation_date=args.date,
            transport=transport, source_url=args.source_url,
            dry_run=args.dry_run, personal_mode=args.personal_mode,
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0 if str(result["status"]).startswith(("COMPLETE", "NOOP", "DRY_RUN")) else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "ARCHIVE_LANDING_ROOT", "ATTEMPT_ROOT", "DATASET_PATH", "LANDING_ROOT", "LANE",
    "RECEIPT_PATH", "CboeDailyPcrLaneError", "run_cboe_archive_backfill",
    "run_cboe_daily_pcr_lane", "target_date", "validate_cboe_daily_pcr",
]
