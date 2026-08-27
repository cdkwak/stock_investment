"""Offline boundary for a future bounded Toss short-selling watchlist run.

The module performs no network or persistence.  It fixes the only reviewed
symbols, call budget, exact-date validation, replay gate, and official-overlap
evidence shape that a later authorized runner must preserve.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any, Callable, Mapping

import pandas as pd

from stock_data.contracts.toss_short_watchlist import (
    TOSS_EQUITY_SHORT_WATCHLIST_DAILY,
    TOSS_SHORT_SOURCE_SCOPE,
    TOSS_SHORT_WATCHLIST,
    TOSS_SHORT_WATCHLIST_VERSION,
)
from stock_data.contracts.tossinvest_historical import KR_EQUITY_SHORT_SELLING_DAILY
from stock_data.pipelines.tossinvest_historical import _atomic_json, _extract, _rate_payload
from stock_data.providers.tossinvest import TossInvestClient, normalize_short_selling
from stock_data.providers.tossinvest.historical import SHORT_SELLING_OPERATION
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic
from stock_data.validation.tossinvest_historical import validate_toss_historical


KRX_NXT_COMBINED_START = date(2025, 3, 4)
RETAINED_FIXTURE_LATEST = date(2026, 8, 10)
RETAINED_FIXTURE_UPDATED_AT_KST = "2026-08-10T18:14:05+09:00"
RETAINED_FIXTURE_CAPTURED_AT_KST = "2026-08-11T01:25:01.522438+09:00"


@dataclass(frozen=True)
class TossShortRequest:
    symbol: str
    market: str
    endpoint: str
    params: Mapping[str, Any]


@dataclass(frozen=True)
class TossShortCallBudget:
    oauth_calls_max: int = 1
    market_calls_max: int = len(TOSS_SHORT_WATCHLIST)
    calls_per_symbol: int = 1
    retries: int = 0


CALL_BUDGET = TossShortCallBudget()
STATE_NAME = "toss_equity_short_watchlist_daily.json"
JOURNAL_NAME = "toss_equity_short_watchlist_daily_journal.json"


def request_plan(target_date: str) -> tuple[TossShortRequest, ...]:
    """Return the immutable two-symbol, one-call-each request plan."""
    parsed = _iso_date(target_date)
    return tuple(
        TossShortRequest(
            symbol=symbol,
            market=market,
            endpoint=f"/api/v1/stocks/{symbol}/short-selling",
            params={"count": 1, "until": parsed.isoformat()},
        )
        for symbol, _name, market in TOSS_SHORT_WATCHLIST
    )


def stage_exact_watchlist(
    frames_by_symbol: Mapping[str, pd.DataFrame], *, target_date: str
) -> pd.DataFrame:
    """Validate all fixed symbols and produce one all-or-nothing staged frame."""
    expected_date = _iso_date(target_date).isoformat()
    expected_symbols = {item[0] for item in TOSS_SHORT_WATCHLIST}
    if set(frames_by_symbol) != expected_symbols:
        raise ValueError("Toss short watchlist must contain exactly the fixed symbols")

    staged: list[pd.DataFrame] = []
    members = {symbol: market for symbol, _name, market in TOSS_SHORT_WATCHLIST}
    for symbol in sorted(expected_symbols):
        source = frames_by_symbol[symbol].copy()
        validate_toss_historical(source, KR_EQUITY_SHORT_SELLING_DAILY)
        if len(source) != 1:
            raise ValueError("each Toss short watchlist symbol must have one exact-date row")
        actual_date = pd.Timestamp(source.iloc[0]["date"]).date().isoformat()
        if actual_date != expected_date or str(source.iloc[0]["source_date"]) != expected_date:
            raise ValueError("Toss short watchlist source date differs from target date")
        if str(source.iloc[0]["symbol"]) != symbol:
            raise ValueError("Toss short watchlist symbol differs from request target")
        if source.iloc[0]["source_operation"] != SHORT_SELLING_OPERATION:
            raise ValueError("unexpected Toss short-selling source operation")
        if pd.isna(source.iloc[0]["updated_at"]):
            raise ValueError("Toss short watchlist requires provider updatedAt")
        source.insert(1, "market", members[symbol])
        insert_at = source.columns.get_loc("source")
        source.insert(insert_at, "source_scope", TOSS_SHORT_SOURCE_SCOPE)
        source.insert(insert_at + 1, "watchlist_version", TOSS_SHORT_WATCHLIST_VERSION)
        staged.append(source)

    result = pd.concat(staged, ignore_index=True)
    result = result[list(TOSS_EQUITY_SHORT_WATCHLIST_DAILY.column_names)]
    result = result.sort_values(
        list(TOSS_EQUITY_SHORT_WATCHLIST_DAILY.sort_key), kind="stable"
    ).reset_index(drop=True)
    validate_staged_watchlist(result, target_date=expected_date)
    return result


def validate_staged_watchlist(frame: pd.DataFrame, *, target_date: str) -> None:
    expected_date = _iso_date(target_date).isoformat()
    contract = TOSS_EQUITY_SHORT_WATCHLIST_DAILY
    if list(frame.columns) != list(contract.column_names):
        raise ValueError("Toss short watchlist schema differs from its contract")
    if len(frame) != len(TOSS_SHORT_WATCHLIST):
        raise ValueError("Toss short watchlist must contain every fixed member once")
    if frame.duplicated(list(contract.primary_key)).any():
        raise ValueError("Toss short watchlist primary key is duplicated")
    expected = {(market, symbol) for symbol, _name, market in TOSS_SHORT_WATCHLIST}
    actual = set(zip(frame["market"].astype(str), frame["symbol"].astype(str)))
    if actual != expected:
        raise ValueError("Toss short watchlist membership differs from the fixed contract")
    dates = pd.to_datetime(frame["date"], errors="coerce").dt.date.astype(str)
    if dates.isna().any() or not dates.eq(expected_date).all():
        raise ValueError("Toss short watchlist contains a non-target date")
    if not frame["source_date"].astype(str).eq(expected_date).all():
        raise ValueError("Toss short watchlist source date differs from target date")
    if not frame["source_scope"].eq(TOSS_SHORT_SOURCE_SCOPE).all():
        raise ValueError("Toss short watchlist source scope is invalid")
    if not frame["watchlist_version"].eq(TOSS_SHORT_WATCHLIST_VERSION).all():
        raise ValueError("Toss short watchlist version is invalid")
    if not frame["source"].eq("tossinvest_open_api").all():
        raise ValueError("Toss short watchlist provider identity is invalid")
    if not frame["source_operation"].eq(SHORT_SELLING_OPERATION).all():
        raise ValueError("Toss short watchlist operation identity is invalid")
    for name in ("short_selling_volume", "short_selling_amount"):
        values = pd.to_numeric(frame[name], errors="coerce")
        if values.isna().any() or values.lt(0).any():
            raise ValueError(f"Toss short watchlist {name} is invalid")
    updated = pd.to_datetime(frame["updated_at"], utc=True, errors="coerce")
    if updated.isna().any():
        raise ValueError("Toss short watchlist provider updatedAt is invalid")


def validate_watchlist_dataset(frame: pd.DataFrame) -> None:
    """Validate a retained multi-date dataset as complete two-member dates."""
    if frame.empty:
        raise ValueError("Toss short watchlist dataset is empty")
    parsed = pd.to_datetime(frame["date"], errors="coerce")
    if parsed.isna().any():
        raise ValueError("Toss short watchlist contains an invalid date")
    for target in sorted(parsed.dt.date.astype(str).unique()):
        selected = frame.loc[parsed.dt.date.astype(str).eq(target)].copy()
        validate_staged_watchlist(selected, target_date=target)
    ordered = frame.sort_values(
        list(TOSS_EQUITY_SHORT_WATCHLIST_DAILY.sort_key), kind="stable"
    ).reset_index(drop=True)
    if not frame.reset_index(drop=True).equals(ordered):
        raise ValueError("Toss short watchlist rows are not sorted")


def pre_network_action(
    checkpoint: Mapping[str, Any] | None,
    *,
    target_date: str,
    retained: pd.DataFrame | None = None,
) -> str:
    """Return RUN, RECOVER, or a verified API-zero no-op decision."""
    expected_date = _iso_date(target_date).isoformat()
    if checkpoint is None:
        return "RUN_REQUIRED"
    if checkpoint.get("dataset") != TOSS_EQUITY_SHORT_WATCHLIST_DAILY.name:
        raise ValueError("Toss short watchlist checkpoint dataset mismatch")
    if checkpoint.get("watchlist_version") != TOSS_SHORT_WATCHLIST_VERSION:
        raise ValueError("Toss short watchlist checkpoint version mismatch")
    status = checkpoint.get("status")
    completed_date = checkpoint.get("completed_date")
    if status == "SUCCEEDED" and completed_date == expected_date:
        if retained is None:
            raise ValueError("successful Toss replay requires retained exact-date data")
        validate_staged_watchlist(retained, target_date=expected_date)
        return "NOOP_ALREADY_SUCCEEDED"
    if completed_date == expected_date or status in {
        "RUNNING", "STAGED", "PROMOTING", "ROLLBACK_REQUIRED"
    }:
        return "RECOVERY_REQUIRED_PRE_NETWORK"
    return "RUN_REQUIRED"


def reconcile_official_overlap(
    toss: pd.DataFrame, official: pd.DataFrame, *, target_date: str
) -> pd.DataFrame:
    """Record same-symbol unit and scope differences without merging sources."""
    expected_date = _iso_date(target_date)
    validate_staged_watchlist(toss, target_date=expected_date.isoformat())
    required = {
        "date", "market", "symbol", "short_volume", "short_trading_value"
    }
    if not required <= set(official.columns):
        raise ValueError("official KRX overlap schema is incomplete")
    expected_members = {(market, symbol) for symbol, _name, market in TOSS_SHORT_WATCHLIST}
    selected = official.copy()
    selected["_date"] = pd.to_datetime(selected["date"], errors="coerce").dt.date
    selected = selected[selected["_date"].eq(expected_date)]
    selected = selected[
        selected.apply(
            lambda row: (str(row["market"]), str(row["symbol"])) in expected_members,
            axis=1,
        )
    ]
    if len(selected) != len(expected_members) or selected.duplicated(
        ["date", "market", "symbol"]
    ).any():
        raise ValueError("official KRX overlap must contain every fixed member once")

    official_scope = (
        "KRX_ONLY" if expected_date < KRX_NXT_COMBINED_START else "KRX_NXT_COMBINED"
    )
    comparable = official_scope == "KRX_ONLY"
    toss_for_join = toss.copy()
    toss_for_join["date"] = pd.to_datetime(
        toss_for_join["date"], errors="raise"
    ).dt.strftime("%Y-%m-%d")
    selected["date"] = selected["_date"].astype(str)
    joined = toss_for_join.merge(
        selected,
        on=["date", "market", "symbol"],
        how="inner",
        validate="one_to_one",
        suffixes=("_toss", "_krx"),
    )
    result = pd.DataFrame(
        {
            "date": joined["date"],
            "market": joined["market"],
            "symbol": joined["symbol"],
            "toss_scope": TOSS_SHORT_SOURCE_SCOPE,
            "official_scope": official_scope,
            "volume_unit": "shares",
            "amount_unit": "KRW",
            "toss_volume": joined["short_selling_volume"],
            "official_volume": joined["short_volume"],
            "volume_difference": (
                joined["short_selling_volume"] - joined["short_volume"]
            ),
            "toss_amount": joined["short_selling_amount"],
            "official_amount": joined["short_trading_value"],
            "amount_difference": (
                joined["short_selling_amount"] - joined["short_trading_value"]
            ),
            "scope_comparable": comparable,
            "comparison_reason": (
                "SAME_KRX_ONLY_SCOPE"
                if comparable
                else "NON_EQUIVALENT_KRX_ONLY_VS_KRX_NXT_COMBINED"
            ),
        }
    )
    return result.sort_values(["market", "symbol"], kind="stable").reset_index(drop=True)


def _iso_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(str(value))
    except ValueError:
        raise ValueError("target_date must be an ISO calendar date") from None
    return parsed


def _read_live(root: Path) -> pd.DataFrame:
    return read_dataset(
        root, TOSS_EQUITY_SHORT_WATCHLIST_DAILY, validate_watchlist_dataset
    )


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"invalid JSON object: {path.name}")
    return payload


def _relative(project_root: Path, path: Path) -> str:
    return path.resolve().relative_to(project_root.resolve()).as_posix()


def _recover_incomplete(project_root: Path) -> None:
    journal_path = project_root / "data/state" / JOURNAL_NAME
    journal = _read_json(journal_path)
    if journal is None or journal.get("status") in {
        "SUCCEEDED", "SUCCEEDED_RECOVERED", "FAILED_ROLLED_BACK",
        "ROLLED_BACK_RECOVERED",
    }:
        return
    if journal.get("dataset") != TOSS_EQUITY_SHORT_WATCHLIST_DAILY.name:
        raise ValueError("Toss short watchlist journal dataset mismatch")
    target_date = str(journal.get("target_date", ""))
    live_root = project_root / "data/normalized" / TOSS_EQUITY_SHORT_WATCHLIST_DAILY.name
    checkpoint = _read_json(project_root / "data/state" / STATE_NAME)
    if checkpoint is not None and checkpoint.get("status") == "SUCCEEDED" and (
        checkpoint.get("completed_date") == target_date
    ):
        retained = _read_live(live_root)
        validate_staged_watchlist(
            retained.loc[retained["date"].astype(str).eq(target_date)].copy(),
            target_date=target_date,
        )
        journal["status"] = "SUCCEEDED_RECOVERED"
        journal["recovered_at"] = datetime.now(timezone.utc).isoformat()
        _atomic_json(journal_path, journal)
        transaction_value = str(journal.get("transaction_root", ""))
        transaction = project_root / transaction_value
        if transaction_value and transaction.is_relative_to(
            project_root / "data/staging"
        ) and transaction.is_dir():
            shutil.rmtree(transaction)
        return

    transaction_value = str(journal.get("transaction_root", ""))
    if not transaction_value:
        journal["status"] = "FAILED_ROLLED_BACK"
        journal["failure"] = "INCOMPLETE_BEFORE_PROMOTION"
        _atomic_json(journal_path, journal)
        return
    transaction = project_root / transaction_value
    if not transaction.is_relative_to(project_root / "data/staging"):
        raise RuntimeError("Toss short recovery transaction root is outside staging")
    backup = transaction / "backup"
    displaced = transaction / "displaced_incomplete"
    if journal.get("live_replaced") is True and live_root.exists():
        if displaced.exists():
            shutil.rmtree(displaced)
        live_root.replace(displaced)
    if journal.get("had_live") is True:
        if not backup.exists():
            raise RuntimeError("Toss short recovery backup is missing")
        backup.replace(live_root)
    journal["status"] = "ROLLED_BACK_RECOVERED"
    journal["recovered_at"] = datetime.now(timezone.utc).isoformat()
    _atomic_json(journal_path, journal)
    if transaction.exists():
        shutil.rmtree(transaction)


def _official_overlap_rows(project_root: Path, target_date: str) -> pd.DataFrame:
    path = (
        project_root / "data/normalized/kr_short_selling_trading_daily"
        / "market=KOSPI" / f"year={target_date[:4]}" / "data.parquet"
    )
    if not path.exists():
        raise FileNotFoundError(path)
    frame = pd.read_parquet(path)
    selected = frame.loc[
        frame["date"].astype(str).eq(target_date)
        & frame["symbol"].astype(str).isin(
            [symbol for symbol, _name, _market in TOSS_SHORT_WATCHLIST]
        )
    ].copy()
    return selected


def _json_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return json.loads(frame.to_json(orient="records", date_format="iso"))


def refresh_toss_short_watchlist_daily(
    project_root: Path,
    *,
    intended_date: str,
    client: TossInvestClient | None = None,
    promotion_hook: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Run the selected exact-date two-symbol Toss transaction."""
    target_date = _iso_date(intended_date).isoformat()
    if target_date != "2026-08-19":
        raise ValueError("only the selected target date 2026-08-19 is authorized")
    _recover_incomplete(project_root)

    state_path = project_root / "data/state" / STATE_NAME
    journal_path = project_root / "data/state" / JOURNAL_NAME
    live_root = project_root / "data/normalized" / TOSS_EQUITY_SHORT_WATCHLIST_DAILY.name
    checkpoint = _read_json(state_path)
    retained = _read_live(live_root) if live_root.exists() else None
    action = pre_network_action(
        checkpoint, target_date=target_date,
        retained=(
            retained.loc[retained["date"].astype(str).eq(target_date)].copy()
            if retained is not None else None
        ),
    )
    if action == "NOOP_ALREADY_SUCCEEDED":
        return {
            "status": action,
            "intended_date": target_date,
            "token_calls": 0,
            "market_calls": 0,
            "promoted_rows": 0,
            "retained_rows": len(retained),
        }
    if action != "RUN_REQUIRED":
        raise RuntimeError(action)
    if client is None:
        raise ValueError("Toss client is required for an uncompleted date")

    initial_token_calls = client.token_request_count
    initial_market_calls = client.market_request_count
    transaction_id = "toss_short_" + target_date.replace("-", "") + "_" + (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    )
    transaction_root = project_root / "data/staging" / transaction_id
    transaction_root.mkdir(parents=True, exist_ok=False)
    journal: dict[str, Any] = {
        "dataset": TOSS_EQUITY_SHORT_WATCHLIST_DAILY.name,
        "watchlist_version": TOSS_SHORT_WATCHLIST_VERSION,
        "target_date": target_date,
        "transaction_id": transaction_id,
        "transaction_root": _relative(project_root, transaction_root),
        "status": "RUNNING",
        "had_live": live_root.exists(),
        "live_replaced": False,
        "landing_files": [],
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    _atomic_json(journal_path, journal)

    frames: dict[str, pd.DataFrame] = {}
    try:
        for request in request_plan(target_date):
            response = client.get_market_data(request.endpoint, params=request.params)
            observed = datetime.now(timezone.utc)
            relative = (
                Path("data/landing/tossinvest") / SHORT_SELLING_OPERATION
                / f"watchlist={TOSS_SHORT_WATCHLIST_VERSION}"
                / f"date={target_date}"
                / f"{request.symbol}_{observed.strftime('%Y%m%dT%H%M%S%fZ')}.json"
            )
            landing_path = project_root / relative
            if landing_path.exists():
                raise RuntimeError("immutable Toss Landing target already exists")
            _atomic_json(landing_path, {
                "collected_at": observed.isoformat(),
                "source": "tossinvest_open_api",
                "operation": SHORT_SELLING_OPERATION,
                "watchlist_version": TOSS_SHORT_WATCHLIST_VERSION,
                "target": request.symbol,
                "market": request.market,
                "intended_date": target_date,
                "request": dict(request.params),
                "rate_limit": _rate_payload(response.rate_limit),
                "raw_response": response.payload,
            })
            journal["landing_files"].append(relative.as_posix())
            _atomic_json(journal_path, journal)
            records, _next_until = _extract(response.payload, "records", "nextUntil")
            frames[request.symbol] = normalize_short_selling(
                records, symbol=request.symbol, collected_at=observed
            )
        token_calls = client.token_request_count - initial_token_calls
        market_calls = client.market_request_count - initial_market_calls
        if token_calls > CALL_BUDGET.oauth_calls_max or market_calls != CALL_BUDGET.market_calls_max:
            raise RuntimeError("Toss short watchlist call budget was violated")
        incoming = stage_exact_watchlist(frames, target_date=target_date)
        official = _official_overlap_rows(project_root, target_date)
        overlap = reconcile_official_overlap(incoming, official, target_date=target_date)

        if retained is None:
            combined = incoming
        else:
            combined = pd.concat([retained, incoming], ignore_index=True)
            combined = combined.drop_duplicates(
                list(TOSS_EQUITY_SHORT_WATCHLIST_DAILY.primary_key), keep="last"
            ).sort_values(
                list(TOSS_EQUITY_SHORT_WATCHLIST_DAILY.sort_key), kind="stable"
            ).reset_index(drop=True)
        validate_watchlist_dataset(combined)
        stage_root = transaction_root / "stage"
        backup_root = transaction_root / "backup"
        write_dataset_atomic(
            combined, stage_root, TOSS_EQUITY_SHORT_WATCHLIST_DAILY,
            validate_watchlist_dataset,
        )
        pd.testing.assert_frame_equal(
            _read_live(stage_root), combined, check_dtype=False
        )
        journal.update({
            "status": "STAGED",
            "token_calls": token_calls,
            "market_calls": market_calls,
            "overlap": _json_records(overlap),
        })
        _atomic_json(journal_path, journal)

        if live_root.exists():
            live_root.replace(backup_root)
        journal["status"] = "PROMOTING"
        _atomic_json(journal_path, journal)
        stage_root.replace(live_root)
        journal["live_replaced"] = True
        journal["status"] = "PROMOTED"
        _atomic_json(journal_path, journal)
        if promotion_hook is not None:
            promotion_hook("after_live_replace")
        readback = _read_live(live_root)
        validate_staged_watchlist(
            readback.loc[readback["date"].astype(str).eq(target_date)].copy(),
            target_date=target_date,
        )
        prior_checkpoint = checkpoint
        new_checkpoint = {
            "dataset": TOSS_EQUITY_SHORT_WATCHLIST_DAILY.name,
            "watchlist_version": TOSS_SHORT_WATCHLIST_VERSION,
            "status": "SUCCEEDED",
            "completed_date": target_date,
            "completed_symbols": sorted(frames),
            "landing_files": list(journal["landing_files"]),
            "token_calls": token_calls,
            "market_calls": market_calls,
            "retained_rows": len(readback),
            "provider_updated_at": sorted(
                pd.to_datetime(incoming["updated_at"], utc=True).astype(str).tolist()
            ),
            "overlap": _json_records(overlap),
            "completed_at": datetime.now(timezone.utc).isoformat(),
        }
        _atomic_json(state_path, new_checkpoint)
        journal["status"] = "SUCCEEDED"
        journal["completed_at"] = new_checkpoint["completed_at"]
        _atomic_json(journal_path, journal)
        if backup_root.exists():
            shutil.rmtree(backup_root)
        if transaction_root.exists():
            shutil.rmtree(transaction_root)
        return {
            "status": "SUCCEEDED",
            "intended_date": target_date,
            "token_calls": token_calls,
            "market_calls": market_calls,
            "promoted_rows": len(incoming),
            "retained_rows": len(readback),
            "overlap_rows": len(overlap),
            "overlap_scope": str(overlap.iloc[0]["comparison_reason"]),
        }
    except Exception as error:
        backup_root = transaction_root / "backup"
        displaced = transaction_root / "failed_promoted"
        if journal.get("live_replaced") is True and live_root.exists():
            live_root.replace(displaced)
        if journal.get("had_live") is True and backup_root.exists():
            backup_root.replace(live_root)
        if 'prior_checkpoint' in locals():
            if prior_checkpoint is None:
                state_path.unlink(missing_ok=True)
            else:
                _atomic_json(state_path, prior_checkpoint)
        journal.update({
            "status": "FAILED_ROLLED_BACK",
            "failure_type": type(error).__name__,
            "failed_at": datetime.now(timezone.utc).isoformat(),
            "token_calls": client.token_request_count - initial_token_calls,
            "market_calls": client.market_request_count - initial_market_calls,
        })
        _atomic_json(journal_path, journal)
        if transaction_root.exists():
            shutil.rmtree(transaction_root)
        raise


__all__ = [
    "CALL_BUDGET", "refresh_toss_short_watchlist_daily", "request_plan",
    "stage_exact_watchlist", "validate_staged_watchlist",
    "validate_watchlist_dataset", "pre_network_action",
    "reconcile_official_overlap",
]
