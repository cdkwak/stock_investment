from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile

import pandas as pd

from stock_data.contracts.kbsec_snapshot import KBSEC_SNAPSHOT_CONTRACTS
from stock_data.providers.kbsec.client import KBSecClient
from stock_data.providers.kbsec.client import KBSecResponse
from stock_data.providers.kbsec.market_summary import normalize_market_summary
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic
from stock_data.validation.kbsec_snapshot import validate_kb_snapshot


def _write_landing_atomic(payload: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", suffix=".json.tmp", prefix="kbsec_", dir=path.parent,
                                     encoding="utf-8", delete=False) as handle:
        temporary = Path(handle.name); json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
    try:
        if json.loads(temporary.read_text(encoding="utf-8")) != payload: raise RuntimeError("KB landing read-back differs")
        temporary.replace(path)
    finally: temporary.unlink(missing_ok=True)


def collect_kb_market_summary(project_root: Path, *, client: KBSecClient | None = None,
                              collected_at: datetime | None = None) -> dict[str, int]:
    """Collect one IVSA0070 provisional snapshot; never writes official historical datasets."""
    observed = collected_at or datetime.now(timezone.utc)
    if observed.tzinfo is None: raise ValueError("collected_at must be timezone-aware")
    response = (client or KBSecClient()).market_summary()
    return store_kb_market_summary_response(project_root, response=response, collected_at=observed)


def store_kb_market_summary_response(project_root: Path, *, response: KBSecResponse,
                                     collected_at: datetime) -> dict[str, int]:
    """Persist an already fetched response without making another API request."""
    observed = collected_at
    if observed.tzinfo is None: raise ValueError("collected_at must be timezone-aware")
    stamp = observed.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    landing = {"collected_at": observed.isoformat(), "source": "kb_securities_open_api",
               "operation": "IVSA0070", "raw_response": response.raw_payload}
    _write_landing_atomic(landing, project_root / "data/landing/kbsec/IVSA0070" / f"{stamp}.json")
    frames = normalize_market_summary(response, collected_at=observed)
    contracts = {item.name: item for item in KBSEC_SNAPSHOT_CONTRACTS}
    counts = {}
    for name, incoming in frames.items():
        if incoming.empty:
            counts[name] = 0; continue
        contract = contracts[name]
        incoming = incoming[list(contract.column_names)].sort_values(list(contract.sort_key), kind="stable").reset_index(drop=True)
        validate_kb_snapshot(incoming, contract)
        root = project_root / "data/normalized" / name
        try: existing = read_dataset(root, contract, lambda frame: validate_kb_snapshot(frame, contract))
        except FileNotFoundError: existing = None
        combined = incoming.copy() if existing is None else pd.concat([existing, incoming], ignore_index=True)
        combined = combined.drop_duplicates(list(contract.primary_key), keep="last").sort_values(list(contract.sort_key), kind="stable").reset_index(drop=True)
        write_dataset_atomic(combined, root, contract, lambda frame: validate_kb_snapshot(frame, contract))
        counts[name] = len(incoming)
    return counts
