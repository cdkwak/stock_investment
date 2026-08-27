"""Bounded Dashboard refresh coordination without implicit provider access.

The coordinator in this module is deliberately transport-free.  A caller must
inject an already-authorized lane runner, which keeps GUI startup and local
polling at API zero and prevents this layer from turning an accepted EOD route
into an inferred intraday route.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from pathlib import Path
from threading import Lock
from types import MappingProxyType
from zoneinfo import ZoneInfo


KST = ZoneInfo("Asia/Seoul")


class DashboardRefreshError(RuntimeError):
    """Raised when a refresh request violates the bounded Dashboard policy."""


class ObservationLayer(StrEnum):
    FINAL_EOD = "FINAL_EOD"
    PROVISIONAL_INTRADAY = "PROVISIONAL_INTRADAY"


class LaneOutcome(StrEnum):
    UPDATED = "UPDATED"
    NOOP_CURRENT = "NOOP_CURRENT"
    NO_NEW_PUBLICATION = "NO_NEW_PUBLICATION"
    FAILED = "FAILED"


class RefreshOutcome(StrEnum):
    UPDATED = "UPDATED"
    NOOP_CURRENT = "NOOP_CURRENT"
    NO_NEW_PUBLICATION = "NO_NEW_PUBLICATION"
    PARTIAL_FAILURE = "PARTIAL_FAILURE"
    FAILED = "FAILED"
    COALESCED = "COALESCED"
    CLOSED = "CLOSED"


@dataclass(frozen=True)
class DashboardLanePolicy:
    lane_id: str
    dataset_ids: tuple[str, ...]
    layer: ObservationLayer
    minimum_provider_interval: timedelta | None
    provider_refresh_enabled: bool
    activation_gate: str | None = None

    def __post_init__(self) -> None:
        if not self.lane_id or not self.dataset_ids:
            raise ValueError("a Dashboard lane needs an id and at least one dataset")
        if self.layer is ObservationLayer.FINAL_EOD and self.minimum_provider_interval:
            raise ValueError("final EOD lanes cannot inherit a periodic intraday cadence")
        if self.layer is ObservationLayer.PROVISIONAL_INTRADAY:
            if self.minimum_provider_interval is None:
                raise ValueError("an intraday lane needs an explicit minimum interval")
            if self.minimum_provider_interval < timedelta(minutes=30):
                raise ValueError("Dashboard provider polling cannot be faster than 30 minutes")


# This is an exact display-lane allowlist, not a provider or security universe.
# Daily lanes remain finalized EOD authority.  The two native-15m routes have
# already passed exact route validation, but UR-111 keeps their 30-minute GUI
# activation off until core recovery is accepted and a runbook explicitly
# authorizes the cadence.
DASHBOARD_REFRESH_LANES = MappingProxyType({
    "FRED_DAILY": DashboardLanePolicy(
        "FRED_DAILY",
        ("fred_treasury_yield_daily", "fred_usd_fx_daily", "fred_vix_daily"),
        ObservationLayer.FINAL_EOD,
        None,
        True,
    ),
    "LENDING_DAILY": DashboardLanePolicy(
        "LENDING_DAILY",
        ("kr_stock_lending_daily", "kr_stock_lending_market_daily"),
        ObservationLayer.FINAL_EOD,
        None,
        True,
    ),
    "SHORT_SELLING_DAILY": DashboardLanePolicy(
        "SHORT_SELLING_DAILY",
        ("kr_short_selling_trading_daily",),
        ObservationLayer.FINAL_EOD,
        None,
        False,
        "EXPLICIT_USER_EXECUTION_APPROVAL_REQUIRED",
    ),
    "VKOSPI_DAILY": DashboardLanePolicy(
        "VKOSPI_DAILY", ("kr_vkospi_daily",), ObservationLayer.FINAL_EOD, None, True,
    ),
    "KR_INDEX_DAILY": DashboardLanePolicy(
        "KR_INDEX_DAILY",
        ("kr_index_daily", "kr_kospi200_index_daily"),
        ObservationLayer.FINAL_EOD,
        None,
        True,
    ),
    "MARKET_INVESTOR_DAILY": DashboardLanePolicy(
        "MARKET_INVESTOR_DAILY",
        ("kr_market_investor_net_purchase_bridge_daily",),
        ObservationLayer.FINAL_EOD,
        None,
        True,
    ),
    "GLOBAL_INDEX_DAILY": DashboardLanePolicy(
        "GLOBAL_INDEX_DAILY", ("global_index_price_daily",), ObservationLayer.FINAL_EOD, None, True,
    ),
    "GLOBAL_ETF_DAILY": DashboardLanePolicy(
        "GLOBAL_ETF_DAILY", ("global_etf_price_daily",), ObservationLayer.FINAL_EOD, None, True,
    ),
    "GLOBAL_COMMODITY_DAILY": DashboardLanePolicy(
        "GLOBAL_COMMODITY_DAILY",
        ("global_commodity_futures_daily",),
        ObservationLayer.FINAL_EOD,
        None,
        True,
    ),
    "CBOE_VIX_NATIVE_15M": DashboardLanePolicy(
        "CBOE_VIX_NATIVE_15M",
        ("market_price_15m_observation",),
        ObservationLayer.PROVISIONAL_INTRADAY,
        timedelta(minutes=30),
        False,
        "UR111_CORE_RECOVERY_AND_30M_RUNBOOK_REQUIRED",
    ),
    "YAHOO_TREASURY_NATIVE_15M": DashboardLanePolicy(
        "YAHOO_TREASURY_NATIVE_15M",
        ("market_price_15m_observation",),
        ObservationLayer.PROVISIONAL_INTRADAY,
        timedelta(minutes=30),
        False,
        "UR111_CORE_RECOVERY_AND_30M_RUNBOOK_REQUIRED",
    ),
})


@dataclass(frozen=True)
class LaneRefreshResult:
    lane_id: str
    outcome: LaneOutcome
    changed_dataset_ids: tuple[str, ...] = ()
    latest: str | None = None
    expected: str | None = None
    source_timestamp: str | None = None
    last_local_refresh_kst: str | None = None
    finality: str | None = None
    error_type: str | None = None


@dataclass(frozen=True)
class DashboardRefreshReport:
    outcome: RefreshOutcome
    trigger: str
    lane_results: tuple[LaneRefreshResult, ...]
    changed_dataset_ids: tuple[str, ...]
    started_at_utc: str
    completed_at_utc: str


LaneRunner = Callable[[DashboardLanePolicy], Mapping[str, object]]


def _typed_lane_result(
    policy: DashboardLanePolicy, raw: Mapping[str, object], *, refreshed_at: datetime,
) -> LaneRefreshResult:
    status = str(raw.get("status", ""))
    aliases = {"NO_NEW_DATA": "NO_NEW_PUBLICATION"}
    status = aliases.get(status, status)
    try:
        outcome = LaneOutcome(status)
    except ValueError as error:
        raise DashboardRefreshError(
            f"{policy.lane_id} returned no typed advancement outcome"
        ) from error

    changed = tuple(str(item) for item in raw.get("changed_dataset_ids", ()))
    unknown = set(changed).difference(policy.dataset_ids)
    if unknown:
        raise DashboardRefreshError(
            f"{policy.lane_id} reported non-allowlisted datasets: {sorted(unknown)}"
        )
    if outcome is LaneOutcome.UPDATED and not changed:
        raise DashboardRefreshError("UPDATED requires at least one changed dataset")
    if outcome is not LaneOutcome.UPDATED and changed:
        raise DashboardRefreshError("only UPDATED may invalidate Dashboard datasets")

    finality = str(raw.get("finality") or policy.layer.value)
    if policy.layer is ObservationLayer.FINAL_EOD and finality != "FINAL_EOD":
        raise DashboardRefreshError("a finalized lane cannot be relabelled provisional")
    if policy.layer is ObservationLayer.PROVISIONAL_INTRADAY and finality == "FINAL_EOD":
        raise DashboardRefreshError("an intraday route cannot masquerade as final EOD")

    error_type = None
    if outcome is LaneOutcome.FAILED:
        error_type = str(raw.get("error_type") or "UNTYPED_FAILURE")
    return LaneRefreshResult(
        lane_id=policy.lane_id,
        outcome=outcome,
        changed_dataset_ids=changed,
        latest=str(raw["latest"]) if raw.get("latest") is not None else None,
        expected=str(raw["expected"]) if raw.get("expected") is not None else None,
        source_timestamp=(
            str(raw["source_timestamp"]) if raw.get("source_timestamp") is not None else None
        ),
        last_local_refresh_kst=(
            str(raw.get("last_local_refresh_kst"))
            if raw.get("last_local_refresh_kst") is not None
            else refreshed_at.astimezone(KST).isoformat()
        ),
        finality=finality,
        error_type=error_type,
    )


def _aggregate(results: tuple[LaneRefreshResult, ...]) -> RefreshOutcome:
    outcomes = {result.outcome for result in results}
    if not results:
        return RefreshOutcome.NOOP_CURRENT
    if outcomes == {LaneOutcome.FAILED}:
        return RefreshOutcome.FAILED
    if LaneOutcome.FAILED in outcomes:
        return RefreshOutcome.PARTIAL_FAILURE
    if LaneOutcome.UPDATED in outcomes:
        return RefreshOutcome.UPDATED
    if outcomes == {LaneOutcome.NOOP_CURRENT}:
        return RefreshOutcome.NOOP_CURRENT
    return RefreshOutcome.NO_NEW_PUBLICATION


class DashboardRefreshCoordinator:
    """Serialize manual/scheduled refreshes and return advancement truth."""

    def __init__(
        self,
        *,
        lanes: Mapping[str, DashboardLanePolicy] = DASHBOARD_REFRESH_LANES,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._lanes = lanes
        self._clock = clock
        self._run_lock = Lock()
        self._state_lock = Lock()
        self._closed = False
        self._last_report: DashboardRefreshReport | None = None

    @property
    def last_report(self) -> DashboardRefreshReport | None:
        with self._state_lock:
            return self._last_report

    def close(self) -> None:
        with self._state_lock:
            self._closed = True

    def run(
        self,
        lane_ids: Iterable[str],
        *,
        trigger: str,
        runner: LaneRunner,
        permit_provider: bool = False,
    ) -> DashboardRefreshReport:
        requested = tuple(dict.fromkeys(lane_ids))
        unknown = set(requested).difference(self._lanes)
        if unknown:
            raise DashboardRefreshError(f"unknown Dashboard refresh lanes: {sorted(unknown)}")
        started = self._clock().astimezone(timezone.utc)
        with self._state_lock:
            if self._closed:
                return DashboardRefreshReport(
                    RefreshOutcome.CLOSED, trigger, (), (), started.isoformat(), started.isoformat()
                )
        if not self._run_lock.acquire(blocking=False):
            return DashboardRefreshReport(
                RefreshOutcome.COALESCED, trigger, (), (), started.isoformat(), started.isoformat()
            )
        try:
            results: list[LaneRefreshResult] = []
            for lane_id in requested:
                policy = self._lanes[lane_id]
                if not permit_provider:
                    raise DashboardRefreshError(
                        "provider execution is disabled; local polling must not invoke a lane runner"
                    )
                if not policy.provider_refresh_enabled:
                    results.append(LaneRefreshResult(
                        lane_id=lane_id,
                        outcome=LaneOutcome.FAILED,
                        finality=policy.layer.value,
                        error_type=policy.activation_gate or "PROVIDER_REFRESH_DISABLED",
                        last_local_refresh_kst=started.astimezone(KST).isoformat(),
                    ))
                    continue
                try:
                    raw = runner(policy)
                    result = _typed_lane_result(policy, raw, refreshed_at=self._clock())
                except DashboardRefreshError:
                    raise
                except Exception as error:  # typed locally; raw exception details are not retained
                    result = LaneRefreshResult(
                        lane_id=lane_id,
                        outcome=LaneOutcome.FAILED,
                        finality=policy.layer.value,
                        error_type=type(error).__name__,
                        last_local_refresh_kst=self._clock().astimezone(KST).isoformat(),
                    )
                results.append(result)
            completed = self._clock().astimezone(timezone.utc)
            typed_results = tuple(results)
            changed = tuple(dict.fromkeys(
                dataset_id
                for result in typed_results
                for dataset_id in result.changed_dataset_ids
            ))
            report = DashboardRefreshReport(
                outcome=_aggregate(typed_results),
                trigger=trigger,
                lane_results=typed_results,
                changed_dataset_ids=changed,
                started_at_utc=started.isoformat(),
                completed_at_utc=completed.isoformat(),
            )
            with self._state_lock:
                self._last_report = report
            return report
        finally:
            self._run_lock.release()


@dataclass(frozen=True)
class LocalPathStamp:
    path: str
    exists: bool
    size: int | None
    modified_ns: int | None


class DashboardLocalPoller:
    """Detect changed local artifacts by metadata only; never reads provider data."""

    def __init__(self, paths: Iterable[Path]) -> None:
        self._paths = tuple(dict.fromkeys(Path(path).resolve() for path in paths))
        self._previous: tuple[LocalPathStamp, ...] | None = None
        self._closed = False

    def close(self) -> None:
        self._closed = True

    def poll(self) -> tuple[LocalPathStamp, ...]:
        if self._closed:
            return ()
        current: list[LocalPathStamp] = []
        for path in self._paths:
            try:
                stat = path.stat()
                current.append(LocalPathStamp(str(path), True, stat.st_size, stat.st_mtime_ns))
            except FileNotFoundError:
                current.append(LocalPathStamp(str(path), False, None, None))
            except PermissionError:
                # ACL-protected paths stay visible as typed metadata-only blockers.
                current.append(LocalPathStamp(str(path), True, None, None))
        snapshot = tuple(current)
        if self._previous is None:
            self._previous = snapshot
            return ()
        previous_by_path = {item.path: item for item in self._previous}
        changed = tuple(item for item in snapshot if previous_by_path.get(item.path) != item)
        self._previous = snapshot
        return changed
