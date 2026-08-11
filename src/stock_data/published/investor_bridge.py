from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile

import pandas as pd

from stock_data.contracts.investor_bridge import KR_MARKET_INVESTOR_NET_PURCHASE_BRIDGE_DAILY
from stock_data.contracts.legacy_market_investor import KR_MARKET_INVESTOR_NET_PURCHASE_DAILY
from stock_data.contracts.tossinvest_historical import KR_MARKET_INVESTOR_TRADING_DAILY
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic
from stock_data.validation.investor_bridge import validate_investor_bridge
from stock_data.validation.legacy_market_investor import validate_legacy_market_investor_net_purchase
from stock_data.validation.tossinvest_historical import validate_toss_historical


EXPECTED_LEGACY_ROWS = 3_834
EXPECTED_TOSS_ROWS = 5_946


def _manifest(root: Path, project_root: Path) -> list[dict[str, object]]:
    result = []
    for path in sorted(root.rglob("*.parquet")):
        result.append({
            "path": path.relative_to(project_root).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
    return result


def _write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", suffix=".tmp", prefix=path.stem + "_",
        dir=path.parent, delete=False,
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
        temporary = Path(handle.name)
    temporary.replace(path)


def build_investor_bridge(*, project_root: Path) -> dict:
    legacy_root = project_root / "data/normalized/kr_market_investor_net_purchase_daily"
    toss_root = project_root / "data/normalized/kr_market_investor_trading_daily"
    legacy = read_dataset(legacy_root, KR_MARKET_INVESTOR_NET_PURCHASE_DAILY, validate_legacy_market_investor_net_purchase)
    toss = read_dataset(
        toss_root, KR_MARKET_INVESTOR_TRADING_DAILY,
        lambda frame: validate_toss_historical(frame, KR_MARKET_INVESTOR_TRADING_DAILY),
    )
    if len(legacy) != EXPECTED_LEGACY_ROWS or len(toss) != EXPECTED_TOSS_ROWS:
        raise ValueError("investor bridge source row count changed")

    legacy_bridge = pd.DataFrame({
        "date": legacy["date"], "market": legacy["market"],
        "institution_net_purchase": legacy["institution_net_buy"],
        "other_corporation_net_purchase": legacy["other_corporation_net_buy"],
        "individual_net_purchase": legacy["individual_net_buy"],
        "foreign_net_purchase": legacy["foreign_net_buy"],
        "total_net_purchase": legacy["total_net_buy"],
        "value_unit": "unit_unknown",
        "source_dataset": KR_MARKET_INVESTOR_NET_PURCHASE_DAILY.name,
        "source_provider": legacy["source"],
        "source_operation": legacy["source_operation"],
        "provider_segment": "legacy_pre_a001",
        "availability_date": None,
        "predictive_use_status": "blocked_unknown_unit_and_availability",
    })
    components = {
        "institution_net_purchase": toss["institution_buy_amount"] - toss["institution_sell_amount"],
        "other_corporation_net_purchase": toss["other_corporation_buy_amount"] - toss["other_corporation_sell_amount"],
        "individual_net_purchase": toss["individual_buy_amount"] - toss["individual_sell_amount"],
        "foreign_net_purchase": toss["foreigner_buy_amount"] - toss["foreigner_sell_amount"],
    }
    toss_bridge = pd.DataFrame({
        "date": toss["date"], "market": toss["market"], **components,
        "total_net_purchase": sum(components.values()),
        "value_unit": "KRW",
        "source_dataset": KR_MARKET_INVESTOR_TRADING_DAILY.name,
        "source_provider": toss["source"],
        "source_operation": toss["source_operation"],
        "provider_segment": "toss_a001",
        "availability_date": toss["availability_date"],
        "predictive_use_status": "eligible_from_availability_date",
    })
    bridge = pd.concat([legacy_bridge, toss_bridge], ignore_index=True)
    bridge = bridge[list(KR_MARKET_INVESTOR_NET_PURCHASE_BRIDGE_DAILY.column_names)]
    bridge = bridge.sort_values(list(KR_MARKET_INVESTOR_NET_PURCHASE_BRIDGE_DAILY.sort_key), kind="stable").reset_index(drop=True)
    validate_investor_bridge(bridge)

    target = project_root / "data/published/kr_market_investor_net_purchase_bridge_daily"
    write_dataset_atomic(bridge, target, KR_MARKET_INVESTOR_NET_PURCHASE_BRIDGE_DAILY, validate_investor_bridge)
    restored = read_dataset(target, KR_MARKET_INVESTOR_NET_PURCHASE_BRIDGE_DAILY, validate_investor_bridge)
    if not restored.equals(bridge):
        raise ValueError("investor bridge read-back differs")
    payload = {
        "task_id": "D001", "status": "complete", "api_calls": 0,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset": KR_MARKET_INVESTOR_NET_PURCHASE_BRIDGE_DAILY.name,
        "rows": len(bridge), "coverage_start": bridge["date"].min(), "coverage_end": bridge["date"].max(),
        "markets": {str(key): int(value) for key, value in bridge["market"].value_counts().sort_index().items()},
        "provider_segments": {str(key): int(value) for key, value in bridge["provider_segment"].value_counts().sort_index().items()},
        "limitations": {
            "legacy_value_unit": "unit_unknown", "legacy_availability_date": None,
            "cross_segment_numeric_comparison": "prohibited without an explicit unit bridge",
        },
        "source_manifests": {"legacy": _manifest(legacy_root, project_root), "toss": _manifest(toss_root, project_root)},
        "output_manifest": _manifest(target, project_root),
    }
    _write_json_atomic(project_root / "data/state/investor_net_purchase_bridge.json", payload)
    return payload
