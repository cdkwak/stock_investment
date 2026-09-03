"""Landing-first manual OpenDART quarterly-fundamentals operation.

The live phase is explicitly bounded and cannot mutate Normalized data. The
separate promotion phase performs zero network calls, verifies its checkpoint
digest and pre-existing dataset fingerprints, and then replaces complete
dataset roots. Provider status 020 is terminal for the current provider day.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time as daytime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import time
from typing import Callable, Iterable, Mapping, Sequence
from uuid import uuid4
from zoneinfo import ZoneInfo

import pandas as pd
import pyarrow.parquet as pq
import requests

from stock_data.contracts.kr_fundamentals import (
    KR_CORP_CODE_MAP,
    KR_FUNDAMENTALS_QUARTERLY,
)
from stock_data.providers.opendart_fundamentals import (
    DOCUMENTED_DAILY_LIMIT,
    OpenDartDailyLimitError,
    OpenDartFundamentalsError,
    OpenDartPeriodEndError,
    REPORT_CODES,
    corp_code_request,
    financial_statement_request,
    normalize_quarter,
    parse_corp_code_zip,
    parse_financial_statement,
    report_period_end,
)
from stock_data.storage.contract_arrow import (
    dataframe_to_contract_table,
    restore_contract_dates,
)


RETRY_COUNT = 0
REQUEST_TIMEOUT_SECONDS = 20
REQUEST_SPACING_SECONDS = 0.2
DEFAULT_MAX_CALLS = 200
CORP_MAP_MAX_AGE = timedelta(days=7)
_RUN_ID = re.compile(r"\d{8}T\d{6}Z_[0-9a-f]{32}\Z")


class FundamentalsRefreshError(RuntimeError):
    pass


def read_api_key() -> str:
    """Read the credential without displaying or persisting it."""
    try:
        value = os.environ["OPENDART_API_KEY"]
    except KeyError:
        value = os.environ.get("OpenDART_API_KEY", "")
    if not isinstance(value, str) or len(value) != 40:
        raise FundamentalsRefreshError(
            "OpenDART API key must be a 40-character environment value"
        )
    return value


def load_watchlist_symbols(project_root: Path) -> tuple[str, ...]:
    path = project_root / "artifacts/local_user/watchlists.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise FundamentalsRefreshError("local watchlist file is missing") from error
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise FundamentalsRefreshError("local watchlist file cannot be read") from error
    symbols: set[str] = set()
    lists = payload.get("lists") if isinstance(payload, dict) else None
    if not isinstance(lists, list):
        raise FundamentalsRefreshError("local watchlist schema is invalid")
    for watchlist in lists:
        if not isinstance(watchlist, dict) or not isinstance(watchlist.get("items", []), list):
            raise FundamentalsRefreshError("local watchlist schema is invalid")
        for item in watchlist.get("items", []):
            if not isinstance(item, dict):
                raise FundamentalsRefreshError("local watchlist item is invalid")
            market = item.get("market")
            symbol = item.get("symbol")
            if market in {"KOSPI", "KOSDAQ"} and isinstance(symbol, str) and re.fullmatch(r"[0-9A-Z]{6}", symbol):
                symbols.add(symbol)
    if not symbols:
        raise FundamentalsRefreshError("local watchlists contain no Korean equity symbols")
    return tuple(sorted(symbols))


def load_universe_symbols(project_root: Path) -> tuple[str, ...]:
    roots = (
        project_root / "data/normalized/kr_equity_master",
        project_root / "data/normalized/kr_equity_canonical_universe_daily",
    )
    for root in roots:
        paths = sorted(root.rglob("*.parquet")) if root.exists() else []
        if not paths:
            continue
        frames = [pd.read_parquet(path, columns=["symbol"]) for path in paths]
        values = {
            str(value) for value in pd.concat(frames, ignore_index=True)["symbol"].dropna()
            if re.fullmatch(r"[0-9A-Z]{6}", str(value))
        }
        if values:
            return tuple(sorted(values))
    raise FundamentalsRefreshError("no retained listed-stock universe is available")


def prepare_collection(
    project_root: Path,
    *,
    symbols: Sequence[str],
    years: Sequence[int],
    max_calls: int = DEFAULT_MAX_CALLS,
    session: object | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    now: datetime | None = None,
) -> dict[str, object]:
    """Run the bounded live Landing phase and build reviewable candidates."""
    project_root = Path(project_root).resolve()
    clean_symbols = _validate_symbols(symbols)
    clean_years = _validate_years(years)
    if not 1 <= max_calls <= DOCUMENTED_DAILY_LIMIT:
        raise FundamentalsRefreshError("max_calls must be between 1 and 20000")
    key = read_api_key()
    captured_now = now or datetime.now(timezone.utc)
    if captured_now.tzinfo is None or captured_now.utcoffset() is None:
        raise FundamentalsRefreshError("operation clock must be timezone-aware")
    captured_now = captured_now.astimezone(timezone.utc)
    # An injected `now` also stamps retrieved_at so runs are reproducible in tests; live runs
    # keep per-request wall-clock stamps.
    clock: Callable[[], datetime] | None = (lambda: captured_now) if now is not None else None
    run_id = captured_now.strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex
    landing_root = project_root / "data/landing/opendart/kr_fundamentals_quarterly"
    run_dir = landing_root / run_id
    state_dir = project_root / "data/state/kr_fundamentals_quarterly" / run_id
    candidate_dir = project_root / "data/staging/kr_fundamentals_quarterly" / run_id
    ledger_path = run_dir / "call_ledger.jsonl"
    checkpoint_path = state_dir / "checkpoint.json"
    provider_day = captured_now.astimezone(ZoneInfo("Asia/Seoul")).date().isoformat()
    calls_before = _calls_recorded_for_provider_day(landing_root, provider_day)
    if _provider_day_hard_stopped(landing_root, provider_day):
        raise FundamentalsRefreshError(
            "OpenDART collection is hard-stopped for the current provider day"
        )
    if calls_before + max_calls > DOCUMENTED_DAILY_LIMIT:
        raise FundamentalsRefreshError(
            "bounded run plus locally recorded provider-day calls exceeds 20000"
        )
    for path in (run_dir, state_dir, candidate_dir):
        path.mkdir(parents=True, exist_ok=False)
    normalized_map_root = project_root / "data/normalized/kr_corp_code_map"
    normalized_fund_root = project_root / "data/normalized/kr_fundamentals_quarterly"
    map_state_path = project_root / "data/state/kr_corp_code_map.json"
    refresh_map = not _corp_map_is_fresh(map_state_path, normalized_map_root, captured_now)
    frozen_plan = [
        {"symbol": symbol, "bsns_year": year, "reprt_code": report}
        for symbol in clean_symbols for year in clean_years for report in REPORT_CODES
    ]
    manifest = {
        "version": 1,
        "run_id": run_id,
        "provider_day_kst": provider_day,
        "symbols": list(clean_symbols),
        "years": list(clean_years),
        "report_codes": list(REPORT_CODES),
        "corp_map_refresh": refresh_map,
        "max_http_calls": max_calls,
        "documented_daily_limit": DOCUMENTED_DAILY_LIMIT,
        "locally_recorded_calls_before_run": calls_before,
        "retry_count": RETRY_COUNT,
        "timeout_seconds": REQUEST_TIMEOUT_SECONDS,
        "spacing_seconds": REQUEST_SPACING_SECONDS,
        "normalized_writes": False,
    }
    _atomic_json(run_dir / "manifest.json", manifest)
    checkpoint: dict[str, object] = {
        **manifest,
        "status": "LANDING_IN_PROGRESS",
        "http_calls": 0,
        "calls_today": calls_before,
        "landing_captures": [],
        "completed_queries": [],
        "remaining_queries": len(frozen_plan),
        "frozen_plan": frozen_plan,
        "pre_map_fingerprint": _fingerprint(normalized_map_root),
        "pre_map_state_fingerprint": _fingerprint(map_state_path),
        "pre_fundamentals_fingerprint": _fingerprint(normalized_fund_root),
        "created_at_utc": captured_now.isoformat(),
    }
    _atomic_json(checkpoint_path, checkpoint)
    http = session or requests.Session()
    raw_by_query: dict[tuple[str, int, str, str], list[dict[str, object]]] = {}
    new_rows: list[dict[str, object]] = []
    dropped_rows_by_reason: Counter[str] = Counter()
    try:
        if refresh_map:
            body, retrieved = _capture_request(
                http, corp_code_request(), key=key, run_dir=run_dir,
                ledger_path=ledger_path, checkpoint=checkpoint,
                checkpoint_path=checkpoint_path, max_calls=max_calls,
                sleeper=sleeper, clock=clock,
            )
            map_rows = parse_corp_code_zip(body)
            map_frame = pd.DataFrame(map_rows, columns=KR_CORP_CODE_MAP.column_names)
            _validate_corp_map(map_frame)
            map_candidate = candidate_dir / KR_CORP_CODE_MAP.name
            _write_candidate(map_frame, map_candidate, KR_CORP_CODE_MAP)
            checkpoint["map_retrieved_at"] = retrieved
            map_state_candidate = candidate_dir / "kr_corp_code_map.state.json"
            _atomic_json(map_state_candidate, {
                "version": 1,
                "retrieved_at": retrieved,
                "dataset_fingerprint": _fingerprint(map_candidate),
            })
        else:
            map_frame = _read_dataset(normalized_map_root, KR_CORP_CODE_MAP)
            _validate_corp_map(map_frame)
        listed = map_frame.dropna(subset=["stock_code"])
        mapping = dict(zip(listed["stock_code"].astype(str), listed["corp_code"].astype(str), strict=False))
        missing = [symbol for symbol in clean_symbols if symbol not in mapping]
        if missing:
            raise FundamentalsRefreshError(
                f"exact corporation-code mapping is unavailable for {len(missing)} requested symbols"
            )
        for index, query in enumerate(frozen_plan):
            # Leave room for the documented CFS-first/OFS-fallback pair.
            if max_calls - int(checkpoint["http_calls"]) < 2:
                break
            symbol = str(query["symbol"])
            year = int(query["bsns_year"])
            report = str(query["reprt_code"])
            corp_code = mapping[symbol]
            chosen_scope: str | None = None
            chosen_rows: list[dict[str, object]] = []
            retrieved_at = ""
            for scope in ("CFS", "OFS"):
                body, retrieved_at = _capture_request(
                    http, financial_statement_request(corp_code, year, report, scope),
                    key=key, run_dir=run_dir, ledger_path=ledger_path,
                    checkpoint=checkpoint, checkpoint_path=checkpoint_path,
                    max_calls=max_calls, sleeper=sleeper, clock=clock,
                )
                classification, rows = parse_financial_statement(
                    body, expected_corp_code=corp_code, expected_year=year,
                    expected_report_code=report, requested_fs_div=scope,
                )
                if classification == "SUCCESS":
                    chosen_scope, chosen_rows = scope, rows
                    break
            completed = {
                **query,
                "corp_code": corp_code,
                "classification": "SUCCESS" if chosen_rows else "VALID_EMPTY",
                "fs_div": chosen_scope,
            }
            cast_completed = checkpoint["completed_queries"]
            assert isinstance(cast_completed, list)
            cast_completed.append(completed)
            checkpoint["remaining_queries"] = len(frozen_plan) - index - 1
            if chosen_rows and chosen_scope is not None:
                raw_by_query[(symbol, year, report, chosen_scope)] = chosen_rows
                q3 = raw_by_query.get((symbol, year, "11014", chosen_scope))
                try:
                    new_rows.append(normalize_quarter(
                        symbol=symbol, rows=chosen_rows, retrieved_at=retrieved_at, q3_rows=q3,
                    ))
                    completed["normalization"] = "NORMALIZED"
                except OpenDartPeriodEndError as error:
                    dropped_rows_by_reason[error.reason] += 1
                    completed["normalization"] = "DROPPED"
                    completed["drop_reason"] = error.reason
            _atomic_json(checkpoint_path, checkpoint)
        existing = _read_dataset_optional(normalized_fund_root, KR_FUNDAMENTALS_QUARTERLY)
        incoming = pd.DataFrame(new_rows, columns=KR_FUNDAMENTALS_QUARTERLY.column_names)
        if existing.empty:
            candidate_frame = incoming
        elif incoming.empty:
            candidate_frame = existing
        else:
            candidate_frame = pd.concat([existing, incoming], ignore_index=True)
        candidate_frame, retained_future_drops = _drop_future_period_end_rows(candidate_frame)
        if retained_future_drops:
            dropped_rows_by_reason["PERIOD_END_AFTER_RECEIPT_DATE"] += retained_future_drops
        if not candidate_frame.empty:
            candidate_frame = candidate_frame.drop_duplicates(
                list(KR_FUNDAMENTALS_QUARTERLY.primary_key), keep="first"
            ).sort_values(list(KR_FUNDAMENTALS_QUARTERLY.sort_key), kind="stable").reset_index(drop=True)
        _validate_fundamentals(candidate_frame)
        fund_candidate = candidate_dir / KR_FUNDAMENTALS_QUARTERLY.name
        _write_candidate(candidate_frame, fund_candidate, KR_FUNDAMENTALS_QUARTERLY)
        checkpoint.update({
            "status": "CANDIDATE_REVIEW_REQUIRED",
            "candidate_map": (
                (candidate_dir / KR_CORP_CODE_MAP.name).relative_to(project_root).as_posix()
                if refresh_map else None
            ),
            "candidate_fundamentals": fund_candidate.relative_to(project_root).as_posix(),
            "candidate_map_fingerprint": (
                _fingerprint(candidate_dir / KR_CORP_CODE_MAP.name) if refresh_map else None
            ),
            "candidate_map_state": (
                (candidate_dir / "kr_corp_code_map.state.json").relative_to(project_root).as_posix()
                if refresh_map else None
            ),
            "candidate_map_state_fingerprint": (
                _fingerprint(candidate_dir / "kr_corp_code_map.state.json") if refresh_map else None
            ),
            "candidate_fundamentals_fingerprint": _fingerprint(fund_candidate),
            "new_normalized_rows": len(new_rows),
            "dropped_normalized_rows": sum(dropped_rows_by_reason.values()),
            "dropped_rows_by_reason": dict(sorted(dropped_rows_by_reason.items())),
            "completed_at_utc": datetime.now(timezone.utc).isoformat(),
        })
        checkpoint["approval_digest"] = approval_digest(checkpoint)
        _atomic_json(checkpoint_path, checkpoint)
        _append_ledger(ledger_path, {
            "event": "RUN_COMPLETED", "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
            "provider_day_kst": provider_day, "http_calls": checkpoint["http_calls"],
            "calls_today": checkpoint["calls_today"], "documented_daily_limit": DOCUMENTED_DAILY_LIMIT,
            "dropped_normalized_rows": checkpoint["dropped_normalized_rows"],
            "dropped_rows_by_reason": checkpoint["dropped_rows_by_reason"],
        }, key)
        return {
            "status": checkpoint["status"],
            "checkpoint": str(checkpoint_path),
            "approval_digest": checkpoint["approval_digest"],
            "http_calls": checkpoint["http_calls"],
            "calls_today": checkpoint["calls_today"],
            "new_normalized_rows": len(new_rows),
            "dropped_normalized_rows": checkpoint["dropped_normalized_rows"],
            "dropped_rows_by_reason": checkpoint["dropped_rows_by_reason"],
            "remaining_queries": checkpoint["remaining_queries"],
        }
    except Exception as error:
        safe = str(error).replace(key, "[REDACTED]")
        checkpoint.update({
            "status": (
                "HARD_STOP_DAILY_LIMIT" if isinstance(error, OpenDartDailyLimitError)
                else "STOPPED"
            ),
            "error_type": type(error).__name__,
            "error": safe,
            "stopped_at_utc": datetime.now(timezone.utc).isoformat(),
        })
        _atomic_json(checkpoint_path, checkpoint)
        _append_ledger(ledger_path, {
            "event": "RUN_STOPPED", "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
            "provider_day_kst": provider_day, "error_type": type(error).__name__,
            "error": safe, "calls_today": checkpoint["calls_today"],
            "documented_daily_limit": DOCUMENTED_DAILY_LIMIT,
        }, key)
        raise


def approval_digest(checkpoint: Mapping[str, object]) -> str:
    keys = (
        "run_id", "provider_day_kst", "symbols", "years", "report_codes",
        "corp_map_refresh", "max_http_calls", "documented_daily_limit",
        "locally_recorded_calls_before_run", "retry_count", "timeout_seconds",
        "spacing_seconds", "http_calls", "calls_today", "landing_captures",
        "completed_queries", "remaining_queries", "frozen_plan",
        "pre_map_fingerprint", "pre_map_state_fingerprint",
        "pre_fundamentals_fingerprint", "candidate_map",
        "candidate_fundamentals", "candidate_map_fingerprint", "candidate_map_state",
        "candidate_map_state_fingerprint",
        "candidate_fundamentals_fingerprint", "new_normalized_rows",
        "dropped_normalized_rows", "dropped_rows_by_reason",
    )
    payload = {key: checkpoint.get(key) for key in keys}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def promote_checkpoint(
    project_root: Path, checkpoint_path: Path, *, expected_approval_digest: str,
) -> dict[str, object]:
    """Perform zero-network, compare-and-swap promotion of reviewed candidates."""
    project_root = Path(project_root).resolve()
    checkpoint_path = Path(checkpoint_path).resolve()
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    run_id = checkpoint.get("run_id")
    expected_path = project_root / "data/state/kr_fundamentals_quarterly" / str(run_id) / "checkpoint.json"
    if not isinstance(run_id, str) or not _RUN_ID.fullmatch(run_id) or checkpoint_path != expected_path:
        raise FundamentalsRefreshError("checkpoint path does not match its run identity")
    if checkpoint.get("status") == "PROMOTED":
        return checkpoint
    if checkpoint.get("status") != "CANDIDATE_REVIEW_REQUIRED":
        raise FundamentalsRefreshError("checkpoint is not review-ready")
    actual_digest = approval_digest(checkpoint)
    if checkpoint.get("approval_digest") != actual_digest or expected_approval_digest != actual_digest:
        raise FundamentalsRefreshError("checkpoint approval digest differs")
    map_target = project_root / "data/normalized/kr_corp_code_map"
    fund_target = project_root / "data/normalized/kr_fundamentals_quarterly"
    if (_fingerprint(map_target) != checkpoint["pre_map_fingerprint"]
            or _fingerprint(project_root / "data/state/kr_corp_code_map.json")
            != checkpoint["pre_map_state_fingerprint"]
            or _fingerprint(fund_target) != checkpoint["pre_fundamentals_fingerprint"]):
        raise FundamentalsRefreshError("Normalized data changed after candidate creation")
    replacements: list[tuple[Path, Path]] = []
    if checkpoint.get("candidate_map"):
        source = project_root / str(checkpoint["candidate_map"])
        expected_source = project_root / "data/staging/kr_fundamentals_quarterly" / run_id / KR_CORP_CODE_MAP.name
        if source != expected_source:
            raise FundamentalsRefreshError("corporation-map candidate path differs")
        if _fingerprint(source) != checkpoint["candidate_map_fingerprint"]:
            raise FundamentalsRefreshError("corporation-map candidate fingerprint differs")
        replacements.append((source, map_target))
        state_source = project_root / str(checkpoint["candidate_map_state"])
        expected_state_source = expected_source.parent / "kr_corp_code_map.state.json"
        if state_source != expected_state_source:
            raise FundamentalsRefreshError("corporation-map state candidate path differs")
        if _fingerprint(state_source) != checkpoint["candidate_map_state_fingerprint"]:
            raise FundamentalsRefreshError("corporation-map state candidate fingerprint differs")
        replacements.append((state_source, project_root / "data/state/kr_corp_code_map.json"))
    fund_source = project_root / str(checkpoint["candidate_fundamentals"])
    expected_fund_source = (
        project_root / "data/staging/kr_fundamentals_quarterly" / run_id
        / KR_FUNDAMENTALS_QUARTERLY.name
    )
    if fund_source != expected_fund_source:
        raise FundamentalsRefreshError("fundamentals candidate path differs")
    if _fingerprint(fund_source) != checkpoint["candidate_fundamentals_fingerprint"]:
        raise FundamentalsRefreshError("fundamentals candidate fingerprint differs")
    replacements.append((fund_source, fund_target))
    promoted = dict(checkpoint)
    promoted.update({
        "status": "PROMOTED", "normalized_mutation": True,
        "promoted_at_utc": datetime.now(timezone.utc).isoformat(),
    })
    # Final fingerprints are filled after installation; checkpoint publication
    # participates in rollback through the finalize callback.
    def finalize() -> None:
        promoted["post_map_fingerprint"] = _fingerprint(map_target)
        promoted["post_fundamentals_fingerprint"] = _fingerprint(fund_target)
        _atomic_json(checkpoint_path, promoted)

    _replace_roots(replacements, finalize=finalize)
    return promoted


def repair_period_end(project_root: Path) -> dict[str, object]:
    """Repair unsafe retained period ends from immutable Landing responses.

    Only rows whose period end is later than the filing receipt are candidates.
    A row is corrected when retained Landing data supplies one unambiguous safe
    ``thstrm_dt`` end date; otherwise it is removed. The complete dataset root
    is replaced atomically and no credential or network access is used.
    """
    project_root = Path(project_root).resolve()
    target = project_root / "data/normalized/kr_fundamentals_quarterly"
    frame = _read_dataset_optional(target, KR_FUNDAMENTALS_QUARTERLY)
    if frame.empty:
        return {
            "status": "NO_CHANGES", "rows_before": 0, "rows_after": 0,
            "corrected_rows": 0, "removed_rows": 0,
        }
    unsafe = _future_period_end_mask(frame)
    if not unsafe.any():
        _validate_fundamentals(frame)
        return {
            "status": "NO_CHANGES", "rows_before": len(frame), "rows_after": len(frame),
            "corrected_rows": 0, "removed_rows": 0,
        }

    landing = _landing_period_ends(
        project_root / "data/landing/opendart/kr_fundamentals_quarterly"
    )
    repaired = frame.copy()
    remove_indices: list[object] = []
    corrected = 0
    for index, row in repaired.loc[unsafe].iterrows():
        key = (
            str(row["rcept_no"]), str(row["corp_code"]),
            int(row["bsns_year"]), str(row["reprt_code"]),
        )
        receipt_date = datetime.strptime(key[0][:8], "%Y%m%d").date()
        candidates = {
            value for value in landing.get(key, set()) if value <= receipt_date
        }
        if len(candidates) == 1:
            repaired.at[index, "period_end"] = next(iter(candidates))
            corrected += 1
        else:
            remove_indices.append(index)
    if remove_indices:
        repaired = repaired.drop(index=remove_indices)
    repaired = repaired.sort_values(
        list(KR_FUNDAMENTALS_QUARTERLY.sort_key), kind="stable",
    ).reset_index(drop=True)
    _validate_fundamentals(repaired)

    candidate = target.with_name(f".{target.name}.repair.{uuid4().hex}")
    try:
        _write_candidate(repaired, candidate, KR_FUNDAMENTALS_QUARTERLY)
        _replace_roots(((candidate, target),))
    finally:
        if candidate.exists():
            shutil.rmtree(candidate)
    return {
        "status": "REPAIRED",
        "rows_before": len(frame),
        "rows_after": len(repaired),
        "corrected_rows": corrected,
        "removed_rows": len(remove_indices),
    }


def latest_fundamental_rows(frame: pd.DataFrame, as_of: date | datetime) -> pd.DataFrame:
    """Derive latest filing revisions and CFS-first scope at read time."""
    columns = list(KR_FUNDAMENTALS_QUARTERLY.column_names)
    if frame.empty:
        return pd.DataFrame(columns=columns)
    _validate_fundamentals(frame)
    cutoff, cutoff_date = _as_of_cutoff(as_of)
    work = frame.copy()
    work["retrieved_at"] = pd.to_datetime(work["retrieved_at"], utc=True, errors="raise")
    work["period_end"] = pd.to_datetime(work["period_end"], errors="raise").dt.date
    work = work[(work["retrieved_at"] <= cutoff) & (work["period_end"] <= cutoff_date)]
    if work.empty:
        return work[columns].reset_index(drop=True)
    revision_key = ["symbol", "bsns_year", "reprt_code", "fs_div"]
    work = work.sort_values(revision_key + ["retrieved_at", "rcept_no"], kind="stable")
    work = work.drop_duplicates(revision_key, keep="last")
    work["_scope_rank"] = work["fs_div"].map({"CFS": 0, "OFS": 1})
    period_key = ["symbol", "bsns_year", "reprt_code"]
    work = work.sort_values(period_key + ["_scope_rank"], kind="stable")
    work = work.drop_duplicates(period_key, keep="first").drop(columns="_scope_rank")
    return work[columns].sort_values(["symbol", "period_end"], kind="stable").reset_index(drop=True)


def fundamental_health(project_root: Path, as_of: date | datetime) -> pd.DataFrame:
    """Return the scanner financial-health projection from four discrete quarters."""
    root = Path(project_root) / "data/normalized/kr_fundamentals_quarterly"
    frame = _read_dataset_optional(root, KR_FUNDAMENTALS_QUARTERLY)
    latest = latest_fundamental_rows(frame, as_of)
    output_columns = [
        "symbol", "debt_ratio_pct", "op_income_positive_4q",
        "net_income_positive_4q", "revenue_trend", "fundamentals_as_of",
    ]
    results: list[dict[str, object]] = []
    for symbol, group in latest.groupby("symbol", sort=True):
        quarters = group.sort_values("period_end", kind="stable").tail(4)
        complete_four = len(quarters) == 4
        same_scope = complete_four and quarters["fs_div"].nunique(dropna=False) == 1
        op_values = quarters["operating_income"]
        net_values = quarters["net_income"]
        revenue_values = quarters["revenue"]
        results.append({
            "symbol": symbol,
            "debt_ratio_pct": quarters.iloc[-1]["debt_ratio_pct"],
            "op_income_positive_4q": (
                bool((op_values > 0).all()) if same_scope and op_values.notna().all() else None
            ),
            "net_income_positive_4q": (
                bool((net_values > 0).all()) if same_scope and net_values.notna().all() else None
            ),
            "revenue_trend": (
                _revenue_trend([int(value) for value in revenue_values])
                if same_scope and revenue_values.notna().all() else "UNAVAILABLE"
            ),
            "fundamentals_as_of": pd.to_datetime(quarters["retrieved_at"], utc=True).max(),
        })
    return pd.DataFrame(results, columns=output_columns)


def _capture_request(
    session: object,
    request: object,
    *,
    key: str,
    run_dir: Path,
    ledger_path: Path,
    checkpoint: dict[str, object],
    checkpoint_path: Path,
    max_calls: int,
    sleeper: Callable[[float], None],
    clock: Callable[[], datetime] | None = None,
) -> tuple[bytes, str]:
    current = int(checkpoint["http_calls"])
    if current >= max_calls:
        raise FundamentalsRefreshError("run HTTP call budget exhausted")
    if current:
        sleeper(REQUEST_SPACING_SECONDS)
    sequence = current + 1
    checkpoint["http_calls"] = sequence
    checkpoint["calls_today"] = int(checkpoint["calls_today"]) + 1
    _atomic_json(checkpoint_path, checkpoint)
    params = {"crtfc_key": key, **dict(request.public_parameters)}
    _append_ledger(ledger_path, {
        "event": "REQUEST_STARTED", "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "provider_day_kst": checkpoint["provider_day_kst"], "sequence": sequence,
        "operation": request.operation, "url": request.endpoint,
        "public_parameters": dict(request.public_parameters),
        "calls_today": checkpoint["calls_today"],
        "documented_daily_limit": DOCUMENTED_DAILY_LIMIT,
    }, key)
    started = time.monotonic()
    response = session.get(
        request.endpoint, params=params, timeout=REQUEST_TIMEOUT_SECONDS,
        allow_redirects=False,
    )
    retrieved_at = (clock() if clock is not None else datetime.now(timezone.utc)).isoformat()
    body = bytes(response.content)
    if key.encode("utf-8") in body:
        raise FundamentalsRefreshError("credential echo detected; Landing write refused")
    suffix = ".zip" if request.operation == "corp_code_map" else ".json"
    target = run_dir / f"response_{sequence:04d}_{request.operation}{suffix}"
    _atomic_new(target, body)
    capture = {
        "sequence": sequence,
        "operation": request.operation,
        "body_file": target.name,
        "status_code": int(response.status_code),
        "response_bytes": len(body),
        "response_sha256": hashlib.sha256(body).hexdigest(),
        "retrieved_at": retrieved_at,
    }
    casts = checkpoint["landing_captures"]
    assert isinstance(casts, list)
    casts.append(capture)
    _atomic_json(checkpoint_path, checkpoint)
    _append_ledger(ledger_path, {
        "event": "HTTP_RESPONSE", "recorded_at_utc": retrieved_at,
        "provider_day_kst": checkpoint["provider_day_kst"],
        **capture,
        "elapsed_ms": round((time.monotonic() - started) * 1000, 3),
        "calls_today": checkpoint["calls_today"],
        "documented_daily_limit": DOCUMENTED_DAILY_LIMIT,
    }, key)
    if int(response.status_code) != 200:
        raise FundamentalsRefreshError(f"OpenDART HTTP status is {response.status_code}")
    return body, retrieved_at


def _validate_symbols(symbols: Sequence[str]) -> tuple[str, ...]:
    values = tuple(dict.fromkeys(str(value) for value in symbols))
    if not values or any(not re.fullmatch(r"[0-9A-Z]{6}", value) for value in values):
        raise FundamentalsRefreshError("symbols must be non-empty six-digit Korean stock codes")
    return values


def _validate_years(years: Sequence[int]) -> tuple[int, ...]:
    values = tuple(dict.fromkeys(int(value) for value in years))
    if not values or any(value < 2015 or value > 9999 for value in values):
        raise FundamentalsRefreshError("years must be non-empty and no earlier than 2015")
    return values


def _validate_corp_map(frame: pd.DataFrame) -> None:
    if tuple(frame.columns) != KR_CORP_CODE_MAP.column_names or frame.empty:
        raise FundamentalsRefreshError("corporation-map columns or row count differ from contract")
    if frame["corp_code"].duplicated().any():
        raise FundamentalsRefreshError("corporation-map primary key is duplicated")
    if not frame["corp_code"].astype(str).str.fullmatch(r"\d{8}").all():
        raise FundamentalsRefreshError("corporation-map corp_code is invalid")
    present = frame["stock_code"].dropna().astype(str)
    if not present.str.fullmatch(r"[0-9A-Z]{6}").all() or present.duplicated().any():
        raise FundamentalsRefreshError("corporation-map listed stock_code is invalid or duplicated")
    if pd.to_datetime(frame["modify_date"], errors="coerce").isna().any():
        raise FundamentalsRefreshError("corporation-map modify_date is invalid")


def _validate_fundamentals(frame: pd.DataFrame) -> None:
    if tuple(frame.columns) != KR_FUNDAMENTALS_QUARTERLY.column_names:
        raise FundamentalsRefreshError("fundamentals columns differ from contract")
    if frame.empty:
        return
    if frame.duplicated(list(KR_FUNDAMENTALS_QUARTERLY.primary_key)).any():
        raise FundamentalsRefreshError("fundamentals vintage primary key is duplicated")
    if not frame["symbol"].astype(str).str.fullmatch(r"[0-9A-Z]{6}").all():
        raise FundamentalsRefreshError("fundamentals symbol is invalid")
    if not frame["corp_code"].astype(str).str.fullmatch(r"\d{8}").all():
        raise FundamentalsRefreshError("fundamentals corp_code is invalid")
    if not frame["reprt_code"].isin(REPORT_CODES).all() or not frame["fs_div"].isin({"CFS", "OFS"}).all():
        raise FundamentalsRefreshError("fundamentals report/scope code is invalid")
    if not frame["rcept_no"].astype(str).str.fullmatch(r"\d{14}").all():
        raise FundamentalsRefreshError("fundamentals receipt is invalid")
    retrieved = pd.to_datetime(frame["retrieved_at"], utc=True, errors="coerce")
    if retrieved.isna().any():
        raise FundamentalsRefreshError("fundamentals retrieved_at is invalid")
    actual_periods = pd.to_datetime(frame["period_end"], errors="coerce")
    if actual_periods.isna().any():
        raise FundamentalsRefreshError("fundamentals period_end is invalid")
    receipt_dates = pd.to_datetime(
        frame["rcept_no"].astype(str).str[:8], format="%Y%m%d", errors="coerce",
    )
    if receipt_dates.isna().any():
        raise FundamentalsRefreshError("fundamentals receipt date is invalid")
    if _future_period_end_mask(frame).any():
        raise FundamentalsRefreshError("fundamentals period_end is later than receipt date")
    if not frame["source_terms_ref"].eq("https://opendart.fss.or.kr/intro/terms.do").all():
        raise FundamentalsRefreshError("fundamentals source terms reference differs")
    equity = pd.to_numeric(frame["total_equity"], errors="coerce")
    liabilities = pd.to_numeric(frame["total_liabilities"], errors="coerce")
    expected = liabilities / equity * 100.0
    expected[(equity <= 0) | equity.isna() | liabilities.isna()] = float("nan")
    actual = pd.to_numeric(frame["debt_ratio_pct"], errors="coerce")
    if not ((actual.isna() & expected.isna()) | (actual.sub(expected).abs() <= 1e-9)).all():
        raise FundamentalsRefreshError("fundamentals debt ratio differs from inputs")


def _future_period_end_mask(frame: pd.DataFrame) -> pd.Series:
    periods = pd.to_datetime(frame["period_end"], errors="coerce")
    receipts = pd.to_datetime(
        frame["rcept_no"].astype(str).str[:8], format="%Y%m%d", errors="coerce",
    )
    return periods.notna() & receipts.notna() & (periods > receipts)


def _drop_future_period_end_rows(frame: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    if frame.empty:
        return frame, 0
    mask = _future_period_end_mask(frame)
    return frame.loc[~mask].reset_index(drop=True), int(mask.sum())


def _landing_period_ends(
    landing_root: Path,
) -> dict[tuple[str, str, int, str], set[date]]:
    results: dict[tuple[str, str, int, str], set[date]] = {}
    if not landing_root.exists():
        return results
    for path in sorted(landing_root.glob("*/response_*_financial_statement.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        items = payload.get("list") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            continue
        grouped: dict[tuple[str, str, int, str], list[Mapping[str, object]]] = {}
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                key = (
                    str(item["rcept_no"]), str(item["corp_code"]),
                    int(item["bsns_year"]), str(item["reprt_code"]),
                )
            except (KeyError, TypeError, ValueError):
                continue
            grouped.setdefault(key, []).append(item)
        for key, rows in grouped.items():
            try:
                value = report_period_end(
                    rows, bsns_year=key[2], reprt_code=key[3],
                )
            except OpenDartFundamentalsError:
                continue
            results.setdefault(key, set()).add(value)
    return results


def _read_dataset(root: Path, contract: object) -> pd.DataFrame:
    paths = sorted(root.rglob("data.parquet"))
    if not paths:
        raise FileNotFoundError(root)
    frame = pd.concat([pd.read_parquet(path) for path in paths], ignore_index=True)
    return restore_contract_dates(frame, contract)[list(contract.column_names)]


def _read_dataset_optional(root: Path, contract: object) -> pd.DataFrame:
    try:
        return _read_dataset(root, contract)
    except FileNotFoundError:
        return pd.DataFrame(columns=contract.column_names)


def _write_candidate(frame: pd.DataFrame, root: Path, contract: object) -> None:
    if root.exists():
        raise FundamentalsRefreshError("candidate root already exists")
    root.mkdir(parents=True)
    target = root / "data.parquet"
    pq.write_table(dataframe_to_contract_table(frame, contract), target)
    verified = _read_dataset(root, contract)
    if len(verified) != len(frame):
        raise FundamentalsRefreshError("candidate row-count readback differs")


def _corp_map_is_fresh(state_path: Path, root: Path, now: datetime) -> bool:
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        retrieved = datetime.fromisoformat(str(state["retrieved_at"]).replace("Z", "+00:00"))
    except (FileNotFoundError, OSError, ValueError, KeyError, json.JSONDecodeError):
        return False
    if retrieved.tzinfo is None or retrieved.utcoffset() is None:
        return False
    age = now.astimezone(timezone.utc) - retrieved.astimezone(timezone.utc)
    return timedelta(0) <= age < CORP_MAP_MAX_AGE and _fingerprint(root) == state.get("dataset_fingerprint")


def _calls_recorded_for_provider_day(landing_root: Path, provider_day: str) -> int:
    count = 0
    if not landing_root.exists():
        return 0
    for path in landing_root.glob("*/call_ledger.jsonl"):
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                record = json.loads(line)
                if record.get("event") == "REQUEST_STARTED" and record.get("provider_day_kst") == provider_day:
                    count += 1
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise FundamentalsRefreshError("existing OpenDART call ledger is unreadable") from error
    return count


def _provider_day_hard_stopped(landing_root: Path, provider_day: str) -> bool:
    if not landing_root.exists():
        return False
    for path in landing_root.glob("*/call_ledger.jsonl"):
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                record = json.loads(line)
                if (
                    record.get("event") == "RUN_STOPPED"
                    and record.get("provider_day_kst") == provider_day
                    and record.get("error_type") == "OpenDartDailyLimitError"
                ):
                    return True
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise FundamentalsRefreshError("existing OpenDART call ledger is unreadable") from error
    return False


def _revenue_trend(values: Sequence[int]) -> str:
    if len(values) != 4:
        return "UNAVAILABLE"
    increases = [right > left for left, right in zip(values, values[1:], strict=False)]
    decreases = [right < left for left, right in zip(values, values[1:], strict=False)]
    if not any(increases) and not any(decreases):
        return "FLAT"
    if not any(decreases):
        return "INCREASING"
    if not any(increases):
        return "DECLINING"
    return "MIXED"


def _as_of_cutoff(value: date | datetime) -> tuple[pd.Timestamp, date]:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise FundamentalsRefreshError("datetime as_of must be timezone-aware")
        return pd.Timestamp(value).tz_convert("UTC"), value.astimezone(ZoneInfo("Asia/Seoul")).date()
    if not isinstance(value, date):
        raise FundamentalsRefreshError("as_of must be a date or aware datetime")
    end_kst = datetime.combine(value, daytime.max, tzinfo=ZoneInfo("Asia/Seoul"))
    return pd.Timestamp(end_kst.astimezone(timezone.utc)), value


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_bytes(
            (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_new(path: Path, body: bytes) -> None:
    if path.exists():
        raise FundamentalsRefreshError("refusing to overwrite Landing response")
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _append_ledger(path: Path, payload: object, key: str) -> None:
    encoded = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    if key.encode("utf-8") in encoded:
        raise FundamentalsRefreshError("credential detected in ledger record")
    descriptor = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, encoded)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fingerprint(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"exists": False}
    if path.is_file():
        body = path.read_bytes()
        return {
            "exists": True, "kind": "file", "bytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
        }
    digest = hashlib.sha256()
    files = []
    for child in sorted(item for item in path.rglob("*") if item.is_file()):
        body = child.read_bytes()
        relative = child.relative_to(path).as_posix()
        value = hashlib.sha256(body).hexdigest()
        files.append({"path": relative, "bytes": len(body), "sha256": value})
        digest.update(relative.encode("utf-8") + b"\0" + value.encode("ascii") + b"\n")
    return {"exists": True, "kind": "directory", "files": files, "sha256": digest.hexdigest()}


def _replace_roots(
    replacements: Iterable[tuple[Path, Path]], *, finalize: Callable[[], None] | None = None,
) -> None:
    token = uuid4().hex
    installed: list[tuple[Path, Path | None]] = []
    try:
        for source, target in replacements:
            target.parent.mkdir(parents=True, exist_ok=True)
            backup = target.with_name(f".{target.name}.{token}.backup") if target.exists() else None
            if backup is not None:
                target.replace(backup)
            try:
                source.replace(target)
            except BaseException:
                if backup is not None and backup.exists():
                    backup.replace(target)
                raise
            installed.append((target, backup))
        if finalize is not None:
            finalize()
    except BaseException:
        for target, backup in reversed(installed):
            if target.exists():
                shutil.rmtree(target) if target.is_dir() else target.unlink()
            if backup is not None and backup.exists():
                backup.replace(target)
        raise
    else:
        for _, backup in installed:
            if backup is not None:
                shutil.rmtree(backup) if backup.is_dir() else backup.unlink()


__all__ = [
    "DEFAULT_MAX_CALLS", "FundamentalsRefreshError", "RETRY_COUNT",
    "REQUEST_SPACING_SECONDS", "REQUEST_TIMEOUT_SECONDS", "approval_digest",
    "fundamental_health", "latest_fundamental_rows", "load_universe_symbols",
    "load_watchlist_symbols", "prepare_collection", "promote_checkpoint",
    "read_api_key", "repair_period_end",
]
