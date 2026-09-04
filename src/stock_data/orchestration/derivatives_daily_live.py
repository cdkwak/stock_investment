from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import date, datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from time import monotonic
from typing import Callable, Mapping
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd

from stock_data.derived.kospi200_futures_basis import build_kospi200_futures_nearest_listed
from stock_data.derived.kospi200_option_pcr_modern import build_modern_kospi200_option_pcr
from stock_data.derived.option_walls import (
    PIT_SAFE_EOD_T_PLUS_1,
    compute_front_month_wall,
    compute_option_walls,
    join_kospi200_daily_index,
)
from stock_data.orchestration.exchange_calendar import ExchangeMarket, ExchangeTradingCalendar
from stock_data.pipelines.backfill_state import BackfillState
from stock_data.providers.data_go_kr.client import (
    DataGoKrClient,
    service_key_from_environment,
    write_landing_pages_atomic,
)
from stock_data.providers.data_go_kr.derivatives import (
    PRODUCT_SPECS,
    normalize_derivatives,
    request_filters,
)
from stock_data.published.kospi200_derivatives_bridge import build_kospi200_derivatives_bridge
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic
from stock_data.validation.data_v1 import validate_data_v1


SEOUL = ZoneInfo("Asia/Seoul")
SOURCE_KEYS = ("kospi200_futures", "kospi200_options")


class DerivativesDailyLiveError(RuntimeError):
    pass


class DerivativesDailyRollbackError(DerivativesDailyLiveError):
    pass


@dataclass(frozen=True)
class DerivativesDailyLiveResult:
    status: str
    market_date: str
    api_calls: int
    retry_count: int
    stages: tuple[str, ...]
    rows: Mapping[str, int]


@dataclass(frozen=True)
class DerivativesDailyCatchupResult:
    status: str
    completed_dates: tuple[str, ...]
    api_calls: int
    retry_count: int
    remaining_target: str | None
    last_result: DerivativesDailyLiveResult | None


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        if _read_json(temporary) != payload:
            raise DerivativesDailyLiveError("state JSON read-back differs")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _calendar_dates(project_root: Path) -> tuple[date, ...]:
    root = project_root / "data/normalized/kr_kospi200_index_daily"
    paths = sorted(root.glob("year=*/data.parquet"))
    if not paths:
        raise DerivativesDailyLiveError("KOSPI200 XKRX calendar is unavailable")
    values = pd.concat(
        [pd.read_parquet(path, columns=["date"] if path.exists() else None) for path in paths],
        ignore_index=True,
    )
    return tuple(sorted(set(pd.to_datetime(values["date"], errors="raise").dt.date)))


def latest_finalized_session(project_root: Path, *, now: datetime | None = None) -> date:
    """T+1 means a target is eligible only after a later XKRX session completed."""
    local_now = now.astimezone(SEOUL) if now is not None else datetime.now(SEOUL)
    latest_exchange_session = ExchangeTradingCalendar(ExchangeMarket.KR).latest_completed_session(
        local_now
    )
    retained = _calendar_dates(project_root)
    completed_retained = [
        value for value in retained if value <= latest_exchange_session
    ]
    if len(completed_retained) < 2:
        raise DerivativesDailyLiveError("no target with a completed successor XKRX session is retained")
    return completed_retained[-2]


def _landing_items(path: Path) -> tuple[Mapping[str, object], ...]:
    payload = _read_json(path)
    if not isinstance(payload, list) or not payload:
        raise DerivativesDailyLiveError("Landing payload is invalid")
    rows: list[Mapping[str, object]] = []
    for page in payload:
        try:
            body = page["response"]["body"]
            container = body.get("items") or {}
            item = container.get("item", []) if isinstance(container, dict) else []
        except (KeyError, TypeError):
            raise DerivativesDailyLiveError("Landing response envelope is invalid") from None
        page_rows = item if isinstance(item, list) else [item]
        if not all(isinstance(row, dict) for row in page_rows):
            raise DerivativesDailyLiveError("Landing response rows are invalid")
        rows.extend(page_rows)
    return tuple(rows)


def _collect_exact(
    project_root: Path, key: str, compact_date: str, *, landing_override: Path | None = None,
) -> tuple[pd.DataFrame, int]:
    spec = PRODUCT_SPECS[key]
    landing = landing_override or (
        project_root / "data/landing/data_go_kr" / spec.contract.name / f"{compact_date}.json"
    )
    if landing.exists():
        items = _landing_items(landing)
        calls = 0
    else:
        client = DataGoKrClient(
            endpoint=spec.endpoint,
            service_key=service_key_from_environment(project_root),
            max_attempts=1,
        )
        result = client.fetch_all(
            filters=request_filters(spec, compact_date), num_of_rows=9999, max_pages=1,
        )
        calls = len(result.pages)
        if calls != 1:
            raise DerivativesDailyLiveError(f"{key} exact-date response is empty or unbounded")
        write_landing_pages_atomic(result.pages, landing)
        items = result.items
        if result.total_count < 1:
            raise DerivativesDailyLiveError(f"{key} exact-date response is valid empty")
    frame = normalize_derivatives(items, spec)
    expected = pd.Timestamp(datetime.strptime(compact_date, "%Y%m%d").date())
    if frame.empty or set(pd.to_datetime(frame["date"], errors="raise")) != {expected}:
        raise DerivativesDailyLiveError(f"{key} response date or promotable rows differ")
    return frame, calls


def _copy_path(source: Path, target: Path) -> None:
    if not source.exists():
        raise FileNotFoundError(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, target)
    else:
        shutil.copy2(source, target)


def _append_source(candidate_root: Path, candidate_state: Path, frame: pd.DataFrame, key: str, compact: str) -> int:
    spec = PRODUCT_SPECS[key]
    existing = read_dataset(candidate_root, spec.contract, lambda value: validate_data_v1(value, spec.contract))
    combined = pd.concat([existing, frame], ignore_index=True)
    combined = combined.drop_duplicates(list(spec.contract.primary_key), keep="last")
    combined = combined.sort_values(list(spec.contract.sort_key), kind="stable").reset_index(drop=True)
    write_dataset_atomic(combined, candidate_root, spec.contract, lambda value: validate_data_v1(value, spec.contract))
    state = BackfillState.load(candidate_state, spec.contract.name)
    state.mark_completed(compact)
    restored = read_dataset(candidate_root, spec.contract, lambda value: validate_data_v1(value, spec.contract))
    if compact not in set(pd.to_datetime(restored["date"]).dt.strftime("%Y%m%d")):
        raise DerivativesDailyLiveError(f"{key} candidate lacks target date")
    return len(restored)


def _recent_wall_source_dates(
    project_root: Path, options_root: Path, target: date,
) -> tuple[date, ...]:
    available: set[date] = set()
    for path in sorted(options_root.glob("year=*/data.parquet")):
        values = pd.to_datetime(
            pd.read_parquet(path, columns=["date"])["date"], errors="raise",
        )
        if values.isna().any():
            raise DerivativesDailyLiveError("Wall rebuild source dates are invalid")
        available.update(values.dt.date)
    retained = set(_calendar_dates(project_root))
    selected = tuple(sorted(
        value for value in available & retained if value <= target
    )[-250:])
    if not selected or selected[-1] != target:
        raise DerivativesDailyLiveError(
            "Wall rebuild source lacks the exact target XKRX date"
        )
    return selected


def _wall_rows_for_dates(
    project_root: Path, options_root: Path, selected_dates: tuple[date, ...],
) -> pd.DataFrame:
    selected_set = set(selected_dates)
    option_frames: list[pd.DataFrame] = []
    index_frames: list[pd.DataFrame] = []
    index_root = project_root / "data/normalized/kr_kospi200_index_daily"
    for year in sorted({value.year for value in selected_dates}):
        option_path = options_root / f"year={year}" / "data.parquet"
        index_path = index_root / f"year={year}" / "data.parquet"
        if not option_path.is_file() or not index_path.is_file():
            raise DerivativesDailyLiveError(
                f"Wall rebuild input partition is unavailable for {year}"
            )
        options = pd.read_parquet(option_path)
        option_dates = pd.to_datetime(options["date"], errors="raise")
        if option_dates.isna().any():
            raise DerivativesDailyLiveError("Wall option partition dates are invalid")
        option_dates = option_dates.dt.date
        option_frames.append(options.loc[option_dates.isin(selected_set)].copy())
        index_daily = pd.read_parquet(
            index_path, columns=["date", "symbol", "close", "source"],
        )
        index_dates = pd.to_datetime(index_daily["date"], errors="raise")
        if index_dates.isna().any():
            raise DerivativesDailyLiveError("Wall index partition dates are invalid")
        index_dates = index_dates.dt.date
        index_frames.append(index_daily.loc[index_dates.isin(selected_set)].copy())
    selected_options = pd.concat(option_frames, ignore_index=True)
    observed_option_dates = set(
        pd.to_datetime(selected_options["date"], errors="raise").dt.date
    )
    if observed_option_dates != selected_set:
        raise DerivativesDailyLiveError("Wall rebuild option-date coverage differs")
    walls = compute_front_month_wall(compute_option_walls(selected_options))
    joined = join_kospi200_daily_index(
        walls,
        pd.concat(index_frames, ignore_index=True),
        dataset_name="kr_kospi200_index_daily",
        symbol="KOSPI200",
        pit_status=PIT_SAFE_EOD_T_PLUS_1,
        require_complete=True,
    )
    joined["date"] = pd.to_datetime(joined["date"], errors="raise").dt.normalize()
    joined_dates = set(joined["date"].dt.date)
    if joined["date"].duplicated().any() or joined_dates != selected_set:
        raise DerivativesDailyLiveError("Wall rebuilt date coverage differs")
    return joined.sort_values("date", kind="stable").reset_index(drop=True)


def _build_wall(project_root: Path, options_root: Path, target: date, output: Path) -> int:
    current_path = project_root / "artifacts/analysis/kospi200_option_wall_recent_250.csv"
    if current_path.exists():
        prior = pd.read_csv(current_path)
        if "date" not in prior or prior.empty or len(prior) > 250:
            raise DerivativesDailyLiveError("prior Wall artifact is invalid")
        prior["date"] = pd.to_datetime(prior["date"], errors="raise").dt.normalize()
        if prior["date"].isna().any() or prior["date"].duplicated().any():
            raise DerivativesDailyLiveError("prior Wall dates are invalid")
        previous_dates = sorted(
            value for value in set(prior["date"].dt.date) if value < target
        )
        calculation_dates = (
            (previous_dates[-1], target) if previous_dates else (target,)
        )
        joined = _wall_rows_for_dates(
            project_root, options_root, calculation_dates,
        )
        joined = joined.loc[joined["date"].dt.date.eq(target)].reset_index(
            drop=True
        )
        if len(joined) != 1:
            raise DerivativesDailyLiveError("Wall target-date join differs")
        missing_in_joined = [column for column in prior.columns if column not in joined.columns]
        if missing_in_joined:
            raise DerivativesDailyLiveError("prior Wall schema differs")
        # Additive derivation columns (e.g. the near-wall window added on 2026-09-03)
        # extend the retained artifact; earlier rows keep them empty.
        added = [column for column in joined.columns if column not in prior.columns]
        ordered = list(prior.columns) + added
        prior = prior.reindex(columns=ordered)
        joined = joined.loc[:, ordered]
        combined = pd.concat(
            [prior.loc[prior["date"].dt.date.ne(target)], joined],
            ignore_index=True,
        )
    else:
        selected_dates = _recent_wall_source_dates(
            project_root, options_root, target,
        )
        combined = _wall_rows_for_dates(
            project_root, options_root, selected_dates,
        )
    combined["date"] = pd.to_datetime(combined["date"], errors="raise").dt.normalize()
    combined = combined.sort_values("date", kind="stable").tail(250).reset_index(drop=True)
    if combined.empty or combined["date"].duplicated().any():
        raise DerivativesDailyLiveError("Wall candidate dates are invalid")
    output.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(output, index=False)
    restored = pd.read_csv(output, parse_dates=["date"])
    if (
        len(restored) != len(combined)
        or restored["date"].duplicated().any()
        or target not in set(restored["date"].dt.date)
    ):
        raise DerivativesDailyLiveError("Wall candidate read-back differs")
    return len(restored)


def _remove(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _acl_powershell_environment(**values: str) -> dict[str, str]:
    environment = os.environ.copy()
    for key in list(environment):
        if key.casefold() == "psmodulepath":
            del environment[key]
    environment.update(values)
    return environment


def _capture_access_sddl(path: Path) -> str | None:
    """Capture only the Windows DACL/inheritance state for exact restoration."""
    if os.name != "nt":
        return None
    powershell = shutil.which("powershell.exe")
    if powershell is None:
        raise DerivativesDailyLiveError("PowerShell is required for Windows ACL preservation")
    environment = _acl_powershell_environment(STOCK_DATA_ACL_PATH=str(path))
    script = (
        "$ErrorActionPreference='Stop';"
        "$acl=Get-Acl -LiteralPath $env:STOCK_DATA_ACL_PATH;"
        "if(-not $acl.AreAccessRulesProtected){exit 0};"
        "[Console]::Out.Write($acl.GetSecurityDescriptorSddlForm("
        "[System.Security.AccessControl.AccessControlSections]::Access))"
    )
    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, env=environment, check=False,
    )
    sddl = result.stdout.strip()
    if result.returncode:
        raise DerivativesDailyLiveError(
            f"could not capture target ACL: {type(result.stderr).__name__}"
        )
    return sddl or None


def _restore_access_sddl(path: Path, sddl: str | None) -> None:
    if sddl is None:
        return
    powershell = shutil.which("powershell.exe")
    if powershell is None:
        raise DerivativesDailyLiveError("PowerShell is required for Windows ACL preservation")
    environment = _acl_powershell_environment(
        STOCK_DATA_ACL_PATH=str(path), STOCK_DATA_ACL_SDDL=sddl,
    )
    script = (
        "$ErrorActionPreference='Stop';"
        "$section=[System.Security.AccessControl.AccessControlSections]::Access;"
        "$item=Get-Item -LiteralPath $env:STOCK_DATA_ACL_PATH;"
        "if($item.PSIsContainer){"
        "$acl=New-Object System.Security.AccessControl.DirectorySecurity"
        "}else{"
        "$acl=New-Object System.Security.AccessControl.FileSecurity"
        "};"
        "$acl.SetSecurityDescriptorSddlForm($env:STOCK_DATA_ACL_SDDL,$section);"
        "$item.SetAccessControl($acl);"
        "$restored=(Get-Acl -LiteralPath $env:STOCK_DATA_ACL_PATH)."
        "GetSecurityDescriptorSddlForm($section);"
        "if($restored -ne $env:STOCK_DATA_ACL_SDDL){throw 'ACL read-back differs'}"
    )
    result = subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, env=environment, check=False,
    )
    if result.returncode:
        raise DerivativesDailyLiveError("could not restore target ACL")


def _promote_atomic(
    project_root: Path,
    candidates: Mapping[str, Path],
    targets: Mapping[str, Path],
    target_date: date,
    *,
    validate_readback: Callable[[], None] | None = None,
    report_phase: Callable[[str], None] | None = None,
) -> None:
    journal = project_root / "data/state/derivatives_price_daily_live.transaction.json"
    if journal.exists():
        raise DerivativesDailyLiveError("unfinished live transaction requires reviewed recovery")
    transaction = next(iter(candidates.values())).parents[1]
    backups = transaction / "backups"
    transaction_relative = transaction.relative_to(project_root).as_posix()
    replaced: list[str] = []
    promoted: list[str] = []
    access_sddl: dict[str, str | None] = {}

    def journal_payload(phase: str) -> dict[str, object]:
        return {
            "version": 1,
            "market_date": target_date.isoformat(),
            "transaction": transaction_relative,
            "phase": phase,
            "replaced": list(replaced),
            "promoted": list(promoted),
        }

    _atomic_json(journal, journal_payload("PREPARED"))
    try:
        for name in candidates:
            if report_phase is not None:
                report_phase(f"PROMOTING_{name.upper()}")
            source, target = candidates[name], targets[name]
            backup = backups / name
            if target.exists():
                access_sddl[name] = _capture_access_sddl(target)
                backup.parent.mkdir(parents=True, exist_ok=True)
                target.replace(backup)
            else:
                access_sddl[name] = None
            target.parent.mkdir(parents=True, exist_ok=True)
            source.replace(target)
            replaced.append(name)
            _atomic_json(journal, journal_payload(f"REPLACED_{name.upper()}"))
            _restore_access_sddl(target, access_sddl[name])
            promoted.append(name)
            _atomic_json(journal, journal_payload(f"PROMOTED_{name.upper()}"))
            if report_phase is not None:
                report_phase(f"PROMOTED_{name.upper()}")
        if validate_readback is not None:
            if report_phase is not None:
                report_phase("VALIDATING_PRODUCTION_READBACK")
            validate_readback()
            if report_phase is not None:
                report_phase("PRODUCTION_READBACK_VALIDATED")
    except Exception as primary_error:
        rollback_completed: list[str] = []
        rollback_failures: list[dict[str, str]] = []

        def rollback_failure(name: str, stage: str, error: BaseException) -> None:
            rollback_failures.append({
                "name": name,
                "stage": stage,
                "error_type": type(error).__name__,
            })

        for name in reversed(replaced):
            target = targets[name]
            backup = backups / name
            failed = False
            try:
                _remove(target)
            except Exception as error:
                failed = True
                rollback_failure(name, "REMOVE_REPLACEMENT", error)
            if target.exists():
                if not failed:
                    rollback_failure(
                        name, "VERIFY_REPLACEMENT_REMOVED",
                        DerivativesDailyLiveError("replacement target remains"),
                    )
                continue
            if backup.exists():
                try:
                    backup.replace(target)
                except Exception as error:
                    rollback_failure(name, "RESTORE_BACKUP", error)
                    continue
            if not failed:
                rollback_completed.append(name)

        for name in candidates:
            if name not in replaced:
                backup = backups / name
                if not backup.exists():
                    continue
                target = targets[name]
                if target.exists():
                    rollback_failure(
                        name, "RESTORE_UNREPLACED_TARGET_EXISTS",
                        DerivativesDailyLiveError("unreplaced target exists"),
                    )
                    continue
                try:
                    backup.replace(target)
                    rollback_completed.append(name)
                except Exception as error:
                    rollback_failure(name, "RESTORE_UNREPLACED_BACKUP", error)

        if rollback_failures:
            payload = journal_payload("ROLLBACK_FAILED")
            payload.update({
                "rollback_completed": rollback_completed,
                "rollback_failures": rollback_failures,
            })
            try:
                _atomic_json(journal, payload)
            except Exception as error:
                rollback_failure("journal", "WRITE_ROLLBACK_FAILED", error)
            raise DerivativesDailyRollbackError(
                "promotion rollback failed; reviewed recovery required"
            ) from primary_error
        journal.unlink(missing_ok=True)
        raise
    else:
        journal.unlink(missing_ok=True)


def _target_paths(project_root: Path) -> dict[str, Path]:
    data = project_root / "data"
    return {
        "source_futures": data / "normalized/kr_kospi200_futures_daily",
        "source_options": data / "normalized/kr_kospi200_options_daily",
        "state_futures": data / "state/kr_kospi200_futures_daily.json",
        "state_options": data / "state/kr_kospi200_options_daily.json",
        "bridge": data / "published/c007_kospi200_derivatives_bridge",
        "bridge_state": data / "state/kospi200_derivatives_bridge_2010_present.json",
        "basis": data / "derived/kr_kospi200_futures_nearest_listed_daily",
        "basis_state": data / "state/kospi200_futures_nearest_listed_daily.json",
        "pcr": data / "derived/kr_kospi200_option_pcr_daily",
        "pcr_state": data / "state/kospi200_option_pcr_2020_present.json",
        "wall": project_root / "artifacts/analysis/kospi200_option_wall_recent_250.csv",
        "checkpoint": data / "state/derivatives_price_daily_live.json",
    }


def _replay_complete(project_root: Path, target: date) -> bool:
    checkpoint = _target_paths(project_root)["checkpoint"]
    if not checkpoint.exists():
        return False
    payload = _read_json(checkpoint)
    if not isinstance(payload, dict) or target.isoformat() not in payload.get("completed_dates", []):
        return False
    targets = _target_paths(project_root)
    for name in ("source_futures", "source_options", "basis", "pcr"):
        partition = targets[name] / f"year={target.year}" / "data.parquet"
        if not partition.exists() or target not in set(pd.to_datetime(pd.read_parquet(partition, columns=["date"])["date"]).dt.date):
            raise DerivativesDailyLiveError(f"checkpointed {name} target is missing")
    for dataset in (
        "kr_kospi200_futures_provider_bridge_daily",
        "kr_kospi200_options_provider_bridge_daily",
    ):
        partition = targets["bridge"] / dataset / f"year={target.year}" / "data.parquet"
        if not partition.exists() or target not in set(
            pd.to_datetime(pd.read_parquet(partition, columns=["date"])["date"]).dt.date
        ):
            raise DerivativesDailyLiveError(f"checkpointed bridge {dataset} target is missing")
    wall = pd.read_csv(targets["wall"], parse_dates=["date"])
    if target not in set(wall["date"].dt.date):
        raise DerivativesDailyLiveError("checkpointed Wall target is missing")
    return True


def _source_latest_session(project_root: Path) -> date:
    latest: list[date] = []
    for dataset in ("kr_kospi200_futures_daily", "kr_kospi200_options_daily"):
        paths = sorted((project_root / "data/normalized" / dataset).glob("year=*/data.parquet"))
        if not paths:
            raise DerivativesDailyLiveError(f"{dataset} source history is unavailable")
        frame = pd.read_parquet(paths[-1], columns=["date"])
        source_dates = pd.to_datetime(frame["date"], errors="raise")
        if source_dates.empty or source_dates.isna().any():
            raise DerivativesDailyLiveError(f"{dataset} source dates are invalid")
        latest.append(source_dates.dt.date.max())
    if latest[0] != latest[1]:
        raise DerivativesDailyLiveError("futures/options source latest dates differ")
    retained = _calendar_dates(project_root)
    if latest[0] not in retained:
        raise DerivativesDailyLiveError(
            "futures/options source latest date is absent from the retained XKRX calendar"
        )
    return latest[0]


def oldest_missing_eligible_session(
    project_root: Path, *, now: datetime | None = None,
) -> date | None:
    """Return the first forward source gap whose successor has completed.

    ``None`` means both accepted Source datasets already reach the latest date
    permitted by the retained T+1 calendar.  Selection never skips a retained
    XKRX session, even when several dates are overdue.
    """
    finalized = latest_finalized_session(project_root, now=now)
    source_latest = _source_latest_session(project_root)
    if source_latest > finalized:
        raise DerivativesDailyLiveError(
            "futures/options source latest date exceeds the retained T+1 boundary"
        )
    candidates = [
        value
        for value in _calendar_dates(project_root)
        if source_latest < value <= finalized
    ]
    return candidates[0] if candidates else None


def _require_next_source_session(project_root: Path, target: date) -> None:
    source_latest = _source_latest_session(project_root)
    next_dates = [value for value in _calendar_dates(project_root) if value > source_latest]
    if not next_dates or min(next_dates) != target:
        raise DerivativesDailyLiveError("target is not the immediate next retained XKRX source session")


def _revalidation_landing_path(
    project_root: Path, key: str, target: date, completed_successor: date,
) -> Path:
    spec = PRODUCT_SPECS[key]
    return (
        project_root / "data/landing/data_go_kr" / spec.contract.name
        / "observations" / target.strftime("%Y%m%d")
        / f"after_successor_{completed_successor:%Y%m%d}.json"
    )


def _attempt_path(project_root: Path, target: date) -> Path:
    return (
        project_root / "data/state"
        / f"derivatives_price_daily_live_{target:%Y%m%d}.attempt.json"
    )


def _recovery_path(project_root: Path, target: date) -> Path:
    return (
        project_root / "data/state"
        / f"derivatives_price_daily_live_{target:%Y%m%d}.recovery.json"
    )


def _recovery_retry_path(project_root: Path, target: date) -> Path:
    return (
        project_root / "data/state"
        / f"derivatives_price_daily_live_{target:%Y%m%d}.recovery_retry.json"
    )


def _retained_exact(
    project_root: Path, key: str, target: date, completed_successor: date,
) -> tuple[pd.DataFrame, Path]:
    """Load the reviewed immutable observation without a provider fallback."""
    landing = _revalidation_landing_path(
        project_root, key, target, completed_successor,
    )
    if not landing.is_file():
        raise DerivativesDailyLiveError(
            f"retained {key} Landing response is unavailable"
        )
    spec = PRODUCT_SPECS[key]
    frame = normalize_derivatives(_landing_items(landing), spec)
    expected = pd.Timestamp(target)
    if frame.empty or set(pd.to_datetime(frame["date"], errors="raise")) != {expected}:
        raise DerivativesDailyLiveError(
            f"retained {key} response date or promotable rows differ"
        )
    return frame, landing


def _completed_successor_session(
    project_root: Path, target: date, *, now: datetime | None,
) -> date:
    retained_sessions = _calendar_dates(project_root)
    successor_dates = [value for value in retained_sessions if value > target]
    if not successor_dates:
        raise DerivativesDailyLiveError("completed successor XKRX session is unavailable")
    completed_successor = min(successor_dates)
    calendar = ExchangeTradingCalendar(ExchangeMarket.KR)
    local_now = now.astimezone(SEOUL) if now is not None else datetime.now(SEOUL)
    if calendar.latest_completed_session(local_now) < completed_successor:
        raise DerivativesDailyLiveError("successor XKRX session has not completed")
    return completed_successor


def _require_exact_failed_attempt(
    project_root: Path, target: date, completed_successor: date,
) -> tuple[Path, Mapping[str, object]]:
    attempt_path = _attempt_path(project_root, target)
    if not attempt_path.is_file():
        raise DerivativesDailyLiveError("retained failed live attempt is unavailable")
    attempt = _read_json(attempt_path)
    expected_landing = {
        key: str(
            _revalidation_landing_path(project_root, key, target, completed_successor)
            .relative_to(project_root)
        )
        for key in SOURCE_KEYS
    }
    expected = {
        "dataset": "derivatives_price_daily_live",
        "market_date": target.isoformat(),
        "completed_successor_session": completed_successor.isoformat(),
        "status": "FAILED_NO_RETRY",
        "max_calls": 2,
        "retry_count": 0,
        "api_calls": 2,
        "calls_started": list(SOURCE_KEYS),
        "calls_completed": list(SOURCE_KEYS),
        "landing_files": expected_landing,
    }
    if not isinstance(attempt, dict) or any(
        attempt.get(key) != value for key, value in expected.items()
    ):
        raise DerivativesDailyLiveError(
            "retained live attempt does not match the reviewed recovery boundary"
        )
    recoverable_acl_failure = attempt.get("error_type") == "PermissionError" or (
        attempt.get("error_type") == "DerivativesDailyLiveError"
        and attempt.get("failure_phase") == "PROMOTING_BRIDGE"
        and attempt.get("error_message") == "could not capture target ACL: str"
    )
    if not recoverable_acl_failure:
        raise DerivativesDailyLiveError(
            "retained live attempt is not an exact reviewed ACL failure"
        )
    return attempt_path, attempt


def _require_exact_failed_acl_recovery(
    project_root: Path,
    target: date,
    completed_successor: date,
    *,
    attempt_path: Path,
    attempt: Mapping[str, object],
) -> tuple[Path, Mapping[str, object]]:
    """Bind one reviewed API-zero retry to the exact ACL-only recovery failure."""
    recovery_path = _recovery_path(project_root, target)
    if not recovery_path.is_file():
        raise DerivativesDailyLiveError(
            "reviewed failed retained-Landing recovery is unavailable"
        )
    recovery = _read_json(recovery_path)
    expected = {
        "version": 1,
        "dataset": "derivatives_price_daily_live",
        "market_date": target.isoformat(),
        "completed_successor_session": completed_successor.isoformat(),
        "status": "FAILED_NO_RETRY",
        "mode": "RETAINED_LANDING_API_ZERO",
        "source_attempt": str(attempt_path.relative_to(project_root)),
        "source_attempt_status": "FAILED_NO_RETRY",
        "source_api_calls": 2,
        "api_calls": 0,
        "retry_count": 0,
        "landing_files": dict(attempt["landing_files"]),
        "error_type": "DerivativesDailyLiveError",
        "failure_phase": "PROMOTING_BRIDGE",
        "error_message": "could not restore target ACL",
    }
    if not isinstance(recovery, dict) or any(
        recovery.get(key) != value for key, value in expected.items()
    ):
        raise DerivativesDailyLiveError(
            "retained recovery does not match the reviewed ACL retry boundary"
        )
    hashes = recovery.get("landing_sha256")
    if not isinstance(hashes, dict) or set(hashes) != set(SOURCE_KEYS):
        raise DerivativesDailyLiveError(
            "retained recovery Landing hashes are incomplete"
        )
    for key in SOURCE_KEYS:
        digest = hashes.get(key)
        landing = project_root / str(dict(attempt["landing_files"])[key])
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or not landing.is_file()
            or hashlib.sha256(landing.read_bytes()).hexdigest() != digest
        ):
            raise DerivativesDailyLiveError(
                "retained recovery Landing hash differs"
            )
    return recovery_path, recovery


def _complete_derivatives_transaction(
    project_root: Path,
    *,
    target: date,
    frames: Mapping[str, pd.DataFrame],
    api_calls: int,
    status_path: Path,
    status_record: dict[str, object],
) -> dict[str, int]:
    compact = target.strftime("%Y%m%d")
    transaction = project_root / "data/staging/derivatives_daily_live" / f"{compact}-{uuid4().hex}"
    candidates_root = transaction / "candidates"
    targets = _target_paths(project_root)
    candidates = {name: candidates_root / name for name in targets}
    rows: dict[str, int] = {}
    phase = "STARTING_LOCAL_TRANSACTION"
    preserve_transaction = False

    def record_phase(value: str) -> None:
        nonlocal phase
        phase = value
        status_record.update({"status": value, "api_calls": api_calls})
        _atomic_json(status_path, status_record)

    def validate_readback() -> None:
        if not _replay_complete(project_root, target):
            raise DerivativesDailyLiveError("production read-back is incomplete")

    try:
        record_phase("COPYING_PRIOR_SOURCE_AND_STATE")
        for name in ("source_futures", "source_options", "state_futures", "state_options"):
            _copy_path(targets[name], candidates[name])
        record_phase("BUILDING_SOURCE_FUTURES")
        rows["source_futures"] = _append_source(candidates["source_futures"], candidates["state_futures"], frames["kospi200_futures"], "kospi200_futures", compact)
        record_phase("BUILDING_SOURCE_OPTIONS")
        rows["source_options"] = _append_source(candidates["source_options"], candidates["state_options"], frames["kospi200_options"], "kospi200_options", compact)

        record_phase("BUILDING_BRIDGE")
        bridge = build_kospi200_derivatives_bridge(
            legacy_futures_root=project_root / "data/normalized/krx_legacy_kospi200_futures_daily",
            official_futures_root=candidates["source_futures"],
            legacy_options_root=project_root / "data/normalized/krx_legacy_kospi200_options_daily",
            official_options_root=candidates["source_options"],
            output_bundle_root=candidates["bridge"], output_state_path=candidates["bridge_state"],
        )
        rows["bridge_futures"] = bridge["datasets"]["kr_kospi200_futures_provider_bridge_daily"]["validation"]["rows"]
        rows["bridge_options"] = bridge["datasets"]["kr_kospi200_options_provider_bridge_daily"]["validation"]["rows"]
        record_phase("BUILDING_BASIS")
        basis = build_kospi200_futures_nearest_listed(
            bridge_root=candidates["bridge"] / "kr_kospi200_futures_provider_bridge_daily",
            legacy_root=project_root / "data/normalized/krx_legacy_kospi200_futures_daily",
            official_root=candidates["source_futures"], output_root=candidates["basis"],
            output_state_path=candidates["basis_state"],
        )
        rows["basis"] = basis["validation"]["rows"]
        record_phase("COPYING_PRIOR_PCR")
        prior = transaction / "prior_pcr"
        for source in sorted(targets["pcr"].glob("year=20[01][0-9]/data.parquet")):
            _copy_path(source, prior / source.parent.name / source.name)
        record_phase("BUILDING_PCR")
        pcr = build_modern_kospi200_option_pcr(
            input_root=candidates["source_options"], input_state_path=candidates["state_options"],
            output_root=candidates["pcr"], output_state_path=candidates["pcr_state"],
            prior_derived_root=prior, start="20200101", end=compact,
        )
        rows["pcr"] = pcr["validation"]["rows"]
        record_phase("BUILDING_WALL")
        rows["wall"] = _build_wall(project_root, candidates["bridge"] / "kr_kospi200_options_provider_bridge_daily", target, candidates["wall"])
        prior_checkpoint = _read_json(targets["checkpoint"]) if targets["checkpoint"].exists() else {
            "dataset": "derivatives_price_daily_live", "completed_dates": [],
        }
        completed = sorted(set(prior_checkpoint.get("completed_dates", [])) | {target.isoformat()})
        _atomic_json(candidates["checkpoint"], {
            "version": 1, "dataset": "derivatives_price_daily_live",
            "completed_dates": completed, "last_api_calls": api_calls, "retry_count": 0,
        })
        status_record["rows"] = rows
        record_phase("CANDIDATES_VALIDATED")
        _promote_atomic(
            project_root, candidates, targets, target,
            validate_readback=validate_readback,
            report_phase=record_phase,
        )
        status_record.update({"status": "SUCCEEDED", "api_calls": api_calls, "rows": rows})
        _atomic_json(status_path, status_record)
    except Exception as error:
        preserve_transaction = isinstance(error, DerivativesDailyRollbackError)
        status_record.update({
            "status": "FAILED_NO_RETRY",
            "failure_phase": phase,
            "error_type": type(error).__name__,
            "error_message": str(error),
            "api_calls": api_calls,
        })
        _atomic_json(status_path, status_record)
        raise
    finally:
        if not preserve_transaction:
            shutil.rmtree(transaction, ignore_errors=True)
    return rows


def run_derivatives_daily(project_root: Path, *, market_date: date | None = None, now: datetime | None = None) -> DerivativesDailyLiveResult:
    from stock_data.contracts.derivatives_price_authority import (
        DATA_GO_KR_KOSPI200_DERIVATIVES_PRICE,
    )

    if not DATA_GO_KR_KOSPI200_DERIVATIVES_PRICE.live_validation_ready:
        raise DerivativesDailyLiveError("derivatives provider finality is not operationally approved")
    project_root = project_root.resolve()
    finalized = latest_finalized_session(project_root, now=now)
    automatic_target = (
        oldest_missing_eligible_session(project_root, now=now)
        if market_date is None
        else None
    )
    target = market_date or automatic_target or finalized
    if target > finalized:
        raise DerivativesDailyLiveError(
            "market date exceeds the latest conservatively finalized XKRX session"
        )
    if _replay_complete(project_root, target):
        return DerivativesDailyLiveResult("NOOP_IDEMPOTENT", target.isoformat(), 0, 0, ("source", "bridge", "basis", "pcr", "wall"), {})
    if market_date is None and automatic_target is None:
        raise DerivativesDailyLiveError(
            "source history is current but the finalized atomic chain is incomplete"
        )
    _require_next_source_session(project_root, target)

    compact = target.strftime("%Y%m%d")
    completed_successor = _completed_successor_session(
        project_root, target, now=now,
    )
    attempt_path = _attempt_path(project_root, target)
    if attempt_path.exists():
        raise DerivativesDailyLiveError(
            "retained derivatives live attempt forbids an unreviewed repeat"
        )
    attempt: dict[str, object] = {
        "version": 1,
        "dataset": "derivatives_price_daily_live",
        "market_date": target.isoformat(),
        "completed_successor_session": completed_successor.isoformat(),
        "status": "STARTED",
        "max_calls": 2,
        "retry_count": 0,
        "calls_started": [],
        "calls_completed": [],
        "landing_files": {},
    }
    _atomic_json(attempt_path, attempt)
    frames: dict[str, pd.DataFrame] = {}
    api_calls = 0
    try:
        for key in SOURCE_KEYS:
            landing = _revalidation_landing_path(
                project_root, key, target, completed_successor
            )
            calls_started = list(attempt["calls_started"])
            landing_files = dict(attempt["landing_files"])
            calls_started.append(key)
            landing_files[key] = str(landing.relative_to(project_root))
            attempt.update({
                "status": f"CALLING_{key.upper()}",
                "calls_started": calls_started,
                "landing_files": landing_files,
            })
            _atomic_json(attempt_path, attempt)
            frame, calls = _collect_exact(
                project_root, key, compact, landing_override=landing,
            )
            frames[key] = frame
            api_calls += calls
            calls_completed = list(attempt["calls_completed"])
            calls_completed.append(key)
            attempt.update({
                "status": f"COLLECTED_{key.upper()}",
                "calls_completed": calls_completed,
                "api_calls": api_calls,
            })
            _atomic_json(attempt_path, attempt)
        if api_calls > 2:
            raise DerivativesDailyLiveError("bounded call budget exceeded")
    except Exception as error:
        attempt.update({
            "status": "FAILED_NO_RETRY",
            "error_type": type(error).__name__,
            "api_calls": api_calls,
        })
        _atomic_json(attempt_path, attempt)
        raise

    rows = _complete_derivatives_transaction(
        project_root,
        target=target,
        frames=frames,
        api_calls=api_calls,
        status_path=attempt_path,
        status_record=attempt,
    )
    return DerivativesDailyLiveResult("AFFECTED_DATE_COMPLETE", target.isoformat(), api_calls, 0, ("source", "bridge", "basis", "pcr", "wall"), rows)


def run_derivatives_daily_catchup(
    project_root: Path,
    *,
    now: datetime | None = None,
    max_sessions: int = 3,
    max_source_calls: int = 6,
    max_elapsed_seconds: float = 600.0,
) -> DerivativesDailyCatchupResult:
    """Advance the daily chain oldest-first within one bounded occurrence.

    Each selected session still commits through ``run_derivatives_daily`` as
    its own two-source-call atomic transaction.  An exception on any date is
    deliberately propagated, so no later session can be attempted past the
    first unresolved date.
    """
    if not 1 <= max_sessions <= 3:
        raise ValueError("max_sessions must be between 1 and 3")
    if not 2 <= max_source_calls <= 6:
        raise ValueError("max_source_calls must be between 2 and 6")
    if not 0 < max_elapsed_seconds <= 600:
        raise ValueError("max_elapsed_seconds must be in (0, 600]")

    project_root = project_root.resolve()
    started = monotonic()
    completed_dates: list[str] = []
    processed_sessions = 0
    api_calls = 0
    last_result: DerivativesDailyLiveResult | None = None
    remaining = oldest_missing_eligible_session(project_root, now=now)
    if remaining is None:
        last_result = run_derivatives_daily(project_root, now=now)
        return DerivativesDailyCatchupResult(
            status="CURRENT",
            completed_dates=(),
            api_calls=last_result.api_calls,
            retry_count=last_result.retry_count,
            remaining_target=None,
            last_result=last_result,
        )

    while remaining is not None:
        limit_reached = (
            processed_sessions >= max_sessions
            or api_calls + 2 > max_source_calls
            or monotonic() - started >= max_elapsed_seconds
        )
        if limit_reached:
            break
        last_result = run_derivatives_daily(
            project_root, market_date=remaining, now=now,
        )
        processed_sessions += 1
        if last_result.api_calls > 2:
            raise DerivativesDailyLiveError(
                "single-date derivatives call budget exceeded"
            )
        api_calls += last_result.api_calls
        if api_calls > max_source_calls:
            raise DerivativesDailyLiveError(
                "catch-up derivatives call budget exceeded"
            )
        if last_result.status == "AFFECTED_DATE_COMPLETE":
            completed_dates.append(last_result.market_date)
        remaining = oldest_missing_eligible_session(project_root, now=now)

    return DerivativesDailyCatchupResult(
        status="CAUGHT_UP" if remaining is None else "PARTIAL_LIMIT_REACHED",
        completed_dates=tuple(completed_dates),
        api_calls=api_calls,
        retry_count=0,
        remaining_target=remaining.isoformat() if remaining is not None else None,
        last_result=last_result,
    )


def recover_derivatives_daily_from_retained(
    project_root: Path,
    *,
    market_date: date,
    now: datetime | None = None,
    reviewed_acl_retry: bool = False,
) -> DerivativesDailyLiveResult:
    """Resume an exact reviewed ACL failure without credentials or network."""
    from stock_data.contracts.derivatives_price_authority import (
        DATA_GO_KR_KOSPI200_DERIVATIVES_PRICE,
    )

    if not DATA_GO_KR_KOSPI200_DERIVATIVES_PRICE.live_validation_ready:
        raise DerivativesDailyLiveError("derivatives provider finality is not operationally approved")
    project_root = project_root.resolve()
    finalized = latest_finalized_session(project_root, now=now)
    if market_date > finalized:
        raise DerivativesDailyLiveError(
            "market date exceeds the latest conservatively finalized XKRX session"
        )
    if _replay_complete(project_root, market_date):
        return DerivativesDailyLiveResult(
            "NOOP_IDEMPOTENT", market_date.isoformat(), 0, 0,
            ("source", "bridge", "basis", "pcr", "wall"), {},
        )
    _require_next_source_session(project_root, market_date)
    completed_successor = _completed_successor_session(
        project_root, market_date, now=now,
    )
    attempt_path, attempt = _require_exact_failed_attempt(
        project_root, market_date, completed_successor,
    )
    prior_recovery: Mapping[str, object] | None = None
    if reviewed_acl_retry:
        prior_recovery_path, prior_recovery = _require_exact_failed_acl_recovery(
            project_root,
            market_date,
            completed_successor,
            attempt_path=attempt_path,
            attempt=attempt,
        )
        recovery_path = _recovery_retry_path(project_root, market_date)
    else:
        prior_recovery_path = None
        recovery_path = _recovery_path(project_root, market_date)
    if recovery_path.exists():
        raise DerivativesDailyLiveError(
            "retained derivatives recovery attempt forbids an unreviewed repeat"
        )
    recovery: dict[str, object] = {
        "version": 1,
        "dataset": "derivatives_price_daily_live",
        "market_date": market_date.isoformat(),
        "completed_successor_session": completed_successor.isoformat(),
        "status": "STARTED",
        "mode": (
            "RETAINED_LANDING_API_ZERO_ACL_RETRY"
            if reviewed_acl_retry
            else "RETAINED_LANDING_API_ZERO"
        ),
        "source_attempt": str(attempt_path.relative_to(project_root)),
        "source_attempt_status": attempt["status"],
        "source_api_calls": attempt["api_calls"],
        "api_calls": 0,
        "retry_count": 0,
        "landing_files": dict(attempt["landing_files"]),
        "landing_sha256": {},
    }
    if prior_recovery_path is not None and prior_recovery is not None:
        recovery.update({
            "recovery_attempt": 2,
            "source_recovery": str(prior_recovery_path.relative_to(project_root)),
            "source_recovery_status": prior_recovery["status"],
        })
    _atomic_json(recovery_path, recovery)
    frames: dict[str, pd.DataFrame] = {}
    try:
        for key in SOURCE_KEYS:
            frame, landing = _retained_exact(
                project_root, key, market_date, completed_successor,
            )
            frames[key] = frame
            hashes = dict(recovery["landing_sha256"])
            digest = hashlib.sha256(landing.read_bytes()).hexdigest()
            if (
                prior_recovery is not None
                and dict(prior_recovery["landing_sha256"])[key] != digest
            ):
                raise DerivativesDailyLiveError(
                    "retained Landing changed after failed recovery"
                )
            hashes[key] = digest
            recovery.update({
                "status": f"VALIDATED_RETAINED_{key.upper()}",
                "landing_sha256": hashes,
            })
            _atomic_json(recovery_path, recovery)
    except Exception as error:
        recovery.update({
            "status": "FAILED_NO_RETRY",
            "failure_phase": recovery["status"],
            "error_type": type(error).__name__,
            "error_message": str(error),
        })
        _atomic_json(recovery_path, recovery)
        raise
    rows = _complete_derivatives_transaction(
        project_root,
        target=market_date,
        frames=frames,
        api_calls=0,
        status_path=recovery_path,
        status_record=recovery,
    )
    return DerivativesDailyLiveResult(
        "AFFECTED_DATE_COMPLETE", market_date.isoformat(), 0, 0,
        ("source", "bridge", "basis", "pcr", "wall"), rows,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bounded atomic KOSPI200 derivatives daily operation")
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--market-date", type=date.fromisoformat)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--recover-retained", action="store_true")
    parser.add_argument("--retry-reviewed-acl-recovery", action="store_true")
    parser.add_argument("--validate-source", choices=SOURCE_KEYS)
    args = parser.parse_args(argv)
    if not args.live:
        print(json.dumps({"status": "LIVE_FLAG_REQUIRED", "api_calls": 0}))
        return 2
    if args.recover_retained:
        if args.validate_source:
            parser.error("--recover-retained cannot be combined with --validate-source")
        if args.market_date is None:
            parser.error("--recover-retained requires --market-date")
        result = recover_derivatives_daily_from_retained(
            args.project_root,
            market_date=args.market_date,
            reviewed_acl_retry=args.retry_reviewed_acl_recovery,
        )
        print(json.dumps(asdict(result), ensure_ascii=False, default=list))
        return 0
    if args.retry_reviewed_acl_recovery:
        parser.error(
            "--retry-reviewed-acl-recovery requires --recover-retained"
        )
    if args.validate_source:
        if args.market_date is None:
            parser.error("--validate-source requires --market-date")
        frame, calls = _collect_exact(
            args.project_root.resolve(), args.validate_source,
            args.market_date.strftime("%Y%m%d"),
        )
        print(json.dumps({
            "status": "SOURCE_VALIDATED", "source": args.validate_source,
            "market_date": args.market_date.isoformat(), "api_calls": calls,
            "rows": len(frame), "retry_count": 0,
        }))
        return 0
    result = run_derivatives_daily(args.project_root, market_date=args.market_date)
    print(json.dumps(asdict(result), ensure_ascii=False, default=list))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
