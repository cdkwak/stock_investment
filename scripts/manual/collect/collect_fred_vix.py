"""One-call, Landing-first full-history FRED VIXCLS collector."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile

import requests

from stock_data.contracts.global_market import FRED_VIX_DAILY
from stock_data.providers.fred import fetch_series
from stock_data.storage.contract_parquet import write_dataset_atomic
from stock_data.validation.global_market import validate_fred


def collect(project_root: Path, *, session=None) -> dict:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    landing = project_root / "data/landing/fred/vixcls" / run_id
    output = project_root / "data/normalized" / FRED_VIX_DAILY.name
    state = project_root / "data/state" / f"{FRED_VIX_DAILY.name}.json"
    frame = fetch_series("VIXCLS", session=session or requests, capture_root=landing)
    validate_fred(frame)
    write_dataset_atomic(frame, output, FRED_VIX_DAILY, validate_fred)
    payload = {"dataset": FRED_VIX_DAILY.name, "series_id": "VIXCLS", "source": "FRED", "source_lineage": "FRED_VIXCLS_DISTINCT_FROM_DIRECT_CBOE", "status": "ARTIFACT_COMPLETE_PIT_LIMITED", "business_calls": 1, "retry_count": 0, "rows": len(frame), "coverage_start": str(frame.date.min()), "coverage_end": str(frame.date.max()), "completed_at_utc": datetime.now(timezone.utc).isoformat()}
    state.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{state.name}.", dir=state.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2); stream.write("\n"); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, state)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(); parser.add_argument("--project-root", type=Path, required=True); parser.add_argument("--confirm-live", action="store_true")
    args = parser.parse_args()
    if not args.confirm_live: raise SystemExit("explicit live confirmation required")
    print(json.dumps(collect(args.project_root.resolve()), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__": raise SystemExit(main())
