from __future__ import annotations

import argparse
from collections.abc import Callable
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from scripts.manual.pilot.pilot_bok_ecos_treasury import run_finality_observation
from stock_data.orchestration.bok_ecos_treasury_daily import (
    FinalityObservationAction,
    plan_finality_observation_occurrence,
)


STATE_RELATIVE = Path("data/state/bok_ecos_treasury_finality_observation.json")
LOG_RELATIVE = Path(
    "artifacts/scheduler_logs/STOCK_DATA_BOK_TREASURY_DAILY_last.json"
)
METADATA_RELATIVE = Path(
    "data/landing/diagnostics/bok_ecos_treasury_pilot/"
    "metadata_20260813T121302Z_c3273a9964264696b55827fbecc70880/"
    "metadata_summary.json"
)
METADATA_SHA256 = "c0174b89888fc986791d5abc4b5c6eb4d03911bfb9f0b7348d453422488d4372"
FIRST_RANGE_START_DATE = "20260813"


def _atomic_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _retained_batch_count(project_root: Path) -> int:
    path = project_root / STATE_RELATIVE
    if not path.exists():
        return 0
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("BOK finality state must be an object")
    batches = payload.get("batches")
    batch_count = payload.get("batch_count")
    if (
        payload.get("version") != 1
        or not isinstance(batches, list)
        or not isinstance(batch_count, int)
        or isinstance(batch_count, bool)
        or batch_count != len(batches)
    ):
        raise ValueError("BOK finality state identity or batch count differs")
    return batch_count


def _report(
    *, status: str, observation_status: str, observation_date_kst: str,
    batch_count_before: int, batch_count_after: int, api_calls: int,
    reason: str, result: dict[str, object] | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "status": status,
        "scheduler_process_status": "SUCCESS" if status == "PASS" else "FAIL",
        "operation": "BOK_ECOS_TREASURY_FINALITY_OBSERVATION",
        "observation_status": observation_status,
        "observation_date_kst": observation_date_kst,
        "batch_count_before": batch_count_before,
        "batch_count_after": batch_count_after,
        "api_calls": api_calls,
        "normalized_writes": 0,
        "reason": reason,
        "finished_at_utc": datetime.now(timezone.utc).isoformat(),
    }
    if result is not None:
        for field in ("selected_date", "comparison_status", "state_status"):
            value = result.get(field)
            if isinstance(value, str):
                payload[field] = value
    return payload


def run_scheduled_observation(
    project_root: Path,
    *,
    observation_time: datetime | None = None,
    operation: Callable[..., dict[str, object]] = run_finality_observation,
) -> dict[str, object]:
    """Run or pre-network no-op one bounded BOK finality occurrence."""

    root = Path(project_root).resolve()
    clock = observation_time or datetime.now(timezone.utc)
    before = _retained_batch_count(root)
    plan = plan_finality_observation_occurrence(
        observation_time=clock, retained_batch_count=before,
    )
    if plan.action is not FinalityObservationAction.OBSERVE_OR_REPLAY:
        report = _report(
            status="PASS",
            observation_status=plan.action.value,
            observation_date_kst=plan.observation_date_kst,
            batch_count_before=before,
            batch_count_after=before,
            api_calls=0,
            reason=plan.reason,
        )
        _atomic_json(root / LOG_RELATIVE, report)
        return report

    metadata = root / METADATA_RELATIVE
    if not metadata.is_file():
        raise FileNotFoundError(metadata)
    load_dotenv(root / ".env", override=False)
    result = operation(
        project_root=root,
        metadata_summary_path=metadata,
        approve_metadata_sha256=METADATA_SHA256,
        range_start_date=FIRST_RANGE_START_DATE if before == 0 else None,
        observation_kst=clock,
    )
    statistic_calls = result.get("statistic_search_calls")
    ui_calls = result.get("official_ui_calls")
    if (
        not isinstance(statistic_calls, int)
        or isinstance(statistic_calls, bool)
        or not 0 <= statistic_calls <= plan.max_statistic_search_calls
        or not isinstance(ui_calls, int)
        or isinstance(ui_calls, bool)
        or not 0 <= ui_calls <= plan.max_official_ui_calls
        or result.get("status") not in {
            "FINALITY_OBSERVATION_COMPLETE", "NOOP_ALREADY_SUCCEEDED",
        }
    ):
        raise ValueError("BOK finality operation returned an invalid terminal result")
    after = _retained_batch_count(root)
    if after < before or after > before + 1:
        raise ValueError("BOK finality batch count advanced outside the bounded scope")
    report = _report(
        status="PASS",
        observation_status=str(result["status"]),
        observation_date_kst=plan.observation_date_kst,
        batch_count_before=before,
        batch_count_after=after,
        api_calls=statistic_calls + ui_calls,
        reason=plan.reason,
        result=result,
    )
    _atomic_json(root / LOG_RELATIVE, report)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the bounded BOK ECOS Treasury finality observation."
    )
    parser.add_argument("--project-root", type=Path, default=ROOT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.project_root.resolve()
    try:
        report = run_scheduled_observation(root)
    except Exception as error:
        report = {
            "schema_version": 1,
            "status": "FAIL",
            "scheduler_process_status": "FAIL",
            "operation": "BOK_ECOS_TREASURY_FINALITY_OBSERVATION",
            "error_type": type(error).__name__,
            "api_calls": None,
            "normalized_writes": 0,
            "finished_at_utc": datetime.now(timezone.utc).isoformat(),
        }
        _atomic_json(root / LOG_RELATIVE, report)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
