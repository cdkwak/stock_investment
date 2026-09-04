"""Server-rendered data-health page context from retained local artifacts."""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from stock_data.gui.health_service import (
    DailyHealthArtifactService,
    summarize_health_artifact,
)
from stock_web.api.fmt import format_kst

FILTERS = ("OPERATIONAL", "DAILY", "BLOCKED", "ALL")
FILTER_LABELS = {
    "OPERATIONAL": "운영 데이터", "DAILY": "일별", "BLOCKED": "차단",
    "ALL": "전체", "UNKNOWN": "미확인",
}
FRESHNESS = {
    "CURRENT": ("정상", "current"),
    "LATE": ("지연", "late"),
    "FAILED": ("실패", "failed"),
    "PRESERVED": ("수동/보존", "preserved"),
    "REFERENCE": ("참고", "reference"),
}
OPERATIONAL = {
    "READY": "운영 가능",
    "READY_WITH_LIMITS": "제한 운영",
    "READY_WITH_FINALITY_GATE": "확정 대기",
    "IMPLEMENTATION_READY": "구현 준비",
    "MANUAL_ONLY": "수동",
    "BLOCKED": "차단",
    "NOT_APPLICABLE": "해당 없음",
    "UNKNOWN": "미확인",
}
BLOCKERS = {
    "N/A": "없음", "SOURCE_CONTRACT": "원천 계약", "FINALITY": "확정성",
    "PERMISSION": "권한", "IMPLEMENTATION": "구현", "ACL": "접근 권한",
    "SEMANTICS": "의미 검증", "PIT_ONLY": "시점 재현성", "INTENTIONAL": "의도적 중단",
}
AUTOMATION = {
    "AUTO_ELIGIBLE": "자동",
    "DEPENDENCY_DRIVEN": "의존 실행",
    "MANUAL_GATE": "수동 확인",
    "NO_REFRESH": "갱신 없음",
    "RESEARCH_ONLY": "연구 전용",
    "DISABLED": "꺼짐",
    "ENABLED": "켜짐",
}
CADENCE = {
    "DAILY": "일별", "INTRADAY": "장중", "WEEKLY": "주별",
    "SNAPSHOT": "스냅샷", "EVENT_DRIVEN": "이벤트",
    "MONTHLY": "월별", "ON_DEMAND": "요청 시", "NONE": "해당 없음",
}
ROLE = {
    "DERIVED": "파생 데이터", "HISTORICAL_SEGMENT": "과거 구간",
    "PUBLISHED_BRIDGE": "공개 연결", "RAW_OBSERVATION": "원시 관측",
    "SNAPSHOT": "현재 스냅샷", "SOURCE": "원천 데이터",
    "SOURCE_OBSERVATION": "원천 관측",
}
WEB_PRESERVED_DATASETS = {
    "research_target_price_consensus": "종목 상세 화면에서 보존 참고값으로 사용",
}


def _enum(raw: object, labels: dict[str, str]) -> dict[str, str]:
    value = str(raw or "UNKNOWN")
    return {
        "raw": value,
        "label": labels.get(value, "미확인"),
    }


def _automation(raw: object) -> dict[str, str]:
    value = str(raw or "UNKNOWN")
    label = " / ".join(
        AUTOMATION.get(part.strip(), "미확인")
        for part in value.split("/")
    )
    return {"raw": value, "label": label}


def _dataset_subject(dataset: str, role: str) -> str:
    if dataset.startswith("kr_index"):
        return "KR 지수"
    if "kospi200" in dataset:
        return "KOSPI200"
    if dataset.startswith("kr_equity"):
        return "KR 종목"
    if dataset.startswith("kr_market"):
        return "KR 시장"
    if "treasury" in dataset:
        return "미 국채"
    if "usd_fx" in dataset:
        return "환율"
    if "vix" in dataset:
        return "변동성"
    if dataset.startswith("global_index"):
        return "글로벌 지수"
    if dataset.startswith("global_etf"):
        return "글로벌 ETF"
    if "commodity" in dataset:
        return "원자재·선물"
    return ROLE.get(role, role)


def _health_row(row: object) -> dict[str, object]:
    freshness_raw = str(getattr(row, "display_status"))
    freshness_label, freshness_class = FRESHNESS.get(
        freshness_raw, (freshness_raw, "unknown"),
    )
    pending_until = getattr(row, "pending_until", None)
    if freshness_raw == "CURRENT" and pending_until:
        freshness_label = f"대기 {pending_until}"
    cadence = str(getattr(row, "cadence"))
    role = str(getattr(row, "role"))
    dataset = str(getattr(row, "dataset"))
    return {
        "dataset": dataset,
        "description": f"{CADENCE.get(cadence, cadence)} · {_dataset_subject(dataset, role)}",
        "latest": _display_date(getattr(row, "latest")),
        "expected": _display_date(getattr(row, "expected")),
        "freshness": {"raw": freshness_raw, "label": freshness_label, "class": freshness_class},
        "operational": _enum(getattr(row, "operational"), OPERATIONAL),
        "blocker": _enum(getattr(row, "blocker"), BLOCKERS),
        "automation": _automation(getattr(row, "automation")),
        "display_reason": str(getattr(row, "display_reason")),
    }


def _display_date(raw: object) -> str:
    value = str(raw or "N/A")
    return "해당 없음" if value == "N/A" else value


def _receipt_time(payload: dict[str, object]) -> str:
    value = (
        payload.get("finished_at_utc") or payload.get("finished_at")
        or payload.get("completed_at") or payload.get("observed_at_utc")
        or payload.get("started_at_utc") or payload.get("scheduled_for")
    )
    return str(value or "")


def _sort_time(value: str) -> float:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except ValueError:
        return float("-inf")


def _receipt_failed(payload: dict[str, object]) -> bool:
    statuses = " ".join(str(payload.get(key) or "") for key in (
        "scheduler_process_status", "status", "observation_status",
    )).upper()
    return any(token in statuses for token in ("FAIL", "ERROR", "BLOCKED"))


def _result_code(raw: object) -> dict[str, str]:
    value = str(raw if raw is not None else "UNKNOWN")
    upper = value.upper()
    if value == "0" or upper in {"SUCCESS", "SUCCEEDED", "PASS", "OK", "COMPLETED"}:
        label = "성공"
    elif value not in {"—", ""} and (
        value.lstrip("-").isdigit() or any(token in upper for token in ("FAIL", "ERROR", "BLOCKED"))
    ):
        label = f"실패 (코드 {value})"
    elif value in {"—", ""}:
        label = "—"
    else:
        label = FILTER_LABELS.get(upper, "미확인")
    return {"raw": value, "label": label}


def load_scheduler_receipts(
    project_root: Path, *, now: datetime | None = None,
) -> list[dict[str, object]]:
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None or reference.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    cutoff = reference.astimezone(timezone.utc) - timedelta(days=7)
    rows: list[dict[str, object]] = []
    for path in (project_root / "artifacts/scheduler_logs").glob("*_last.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        finished = _receipt_time(payload)
        raw_status = str(
            payload.get("scheduler_process_status") or payload.get("status")
            or payload.get("observation_status") or "UNKNOWN"
        )
        failed = _receipt_failed(payload)
        result_code = next((
            payload[key] for key in (
                "terminal_exit_code", "result_code", "exit_code", "return_code",
            ) if key in payload
        ), "—")
        sort_time = _sort_time(finished)
        rows.append({
            "task": str(
                payload.get("task_name") or payload.get("lane")
                or payload.get("operation") or path.stem.removesuffix("_last")
            ),
            "status": {"raw": raw_status, "label": "실패" if failed else "완료"},
            "finished": finished,
            "finished_label": format_kst(finished),
            "api_calls": payload.get("api_calls", "—"),
            "result_code": result_code,
            "result_code_display": _result_code(result_code),
            "has_result_code": str(result_code) not in {"—", ""},
            "failed": failed,
            "older_than_7_days": sort_time == float("-inf") or sort_time < cutoff.timestamp(),
        })
    rows.sort(key=lambda row: (
        bool(row["older_than_7_days"]), not bool(row["failed"]),
        -_sort_time(str(row["finished"])),
    ))
    return rows


def load_credential_expiries(project_root: Path, *, today: date | None = None) -> list[dict[str, object]]:
    """Expiry dates recorded in .env as ``<NAME>_EXPIRES_AT`` (names and dates only; secrets are
    never read into the payload). Warns 14 days ahead so keys can be renewed before a lane fails."""
    env_file = project_root / ".env"
    if not env_file.is_file():
        return []
    today = today or datetime.now(ZoneInfo("Asia/Seoul")).date()
    rows: list[dict[str, object]] = []
    for raw in env_file.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key.upper().endswith("_EXPIRES_AT"):
            continue
        text = value.strip().strip("\"'")
        try:
            expires = date.fromisoformat(text[:10])
        except ValueError:
            rows.append({"name": key[: -len("_EXPIRES_AT")], "expires": text or "미기록", "days_left": None,
                         "status": "unknown", "label": "형식 확인 필요"})
            continue
        days_left = (expires - today).days
        if days_left < 0:
            status, label = "expired", "만료됨"
        elif days_left <= 14:
            status, label = "soon", f"{days_left}일 남음"
        else:
            status, label = "ok", f"{days_left}일 남음"
        rows.append({"name": key[: -len("_EXPIRES_AT")], "expires": expires.isoformat(),
                     "days_left": days_left, "status": status, "label": label})
    rows.sort(key=lambda row: (row["days_left"] is None, row["days_left"] if row["days_left"] is not None else 0))
    return rows


def build_data_page_context(project_root: Path, status_filter: str) -> dict[str, object]:
    latest_artifact = project_root / "artifacts/daily_health/universe_data_v2_latest.json"
    service = DailyHealthArtifactService(
        project_root, latest_artifact if latest_artifact.is_file() else None,
    )
    view = service.load()
    selected = status_filter if status_filter in FILTERS else "OPERATIONAL"
    selected_rows = service.filter_rows(view.rows, selected) if view.artifact_state == "READY" else ()
    projected = tuple(_health_row(row) for row in selected_rows)
    groups = []
    for raw, (label, css_class) in FRESHNESS.items():
        grouped = tuple(row for row in projected if row["freshness"]["raw"] == raw)
        if grouped:
            groups.append({"raw": raw, "label": label, "class": css_class, "rows": grouped})
    freshness_counts = [
        {"raw": raw, "label": label, "class": css_class,
         "count": sum(row.display_status == raw for row in view.rows)}
        for raw, (label, css_class) in FRESHNESS.items()
    ]
    receipts = load_scheduler_receipts(project_root)
    show_result_code = sum(bool(row["has_result_code"]) for row in receipts) >= 2
    return {
        "filters": FILTERS,
        "filter_labels": FILTER_LABELS,
        "selected_filter": selected,
        "health_state": view.artifact_state,
        "health_warning": view.warning,
        "unregistered_dataset_ids": view.unregistered_dataset_ids,
        "health_summary": summarize_health_artifact(view),
        "freshness_counts": freshness_counts,
        "health_groups": groups,
        "receipts": tuple(row for row in receipts if not row["older_than_7_days"]),
        "older_receipts": tuple(row for row in receipts if row["older_than_7_days"]),
        "show_result_code": show_result_code,
        "web_preserved_datasets": tuple(
            {"dataset": dataset, "reason": reason}
            for dataset, reason in WEB_PRESERVED_DATASETS.items()
            if dataset in {row.dataset for row in view.rows}
        ),
        "credential_expiries": load_credential_expiries(project_root),
    }


__all__ = ["FILTERS", "build_data_page_context", "load_credential_expiries", "load_scheduler_receipts"]
