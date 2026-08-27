from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time
from uuid import uuid4

from stock_data.orchestration.daily_operations import DailyRunLock
from stock_data.orchestration.exchange_calendar import (
    ExchangeMarket,
    ExchangeTradingCalendar,
)
from stock_data.pipelines.canonical_equity_incremental import (
    build_date_frames,
    promote_date_atomic,
    refresh_breadth_date_atomic,
)
from stock_data.providers.data_go_kr.client import (
    DataGoKrClient,
    DataGoKrResult,
    service_key_from_environment,
    write_landing_pages_atomic,
)
from stock_data.providers.data_go_kr.stock_price import STOCK_PRICE_ENDPOINT
from stock_data.providers.data_go_kr.universe import UNIVERSE_ENDPOINT


class CanonicalEquityDailyError(RuntimeError):
    """Safe scheduler failure that never embeds response bodies or credentials."""


@dataclass(frozen=True)
class CanonicalEquityDailyResult:
    status: str
    available_through: date
    selected_date: date | None
    api_calls: int
    run_id: str | None
    latest_before: date
    latest_after: date
    reason: str


@dataclass(frozen=True)
class CanonicalEquityCatchupResult:
    status: str
    available_through: date
    selected_date: date | None
    api_calls: int
    run_id: str | None
    latest_before: date
    latest_after: date
    reason: str
    selected_dates: tuple[date, ...]
    attempted_dates: tuple[date, ...]
    accepted_dates: tuple[date, ...]
    run_ids: tuple[str, ...]


StreamSupplier = Callable[[date, str], DataGoKrResult]


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(payload, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _accepted_state(project_root: Path) -> tuple[date, frozenset[date]]:
    path = project_root / "data/state/canonical_equity_accepted_dates.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        values = frozenset(date.fromisoformat(str(item)) for item in payload["accepted_dates"])
        latest = date.fromisoformat(str(payload["latest_accepted_date"]))
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise CanonicalEquityDailyError("canonical equity accepted-date state is invalid") from error
    if not values or latest != max(values):
        raise CanonicalEquityDailyError("canonical equity accepted-date state is inconsistent")
    return latest, values


def oldest_missing_session(project_root: Path, *, available_through: date) -> date | None:
    """Select one bounded append date; never jump over an unaccepted session."""

    latest, accepted = _accepted_state(project_root)
    if latest >= available_through:
        return None
    calendar = ExchangeTradingCalendar(ExchangeMarket.KR)
    selected = calendar.next_trading_day(latest)
    if selected > available_through:
        return None
    if selected in accepted:
        raise CanonicalEquityDailyError("accepted-date state contains a non-terminal duplicate")
    return selected


def _live_stream_supplier(project_root: Path) -> StreamSupplier:
    service_key = service_key_from_environment(project_root)
    endpoints = {"price_cap": STOCK_PRICE_ENDPOINT, "universe": UNIVERSE_ENDPOINT}

    def supply(target: date, stream: str) -> DataGoKrResult:
        if stream not in endpoints:
            raise CanonicalEquityDailyError("unknown canonical equity stream")
        base_date = target.strftime("%Y%m%d")
        return DataGoKrClient(
            endpoint=endpoints[stream],
            service_key=service_key,
            max_attempts=2,
            backoff_seconds=1.0,
        ).fetch_all(filters={"basDt": base_date}, num_of_rows=9999, max_pages=1)

    return supply


def run_canonical_equity_daily(
    project_root: Path,
    *,
    available_through: date,
    stream_supplier: StreamSupplier | None = None,
    now: datetime | None = None,
) -> CanonicalEquityDailyResult:
    """Capture and atomically advance exactly one oldest eligible XKRX date."""

    root = project_root.resolve()
    latest_before, _accepted = _accepted_state(root)
    selected = oldest_missing_session(root, available_through=available_through)
    if selected is None:
        return CanonicalEquityDailyResult(
            "NOOP_IDEMPOTENT", available_through, None, 0, None,
            latest_before, latest_before, "AVAILABLE_TARGET_ALREADY_ACCEPTED",
        )

    started = now or datetime.now(timezone.utc)
    if started.tzinfo is None or started.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    run_id = f"canonical-equity-{selected:%Y%m%d}-{uuid4().hex}"
    provider_lock = DailyRunLock(
        root / "data/state/data_go_kr_provider.lock",
        run_id=run_id,
        acquired_at=started,
    ).acquire()
    try:
        # Re-evaluate under the shared provider lock so two eligible occurrences
        # cannot both spend calls on the same oldest missing date.
        selected = oldest_missing_session(root, available_through=available_through)
        if selected is None:
            latest_after, _accepted = _accepted_state(root)
            return CanonicalEquityDailyResult(
                "NOOP_IDEMPOTENT", available_through, None, 0, None,
                latest_before, latest_after, "AVAILABLE_TARGET_ALREADY_ACCEPTED",
            )
        capture_root = root / "data/landing/data_go_kr/canonical_equity_daily" / run_id
        price_path = capture_root / "price_cap.json"
        universe_path = capture_root / "universe.json"
        supplier = stream_supplier or _live_stream_supplier(root)
        captured: dict[str, DataGoKrResult] = {}
        for stream, path in (("price_cap", price_path), ("universe", universe_path)):
            result = supplier(selected, stream)
            if not isinstance(result, DataGoKrResult):
                raise CanonicalEquityDailyError(
                    "canonical equity supplier returned an invalid result"
                )
            if len(result.pages) != 1:
                raise CanonicalEquityDailyError(
                    "canonical equity capture must use one page per stream"
                )
            # Capture each successful response before spending the next call.
            # A later provider failure therefore cannot erase earlier evidence.
            write_landing_pages_atomic(result.pages, path)
            captured[stream] = result
        price = captured["price_cap"]
        universe = captured["universe"]
        price_sha256 = _sha256(price_path)
        universe_sha256 = _sha256(universe_path)
        receipt = {
            "schema_version": 1,
            "run_id": run_id,
            "target_date": selected.isoformat(),
            "retry_count": 0,
            "api_calls": 2,
            "streams": {
                "price_cap": {
                    "total_count": price.total_count,
                    "sha256": price_sha256,
                },
                "universe": {
                    "total_count": universe.total_count,
                    "sha256": universe_sha256,
                },
            },
        }
        _atomic_json(capture_root / "receipt.json", receipt)
        if price.total_count == 0 or universe.total_count == 0:
            return CanonicalEquityDailyResult(
                "DEGRADED_VALID_EMPTY_PRESERVED", available_through, selected, 2,
                run_id, latest_before, latest_before,
                "BOTH_EXACT_DATE_STREAMS_MUST_BE_NON_EMPTY",
            )

        base_date = selected.strftime("%Y%m%d")
        frames = build_date_frames(
            root,
            base_date=base_date,
            price_landing=price_path,
            universe_landing=universe_path,
        )
        manifest_digest = hashlib.sha256(
            json.dumps(
                {
                    "price_cap": price_sha256,
                    "universe": universe_sha256,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        promoted = promote_date_atomic(
            root,
            base_date=base_date,
            frames=frames,
            landing_manifest_sha256=manifest_digest,
        )
        breadth = refresh_breadth_date_atomic(root, base_date=base_date)
        latest_after, _accepted = _accepted_state(root)
        if latest_after != selected or breadth.get("status") != "AFFECTED_BREADTH_COMPLETE":
            raise CanonicalEquityDailyError("canonical equity read-back did not reach selected date")
        return CanonicalEquityDailyResult(
            str(promoted.get("status", "CANONICAL_ACCEPTED_DATE")),
            available_through, selected, 2, run_id,
            latest_before, latest_after, "ATOMIC_FOUR_DATASET_AND_BREADTH_PROMOTION",
        )
    finally:
        provider_lock.release()


def run_canonical_equity_catchup(
    project_root: Path,
    *,
    available_through: date,
    max_sessions: int = 3,
    max_api_calls: int = 6,
    max_elapsed_seconds: float = 600.0,
    stream_supplier: StreamSupplier | None = None,
    now: datetime | None = None,
    monotonic_fn: Callable[[], float] = time.monotonic,
) -> CanonicalEquityCatchupResult:
    """Advance consecutive missing sessions inside bounded call and time budgets.

    Each date remains its own atomic transaction. A degraded or failed date stops
    the loop, so a later session can never jump over unresolved earlier evidence.
    The elapsed budget is checked between dates and never interrupts an atomic
    two-stream capture/promotion already in progress.
    """

    if max_sessions < 1:
        raise ValueError("max_sessions must be positive")
    if max_api_calls < 2:
        raise ValueError("max_api_calls must allow one two-stream session")
    if max_elapsed_seconds <= 0:
        raise ValueError("max_elapsed_seconds must be positive")

    root = project_root.resolve()
    latest_before, _accepted = _accepted_state(root)
    started = monotonic_fn()
    results: list[CanonicalEquityDailyResult] = []
    attempted_dates: list[date] = []
    accepted_dates: list[date] = []
    supplier_calls = 0
    live_supplier: StreamSupplier | None = stream_supplier
    budget_exhausted = False

    def metered_supplier(target: date, stream: str) -> DataGoKrResult:
        nonlocal supplier_calls, live_supplier
        if live_supplier is None:
            live_supplier = _live_stream_supplier(root)
        supplier_calls += 1
        return live_supplier(target, stream)

    while len(results) < max_sessions:
        if supplier_calls + 2 > max_api_calls:
            budget_exhausted = True
            break
        if monotonic_fn() - started >= max_elapsed_seconds:
            budget_exhausted = True
            break
        selected = oldest_missing_session(root, available_through=available_through)
        if selected is None:
            if not results:
                return CanonicalEquityCatchupResult(
                    "NOOP_IDEMPOTENT", available_through, None, 0, None,
                    latest_before, latest_before, "AVAILABLE_TARGET_ALREADY_ACCEPTED",
                    (), (), (), (),
                )
            break
        attempted_dates.append(selected)
        calls_before = supplier_calls
        try:
            result = run_canonical_equity_daily(
                root,
                available_through=available_through,
                stream_supplier=metered_supplier,
                now=now,
            )
        except Exception as error:
            try:
                latest_after, retained_acceptance = _accepted_state(root)
            except CanonicalEquityDailyError:
                latest_after = results[-1].latest_after if results else latest_before
                retained_acceptance = frozenset(accepted_dates)
            reason = f"FIRST_UNRESOLVED_DATE_{type(error).__name__}"
            if selected in retained_acceptance:
                if selected not in accepted_dates:
                    accepted_dates.append(selected)
                breadth_path = root / "data/state/canonical_equity_breadth_status.json"
                try:
                    breadth_state = json.loads(breadth_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    breadth_state = None
                if (
                    isinstance(breadth_state, dict)
                    and breadth_state.get("status") == "PENDING"
                    and breadth_state.get("pending_date") == selected.isoformat()
                ):
                    reason = "CANONICAL_ACCEPTED_BREADTH_PENDING"
                else:
                    reason = "CANONICAL_ACCEPTED_BREADTH_STATE_UNVERIFIED"
            run_ids = tuple(item.run_id for item in results if item.run_id is not None)
            return CanonicalEquityCatchupResult(
                "FAILED_PRESERVED", available_through, selected, supplier_calls,
                None, latest_before, latest_after,
                reason,
                tuple(attempted_dates), tuple(attempted_dates),
                tuple(accepted_dates), run_ids,
            )
        if supplier_calls == calls_before:
            # Deterministic injected phase runners may report their bounded call
            # count without invoking the transport wrapper.
            supplier_calls += result.api_calls
        if result.status == "NOOP_IDEMPOTENT":
            if not results:
                return CanonicalEquityCatchupResult(
                    result.status, available_through, None, 0, None,
                    latest_before, result.latest_after, result.reason,
                    (), (), (), (),
                )
            break
        results.append(result)
        if result.selected_date is not None and result.latest_after == result.selected_date:
            accepted_dates.append(result.selected_date)
        if result.status.startswith("DEGRADED_"):
            break
        if result.latest_after >= available_through:
            break

    if not results:
        latest_after, _accepted = _accepted_state(root)
        selected = oldest_missing_session(root, available_through=available_through)
        if budget_exhausted and selected is not None:
            return CanonicalEquityCatchupResult(
                "CANONICAL_BOUNDED_CATCHUP", available_through, None, 0, None,
                latest_before, latest_after, "BOUNDED_CATCHUP_BUDGET_EXHAUSTED",
                (), (), (), (),
            )
        return CanonicalEquityCatchupResult(
            "NOOP_IDEMPOTENT", available_through, None, 0, None,
            latest_before, latest_after, "AVAILABLE_TARGET_ALREADY_ACCEPTED",
            (), (), (), (),
        )

    last = results[-1]
    selected_dates = tuple(
        item.selected_date for item in results if item.selected_date is not None
    )
    run_ids = tuple(item.run_id for item in results if item.run_id is not None)
    api_calls = supplier_calls
    if last.status.startswith("DEGRADED_"):
        status = last.status
        reason = last.reason
    elif last.latest_after >= available_through:
        status = "CANONICAL_ACCEPTED_DATE"
        reason = "BOUNDED_CATCHUP_REACHED_AVAILABLE_TARGET"
    else:
        status = "CANONICAL_BOUNDED_CATCHUP"
        reason = "BOUNDED_CATCHUP_BUDGET_EXHAUSTED"
    return CanonicalEquityCatchupResult(
        status, available_through, last.selected_date, api_calls, last.run_id,
        latest_before, last.latest_after, reason, selected_dates,
        tuple(attempted_dates), tuple(accepted_dates), run_ids,
    )


__all__ = [
    "CanonicalEquityDailyError",
    "CanonicalEquityCatchupResult",
    "CanonicalEquityDailyResult",
    "oldest_missing_session",
    "run_canonical_equity_catchup",
    "run_canonical_equity_daily",
]
