from __future__ import annotations

from datetime import date, datetime, timezone
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any

import pandas as pd

from stock_data.contracts.investor_bridge import (
    KR_MARKET_INVESTOR_NET_PURCHASE_BRIDGE_DAILY,
)
from stock_data.contracts.legacy_market_investor import (
    KR_MARKET_INVESTOR_NET_PURCHASE_DAILY,
)
from stock_data.contracts.tossinvest_historical import (
    KR_MARKET_INVESTOR_TRADING_DAILY,
)
from stock_data.pipelines.tossinvest_historical import _atomic_json, _extract
from stock_data.providers.tossinvest import TossInvestClient, normalize_market_investor
from stock_data.providers.tossinvest.historical import MARKET_INVESTOR_OPERATION
from stock_data.published.investor_bridge import (
    compose_investor_bridge,
    write_investor_bridge_state,
)
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic
from stock_data.validation.investor_bridge import validate_investor_bridge
from stock_data.validation.legacy_market_investor import (
    validate_legacy_market_investor_net_purchase,
)
from stock_data.validation.tossinvest_historical import validate_toss_historical


MARKETS = ("KOSPI", "KOSDAQ")


def _read_toss(root: Path) -> pd.DataFrame:
    return read_dataset(
        root,
        KR_MARKET_INVESTOR_TRADING_DAILY,
        lambda frame: validate_toss_historical(
            frame, KR_MARKET_INVESTOR_TRADING_DAILY
        ),
    )


def _read_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"dataset": KR_MARKET_INVESTOR_TRADING_DAILY.name, "completed_dates": []}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("dataset") != KR_MARKET_INVESTOR_TRADING_DAILY.name:
        raise ValueError("Toss investor daily state dataset mismatch")
    return payload


def _verify_promoted_date(project_root: Path, intended_date: str) -> None:
    source = _read_toss(
        project_root / "data/normalized" / KR_MARKET_INVESTOR_TRADING_DAILY.name
    )
    source_rows = source.loc[source["date"].astype(str).eq(intended_date)]
    if set(source_rows["market"].astype(str)) != set(MARKETS) or len(source_rows) != 2:
        raise RuntimeError("completed Toss investor date is missing from normalized data")
    bridge = read_dataset(
        project_root
        / "data/published"
        / KR_MARKET_INVESTOR_NET_PURCHASE_BRIDGE_DAILY.name,
        KR_MARKET_INVESTOR_NET_PURCHASE_BRIDGE_DAILY,
        validate_investor_bridge,
    )
    bridge_rows = bridge.loc[bridge["date"].astype(str).eq(intended_date)]
    if set(bridge_rows["market"].astype(str)) != set(MARKETS) or len(bridge_rows) != 2:
        raise RuntimeError("completed Toss investor date is missing from published bridge")


def is_toss_market_investor_date_complete(
    project_root: Path, intended_date: str | date,
) -> bool:
    """Verify an idempotent completion without loading credentials or calling Toss."""
    target_date = pd.Timestamp(intended_date).date().isoformat()
    state = _read_state(project_root / "data/state/toss_market_investor_daily.json")
    if target_date not in set(map(str, state.get("completed_dates", []))):
        return False
    _verify_promoted_date(project_root, target_date)
    return True


def refresh_toss_market_investor_daily(
    project_root: Path,
    *,
    intended_date: str | date,
    client: TossInvestClient | None = None,
) -> dict[str, Any]:
    """Refresh one finalized date with two bounded market calls and joint promotion."""
    target_date = pd.Timestamp(intended_date).date().isoformat()
    state_path = project_root / "data/state/toss_market_investor_daily.json"
    state = _read_state(state_path)
    if target_date in set(map(str, state.get("completed_dates", []))):
        _verify_promoted_date(project_root, target_date)
        return {
            "status": "already_complete",
            "intended_date": target_date,
            "token_calls": 0,
            "market_calls": 0,
            "promoted_rows": 0,
        }
    if client is None:
        raise ValueError("Toss client is required for an uncompleted date")

    initial_token_calls = client.token_request_count
    initial_market_calls = client.market_request_count
    normalized_rows: list[pd.DataFrame] = []
    landing_files: list[str] = []
    for market in MARKETS:
        response = client.get_market_data(
            f"/api/v1/market-indicators/{market}/investor-trading",
            params={"interval": "1d", "count": 100},
        )
        observed = datetime.now(timezone.utc)
        relative = (
            Path("data/landing/tossinvest")
            / MARKET_INVESTOR_OPERATION
            / market
            / f"daily_{target_date}_{observed.strftime('%Y%m%dT%H%M%S%fZ')}.json"
        )
        _atomic_json(
            project_root / relative,
            {
                "collected_at": observed.isoformat(),
                "source": "tossinvest_open_api",
                "operation": MARKET_INVESTOR_OPERATION,
                "target": market,
                "intended_date": target_date,
                "raw_response": response.payload,
            },
        )
        landing_files.append(relative.as_posix())
        records, _ = _extract(response.payload, "records", "nextUntil")
        normalized = normalize_market_investor(
            records, market=market, collected_at=observed
        )
        exact = normalized.loc[normalized["date"].astype(str).eq(target_date)].copy()
        if len(exact) != 1:
            raise RuntimeError(
                f"Toss {market} investor response does not contain exactly one intended date"
            )
        normalized_rows.append(exact)

    incoming = pd.concat(normalized_rows, ignore_index=True)
    incoming = incoming.sort_values(
        list(KR_MARKET_INVESTOR_TRADING_DAILY.sort_key), kind="stable"
    ).reset_index(drop=True)
    validate_toss_historical(incoming, KR_MARKET_INVESTOR_TRADING_DAILY)

    source_live = (
        project_root / "data/normalized" / KR_MARKET_INVESTOR_TRADING_DAILY.name
    )
    bridge_live = (
        project_root
        / "data/published"
        / KR_MARKET_INVESTOR_NET_PURCHASE_BRIDGE_DAILY.name
    )
    existing = _read_toss(source_live)
    combined = pd.concat([existing, incoming], ignore_index=True)
    combined = combined.drop_duplicates(
        list(KR_MARKET_INVESTOR_TRADING_DAILY.primary_key), keep="last"
    )
    combined = combined.sort_values(
        list(KR_MARKET_INVESTOR_TRADING_DAILY.sort_key), kind="stable"
    ).reset_index(drop=True)
    validate_toss_historical(combined, KR_MARKET_INVESTOR_TRADING_DAILY)
    legacy = read_dataset(
        project_root
        / "data/normalized"
        / KR_MARKET_INVESTOR_NET_PURCHASE_DAILY.name,
        KR_MARKET_INVESTOR_NET_PURCHASE_DAILY,
        validate_legacy_market_investor_net_purchase,
    )
    bridge = compose_investor_bridge(legacy, combined)
    existing_bridge = read_dataset(
        bridge_live,
        KR_MARKET_INVESTOR_NET_PURCHASE_BRIDGE_DAILY,
        validate_investor_bridge,
    )

    staging_parent = project_root / "data/staging"
    staging_parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="toss_investor_daily_", dir=staging_parent))
    source_stage = staging / "source"
    bridge_stage = staging / "bridge"
    source_promoted = False
    bridge_promoted = False
    try:
        write_dataset_atomic(
            combined,
            source_stage,
            KR_MARKET_INVESTOR_TRADING_DAILY,
            lambda frame: validate_toss_historical(
                frame, KR_MARKET_INVESTOR_TRADING_DAILY
            ),
        )
        write_dataset_atomic(
            bridge,
            bridge_stage,
            KR_MARKET_INVESTOR_NET_PURCHASE_BRIDGE_DAILY,
            validate_investor_bridge,
        )
        if not _read_toss(source_stage).equals(combined):
            raise RuntimeError("staged Toss investor data differs")
        staged_bridge = read_dataset(
            bridge_stage,
            KR_MARKET_INVESTOR_NET_PURCHASE_BRIDGE_DAILY,
            validate_investor_bridge,
        )
        if not staged_bridge.equals(bridge):
            raise RuntimeError("staged investor bridge differs")

        # Keep the established live roots in place so their Windows ACLs and
        # inherited access remain unchanged. Partition files are replaced
        # atomically by the contract writer.
        write_dataset_atomic(
            combined,
            source_live,
            KR_MARKET_INVESTOR_TRADING_DAILY,
            lambda frame: validate_toss_historical(
                frame, KR_MARKET_INVESTOR_TRADING_DAILY
            ),
        )
        source_promoted = True
        write_dataset_atomic(
            bridge,
            bridge_live,
            KR_MARKET_INVESTOR_NET_PURCHASE_BRIDGE_DAILY,
            validate_investor_bridge,
        )
        bridge_promoted = True
        _verify_promoted_date(project_root, target_date)
        write_investor_bridge_state(project_root=project_root, bridge=bridge)
    except Exception:
        if bridge_promoted:
            write_dataset_atomic(
                existing_bridge,
                bridge_live,
                KR_MARKET_INVESTOR_NET_PURCHASE_BRIDGE_DAILY,
                validate_investor_bridge,
            )
        if source_promoted:
            write_dataset_atomic(
                existing,
                source_live,
                KR_MARKET_INVESTOR_TRADING_DAILY,
                lambda frame: validate_toss_historical(
                    frame, KR_MARKET_INVESTOR_TRADING_DAILY
                ),
            )
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)

    state["completed_dates"] = sorted(
        set(map(str, state.get("completed_dates", []))) | {target_date}
    )
    state.update(
        {
            "status": "complete",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "latest_date": target_date,
            "landing_files": landing_files,
            "token_calls": client.token_request_count - initial_token_calls,
            "market_calls": client.market_request_count - initial_market_calls,
        }
    )
    _atomic_json(state_path, state)
    return {
        "status": "complete",
        "intended_date": target_date,
        "token_calls": client.token_request_count - initial_token_calls,
        "market_calls": client.market_request_count - initial_market_calls,
        "promoted_rows": len(incoming),
        "source_rows": len(combined),
        "bridge_rows": len(bridge),
    }


__all__ = [
    "is_toss_market_investor_date_complete", "refresh_toss_market_investor_daily",
]
