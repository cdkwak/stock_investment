"""Server-rendered data-health page context from retained local artifacts."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from stock_data.gui.health_service import (
    DailyHealthArtifactService,
    summarize_health_artifact,
)

FILTERS = ("OPERATIONAL", "DAILY", "BLOCKED", "ALL")


def _receipt_time(payload: dict[str, object]) -> str:
    value = (
        payload.get("finished_at_utc") or payload.get("finished_at")
        or payload.get("completed_at") or payload.get("observed_at_utc")
    )
    return str(value or "미상")


def _sort_time(value: str) -> float:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except ValueError:
        return float("-inf")


def load_scheduler_receipts(project_root: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for path in (project_root / "artifacts/scheduler_logs").glob("*_last.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        finished = _receipt_time(payload)
        rows.append({
            "task": str(
                payload.get("task_name") or payload.get("lane")
                or payload.get("operation") or path.stem.removesuffix("_last")
            ),
            "status": str(
                payload.get("scheduler_process_status") or payload.get("status")
                or payload.get("observation_status") or "UNKNOWN"
            ),
            "finished": finished,
            "api_calls": payload.get("api_calls", "—"),
        })
    rows.sort(key=lambda row: _sort_time(str(row["finished"])), reverse=True)
    return rows


def build_data_page_context(project_root: Path, status_filter: str) -> dict[str, object]:
    service = DailyHealthArtifactService(project_root)
    view = service.load()
    selected = status_filter if status_filter in FILTERS else "OPERATIONAL"
    rows = service.filter_rows(view.rows, selected) if view.artifact_state == "READY" else ()
    summary = summarize_health_artifact(view)
    return {
        "filters": FILTERS,
        "selected_filter": selected,
        "health_state": view.artifact_state,
        "health_warning": view.warning,
        "health_summary": summary,
        "rows": rows,
        "receipts": load_scheduler_receipts(project_root),
    }


__all__ = ["FILTERS", "build_data_page_context", "load_scheduler_receipts"]
