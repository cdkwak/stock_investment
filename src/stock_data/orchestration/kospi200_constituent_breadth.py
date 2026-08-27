"""Offline exact-date KOSPI200 membership, prices, and breadth transaction.

The module has no provider client.  It accepts a retained exact-date membership
observation and already-retained equity prices.  Membership is never carried
backward or forward to another date.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import base64
import json
import os
from pathlib import Path
import re
import shutil
from uuid import uuid4

import pandas as pd

from stock_data.contracts.kospi200_constituent_breadth import (
    KR_INDEX_CONSTITUENT_DAILY,
    KR_KOSPI200_BREADTH_DAILY,
    KR_KOSPI200_CONSTITUENT_PRICE_DAILY,
)
from stock_data.contracts.kr_equity import KR_EQUITY_PRICE_DAILY
from stock_data.providers.krx_mdc.kospi200_constituents import (
    BodyFetcher,
    capture_kospi200_constituents,
)
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic
from stock_data.validation.kospi200_constituent_breadth import (
    validate_index_constituent_daily,
    validate_kospi200_breadth_daily,
    validate_kospi200_constituent_price_daily,
)
from stock_data.validation.kr_equity import validate_equity_price


class KOSPI200BreadthOperationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExactKOSPI200Scope:
    market_date: str
    previous_session_date: str
    membership: pd.DataFrame
    prices: pd.DataFrame
    breadth: pd.DataFrame


@dataclass(frozen=True)
class KOSPI200BreadthOperationResult:
    status: str
    market_date: str
    previous_session_date: str
    constituent_rows: int
    price_rows: int
    breadth_rows: int
    api_calls: int


def latest_accepted_canonical_target(project_root: Path) -> date:
    path = project_root / "data/state/canonical_equity_accepted_dates.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        latest = date.fromisoformat(str(payload["latest_accepted_date"]))
        accepted = {date.fromisoformat(str(value)) for value in payload["accepted_dates"]}
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise KOSPI200BreadthOperationError(
            "canonical accepted-date state is missing or invalid"
        ) from error
    if latest not in accepted:
        raise KOSPI200BreadthOperationError(
            "canonical latest date is not present in the accepted-date ledger"
        )
    return latest


def _completed_result_before_provider(
    project_root: Path, target: str,
) -> KOSPI200BreadthOperationResult | None:
    checkpoint = project_root / "data/state/kr_kospi200_constituent_breadth.json"
    if not checkpoint.exists():
        return None
    try:
        payload = json.loads(checkpoint.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise KOSPI200BreadthOperationError("completion checkpoint is unreadable") from error
    if payload.get("status") != "SUCCEEDED" or payload.get("market_date") != target:
        return None
    scope = _read_outputs(project_root, target)
    return KOSPI200BreadthOperationResult(
        "NOOP_ALREADY_SUCCEEDED", target, scope.previous_session_date,
        len(scope.membership), len(scope.prices), len(scope.breadth), 0,
    )


def run_kospi200_constituent_breadth_daily(
    project_root: Path,
    *,
    market_date: str | date | None = None,
    run_id: str | None = None,
    captured_at: str | None = None,
    body_fetcher: BodyFetcher | None = None,
) -> KOSPI200BreadthOperationResult:
    """Capture and atomically publish the latest canonical accepted KOSPI200 scope.

    A completed exact-date checkpoint is validated before credentials or a
    provider client are touched. New work is limited to the latest date already
    accepted by the canonical equity transaction.
    """
    root = project_root.resolve()
    latest = latest_accepted_canonical_target(root)
    target = latest if market_date is None else date.fromisoformat(_day(market_date))
    if target != latest:
        raise KOSPI200BreadthOperationError(
            "target must equal the latest canonical accepted equity date"
        )
    target_text = target.isoformat()
    completed = _completed_result_before_provider(root, target_text)
    if completed is not None:
        return completed
    price_root = (
        root / "data/normalized/kr_equity_price_daily"
        / "market=KOSPI" / f"year={target.year}"
    )
    equity_prices = read_dataset(price_root, KR_EQUITY_PRICE_DAILY, validate_equity_price)
    identifier = run_id or (
        f"kospi200-breadth-{target:%Y%m%d}-"
        f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid4().hex}"
    )
    observed_at = captured_at or datetime.now(timezone.utc).isoformat()
    capture, membership = capture_kospi200_constituents(
        target,
        run_id=identifier,
        landing_root=root / "data/landing/krx_mdc/kr_index_constituent_daily",
        env_file=root / ".env",
        captured_at=observed_at,
        body_fetcher=body_fetcher,
    )
    if capture.business_calls != 1 or capture.retry_count != 0:
        raise KOSPI200BreadthOperationError("constituent provider call budget differs")
    if len(membership) != 200 or membership["symbol"].nunique() != 200:
        raise KOSPI200BreadthOperationError(
            "exact KOSPI200 response must contain 200 unique members"
        )
    result = run_offline_kospi200_scope(
        root, membership, equity_prices,
        market_date=target, run_id=identifier,
    )
    return KOSPI200BreadthOperationResult(
        result.status, result.market_date, result.previous_session_date,
        result.constituent_rows, result.price_rows, result.breadth_rows, 1,
    )


def _day(value: str | date) -> str:
    try:
        return (value if isinstance(value, date) else date.fromisoformat(value)).isoformat()
    except (TypeError, ValueError) as error:
        raise KOSPI200BreadthOperationError("market_date must use YYYY-MM-DD") from error


def build_exact_kospi200_scope(
    membership: pd.DataFrame,
    equity_prices: pd.DataFrame,
    *,
    market_date: str | date,
) -> ExactKOSPI200Scope:
    """Build a complete exact-date scope; partial prices fail closed."""
    target = _day(market_date)
    validate_index_constituent_daily(membership)
    if membership["date"].astype(str).nunique() != 1 or str(membership["date"].iloc[0]) != target:
        raise KOSPI200BreadthOperationError(
            "membership observation must equal market_date; as-of/backprojection is forbidden"
        )
    validate_equity_price(equity_prices)
    prices = equity_prices.loc[equity_prices["market"].eq("KOSPI")].copy()
    price_days = sorted(set(prices["date"].astype(str)))
    prior_days = [value for value in price_days if value < target]
    if target not in price_days or not prior_days:
        raise KOSPI200BreadthOperationError("target and previous-session prices are required")
    previous = prior_days[-1]
    symbols = set(membership["symbol"].astype(str))
    current = prices.loc[prices["date"].astype(str).eq(target) & prices["symbol"].astype(str).isin(symbols)].copy()
    prior = prices.loc[prices["date"].astype(str).eq(previous) & prices["symbol"].astype(str).isin(symbols)].copy()
    current_symbols = set(current["symbol"].astype(str))
    prior_symbols = set(prior["symbol"].astype(str))
    if current_symbols != symbols or prior_symbols != symbols:
        missing_current = sorted(symbols - current_symbols)
        missing_prior = sorted(symbols - prior_symbols)
        raise KOSPI200BreadthOperationError(
            f"exact constituent price coverage is incomplete: current={missing_current[:3]} previous={missing_prior[:3]}"
        )
    published = current.assign(membership_observation_date=target)[list(KR_KOSPI200_CONSTITUENT_PRICE_DAILY.column_names)]
    published = published.sort_values(list(KR_KOSPI200_CONSTITUENT_PRICE_DAILY.sort_key), kind="stable").reset_index(drop=True)
    validate_kospi200_constituent_price_daily(published)

    comparison = current[["symbol", "close"]].merge(
        prior[["symbol", "close"]], on="symbol", suffixes=("_current", "_previous"), validate="one_to_one"
    )
    advancing = int(comparison["close_current"].gt(comparison["close_previous"]).sum())
    declining = int(comparison["close_current"].lt(comparison["close_previous"]).sum())
    unchanged = int(comparison["close_current"].eq(comparison["close_previous"]).sum())
    breadth = pd.DataFrame([{
        "date": target,
        "membership_observation_date": target,
        "previous_session_date": previous,
        "index_symbol": "KOSPI200",
        "index_ticker": "1028",
        "advancing": advancing,
        "declining": declining,
        "unchanged": unchanged,
        "total": len(symbols),
        "missing_price_count": 0,
        "scope_status": "COMPLETE_EXACT_DATE",
    }], columns=KR_KOSPI200_BREADTH_DAILY.column_names)
    validate_kospi200_breadth_daily(breadth)
    return ExactKOSPI200Scope(target, previous, membership.copy(deep=True), published, breadth)


def _atomic_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    if temporary.exists():
        raise KOSPI200BreadthOperationError("state temporary path already exists")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _remove(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _roots(project_root: Path) -> dict[str, Path]:
    return {
        "membership": project_root / "data/normalized/kr_index_constituent_daily",
        "prices": project_root / "data/published/kr_kospi200_constituent_price_daily",
        "breadth": project_root / "data/derived/kr_kospi200_breadth_daily",
    }


def _merge_preserving_history(root: Path, incoming: pd.DataFrame, contract, validator) -> pd.DataFrame:
    if not root.exists():
        return incoming.copy(deep=True)
    existing = read_dataset(root, contract, validator)
    key = list(contract.primary_key)
    incoming_keys = set(map(tuple, incoming[key].itertuples(index=False, name=None)))
    existing_index = {
        tuple(row[column] for column in key): row
        for _, row in existing.iterrows()
    }
    for _, row in incoming.iterrows():
        identity = tuple(row[column] for column in key)
        if identity in existing_index and row.to_dict() != existing_index[identity].to_dict():
            raise KOSPI200BreadthOperationError(f"retained finalized key conflicts: {identity}")
    appended = incoming.loc[
        ~incoming.apply(lambda row: tuple(row[column] for column in key) in existing_index, axis=1)
    ]
    merged = pd.concat([existing, appended], ignore_index=True)
    merged = merged.sort_values(list(contract.sort_key), kind="stable").reset_index(drop=True)
    validator(merged)
    return merged


def _read_outputs(project_root: Path, market_date: str) -> ExactKOSPI200Scope:
    roots = _roots(project_root)
    membership = read_dataset(roots["membership"], KR_INDEX_CONSTITUENT_DAILY, validate_index_constituent_daily)
    prices = read_dataset(roots["prices"], KR_KOSPI200_CONSTITUENT_PRICE_DAILY, validate_kospi200_constituent_price_daily)
    breadth = read_dataset(roots["breadth"], KR_KOSPI200_BREADTH_DAILY, validate_kospi200_breadth_daily)
    membership = membership.loc[membership["date"].astype(str).eq(market_date)].reset_index(drop=True)
    prices = prices.loc[prices["date"].astype(str).eq(market_date)].reset_index(drop=True)
    breadth = breadth.loc[breadth["date"].astype(str).eq(market_date)].reset_index(drop=True)
    if membership.empty or prices.empty or breadth.empty or len(membership) != len(prices):
        raise KOSPI200BreadthOperationError("checkpoint exists but exact-date output scope is incomplete")
    return ExactKOSPI200Scope(market_date, str(breadth.iloc[0]["previous_session_date"]), membership, prices, breadth)


def _recover(project_root: Path, journal: Path) -> None:
    if not journal.exists():
        return
    try:
        payload = json.loads(journal.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise KOSPI200BreadthOperationError("transaction journal is unreadable") from error
    if payload.get("status") == "SUCCEEDED":
        _remove(Path(payload["transaction_root"]))
        return
    roots = _roots(project_root)
    transaction = Path(payload.get("transaction_root", ""))
    if not transaction.resolve().is_relative_to((project_root / "data/staging/kospi200_scope").resolve()):
        raise KOSPI200BreadthOperationError("transaction root escapes approved staging")
    backups = transaction / "backups"
    for name, root in reversed(tuple(roots.items())):
        backup = backups / name
        if backup.exists():
            _remove(root)
            backup.replace(root)
        elif name in payload.get("promoted", []):
            _remove(root)
    checkpoint = project_root / "data/state/kr_kospi200_constituent_breadth.json"
    prior = payload.get("prior_checkpoint_b64")
    if prior is None:
        checkpoint.unlink(missing_ok=True)
    else:
        checkpoint.write_bytes(base64.b64decode(prior))
    _remove(transaction)
    _atomic_json(journal, {**payload, "status": "RECOVERED"})


def run_offline_kospi200_scope(
    project_root: Path,
    membership: pd.DataFrame,
    equity_prices: pd.DataFrame,
    *,
    market_date: str | date,
    run_id: str,
) -> KOSPI200BreadthOperationResult:
    """Atomically promote membership, prices, breadth, and checkpoint together."""
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", run_id):
        raise KOSPI200BreadthOperationError("run_id is invalid")
    target = _day(market_date)
    state = project_root / "data/state"
    checkpoint = state / "kr_kospi200_constituent_breadth.json"
    journal = state / "kr_kospi200_constituent_breadth.transaction.json"
    _recover(project_root, journal)
    if checkpoint.exists():
        payload = json.loads(checkpoint.read_text(encoding="utf-8"))
        if payload.get("status") == "SUCCEEDED" and payload.get("market_date") == target:
            scope = _read_outputs(project_root, target)
            return KOSPI200BreadthOperationResult("NOOP_ALREADY_SUCCEEDED", target, scope.previous_session_date, len(scope.membership), len(scope.prices), len(scope.breadth), 0)
    scope = build_exact_kospi200_scope(membership, equity_prices, market_date=target)
    transaction = project_root / "data/staging/kospi200_scope" / f"{target.replace('-', '')}-{run_id}-{uuid4().hex}"
    candidates = transaction / "candidates"
    backups = transaction / "backups"
    roots = _roots(project_root)
    writers = {
        "membership": (scope.membership, KR_INDEX_CONSTITUENT_DAILY, validate_index_constituent_daily),
        "prices": (scope.prices, KR_KOSPI200_CONSTITUENT_PRICE_DAILY, validate_kospi200_constituent_price_daily),
        "breadth": (scope.breadth, KR_KOSPI200_BREADTH_DAILY, validate_kospi200_breadth_daily),
    }
    transaction.mkdir(parents=True, exist_ok=False)
    for name, (frame, contract, validator) in writers.items():
        merged = _merge_preserving_history(roots[name], frame, contract, validator)
        write_dataset_atomic(merged, candidates / name, contract, validator)
    prior_checkpoint = checkpoint.read_bytes() if checkpoint.exists() else None
    payload = {
        "version": 1,
        "status": "PREPARED",
        "market_date": target,
        "transaction_root": str(transaction.resolve()),
        "promoted": [],
        "prior_checkpoint_b64": base64.b64encode(prior_checkpoint).decode("ascii") if prior_checkpoint is not None else None,
    }
    _atomic_json(journal, payload)
    try:
        backups.mkdir(parents=True, exist_ok=True)
        for name, root in roots.items():
            root.parent.mkdir(parents=True, exist_ok=True)
            if root.exists():
                root.replace(backups / name)
            (candidates / name).replace(root)
            payload["promoted"].append(name)
            payload["status"] = "PROMOTING"
            _atomic_json(journal, payload)
        _atomic_json(checkpoint, {
            "version": 1,
            "status": "SUCCEEDED",
            "market_date": target,
            "previous_session_date": scope.previous_session_date,
            "constituent_rows": len(scope.membership),
            "price_rows": len(scope.prices),
            "breadth_rows": len(scope.breadth),
        })
        payload["status"] = "SUCCEEDED"
        _atomic_json(journal, payload)
    except Exception:
        _recover(project_root, journal)
        raise
    _remove(transaction)
    return KOSPI200BreadthOperationResult("SUCCEEDED", target, scope.previous_session_date, len(scope.membership), len(scope.prices), len(scope.breadth), 0)


__all__ = [
    "ExactKOSPI200Scope",
    "KOSPI200BreadthOperationError",
    "KOSPI200BreadthOperationResult",
    "build_exact_kospi200_scope",
    "latest_accepted_canonical_target",
    "run_kospi200_constituent_breadth_daily",
    "run_offline_kospi200_scope",
]
