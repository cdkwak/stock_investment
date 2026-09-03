from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path

from dotenv import dotenv_values

from stock_data.orchestration.kb_account_snapshot import (
    KBAccountRefreshResult,
    KBAccountSnapshotCoordinator,
)
from stock_data.orchestration.account_privacy import (
    account_snapshot_lifecycle_lock,
    retain_positions_history,
)
from stock_data.orchestration.toss_account_snapshot import AccountRefreshTrigger
from stock_data.providers.kbsec.client import KBSecClient, KBSecResponse


KBSEC_BASE_URL_ENV = "KBSEC_BASE_URL"
KBSEC_APP_KEY_ENV = "KBSEC_APP_KEY"
KBSEC_APP_SECRET_ENV = "KBSEC_APP_SECRET"
KBSEC_ACCOUNT_REQUIRED_ENV_NAMES = (
    KBSEC_BASE_URL_ENV,
    KBSEC_APP_KEY_ENV,
    KBSEC_APP_SECRET_ENV,
)

# Current Data authority binds exact POST /api/v1/ssqm2952, App Key/Token
# account permission, and the three existing desktop credential names.  The
# runtime remains manual-click-only and construction/startup stay API zero.
KBSEC_ACCOUNT_LIVE_AUTHORIZED = True


class KBSecAccountRuntimeState(str, Enum):
    ENABLED = "ENABLED"
    NOT_AVAILABLE_MISSING_CONFIG = "NOT_AVAILABLE_MISSING_CONFIG"
    NOT_AVAILABLE_INVALID_CONFIG = "NOT_AVAILABLE_INVALID_CONFIG"


@dataclass(frozen=True)
class KBSecAccountRuntimeWiring:
    state: KBSecAccountRuntimeState
    refresher: Callable[[AccountRefreshTrigger], KBAccountRefreshResult] | None
    missing_names: tuple[str, ...] = ()
    reason: str | None = None

    @property
    def enabled(self) -> bool:
        return self.state is KBSecAccountRuntimeState.ENABLED


def load_kbsec_account_environment(
    project_root: Path, process_environment: Mapping[str, str],
) -> dict[str, str]:
    """Load only the three approved KB settings without mutating the process."""

    file_values = dotenv_values(
        project_root.resolve() / ".env", encoding="utf-8", interpolate=False,
    )
    result: dict[str, str] = {}
    for name in KBSEC_ACCOUNT_REQUIRED_ENV_NAMES:
        process_value = process_environment.get(name)
        selected = process_value if process_value is not None else file_values.get(name)
        result[name] = selected if isinstance(selected, str) else ""
    return result


def build_kbsec_account_runtime(
    project_root: Path,
    environment: Mapping[str, str],
    *,
    client_factory: Callable[..., KBSecClient] = KBSecClient,
) -> KBSecAccountRuntimeWiring:
    """Build a manual/periodic KB account refresher without making a call."""

    if not KBSEC_ACCOUNT_LIVE_AUTHORIZED:
        return KBSecAccountRuntimeWiring(
            state=KBSecAccountRuntimeState.NOT_AVAILABLE_INVALID_CONFIG,
            refresher=None,
            reason="LIVE_EXECUTION_AUTHORITY_REQUIRED",
        )

    values = {
        name: environment.get(name, "")
        for name in KBSEC_ACCOUNT_REQUIRED_ENV_NAMES
    }
    missing = tuple(
        name
        for name in KBSEC_ACCOUNT_REQUIRED_ENV_NAMES
        if not isinstance(values[name], str) or not values[name].strip()
    )
    if missing:
        return KBSecAccountRuntimeWiring(
            state=KBSecAccountRuntimeState.NOT_AVAILABLE_MISSING_CONFIG,
            refresher=None,
            missing_names=missing,
            reason="RUNTIME_CONFIG_REQUIRED",
        )
    try:
        client = client_factory(
            base_url=values[KBSEC_BASE_URL_ENV],
            app_key=values[KBSEC_APP_KEY_ENV],
            app_secret=values[KBSEC_APP_SECRET_ENV],
        )
    except Exception:
        return KBSecAccountRuntimeWiring(
            state=KBSecAccountRuntimeState.NOT_AVAILABLE_INVALID_CONFIG,
            refresher=None,
            reason="CLIENT_INITIALIZATION_FAILED",
        )

    def response_supplier() -> dict[str, object]:
        response = client.account_snapshot()
        if type(response) is not KBSecResponse:
            raise TypeError("KB account client result differs")
        return response.raw_payload

    coordinator = KBAccountSnapshotCoordinator(
        project_root=project_root,
        response_supplier=response_supplier,
    )

    def refresh(trigger: AccountRefreshTrigger) -> KBAccountRefreshResult:
        if trigger not in {
            AccountRefreshTrigger.MANUAL, AccountRefreshTrigger.PERIODIC,
        }:
            return KBAccountRefreshResult(
                status="FAILED_PRESERVED_PRIOR",
                supplier_calls=0,
                reason="MANUAL_TRIGGER_REQUIRED",
            )
        root = project_root.resolve()
        try:
            with account_snapshot_lifecycle_lock(root):
                result = coordinator.refresh_manual()
                if (
                    result.status == "SUCCEEDED"
                    and result.supplier_calls == 1
                    and result.snapshot_path
                    == "data/local/account_snapshots/kb_self.json"
                ):
                    snapshot = json.loads(
                        (root / result.snapshot_path).read_text(encoding="utf-8")
                    )
                    retain_positions_history(root, "kb_self", snapshot)
                return result
        except TimeoutError:
            return KBAccountRefreshResult(
                status="FAILED_PRESERVED_PRIOR",
                supplier_calls=0,
                reason="KB_ACCOUNT_LOCK_TIMEOUT",
            )

    return KBSecAccountRuntimeWiring(
        state=KBSecAccountRuntimeState.ENABLED,
        refresher=refresh,
    )


__all__ = [
    "KBSEC_ACCOUNT_LIVE_AUTHORIZED",
    "KBSEC_ACCOUNT_REQUIRED_ENV_NAMES",
    "KBSEC_APP_KEY_ENV",
    "KBSEC_APP_SECRET_ENV",
    "KBSEC_BASE_URL_ENV",
    "KBSecAccountRuntimeState",
    "KBSecAccountRuntimeWiring",
    "build_kbsec_account_runtime",
    "load_kbsec_account_environment",
]
