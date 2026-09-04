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
from stock_data.orchestration.exchange_calendar import (
    ExchangeMarket,
    ExchangeTradingCalendar,
)
from stock_web.api.fmt import format_kst

FILTERS = ("OPERATIONAL", "DAILY", "BLOCKED", "ALL")
FILTER_LABELS = {
    "OPERATIONAL": "운영 데이터", "DAILY": "일별", "BLOCKED": "차단",
    "ALL": "전체", "UNKNOWN": "미확인",
}
FRESHNESS = {
    "CURRENT": ("정시", "current"),
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

_CALENDAR_MARKETS = {
    "XKRX": ExchangeMarket.KR,
    "XNYS": ExchangeMarket.US,
}
_WEEKDAY_CALENDARS = frozenset({
    "BOK_ECOS_PROVIDER_WEEKDAY",
    "PROVIDER_BUSINESS_DAY",
    "PROVIDER_PUBLICATION",
})
_FIXED_LANE_TIMES = {
    "FRED_DAILY": "06:00",
    "GLOBAL_ETF_DAILY": "06:10",
    "GLOBAL_EQUITY_DAILY": "06:10",
    "GLOBAL_INDEX_DAILY": "06:20",
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


def _age_sessions(
    latest: object, *, today: date, calendar_name: object,
) -> int | None:
    """Count source-calendar sessions after ``latest`` through ``today``."""
    if latest in {None, "N/A"}:
        return None
    latest_date = date.fromisoformat(str(latest))
    if latest_date >= today:
        return 0
    start = latest_date + timedelta(days=1)
    calendar_key = str(calendar_name or "")
    if calendar_key in _CALENDAR_MARKETS:
        calendar = ExchangeTradingCalendar(_CALENDAR_MARKETS[calendar_key])
        return len(calendar.sessions_in_range(start, today))
    if calendar_key in _WEEKDAY_CALENDARS:
        return sum(
            1
            for offset in range((today - start).days + 1)
            if (start + timedelta(days=offset)).weekday() < 5
        )
    # Some manual, snapshot, event, and weekly rows intentionally have no
    # asserted trading calendar. Calendar-day age is still more honest than
    # hiding their retained date.
    return (today - latest_date).days


def _age_badge(age_sessions: int | None) -> tuple[str, str] | None:
    if age_sessions is None:
        return None
    label = (
        "오늘" if age_sessions == 0
        else "1일 전" if age_sessions == 1
        else f"{age_sessions}일 전"
    )
    css_class = (
        "age-neutral" if age_sessions <= 1
        else "age-amber" if age_sessions <= 3
        else "age-red"
    )
    return label, css_class


def _next_collection_hint(metadata: dict[str, object]) -> str:
    policy = str(metadata.get("provider_availability_policy") or "")
    lane = str(metadata.get("scheduler_lane") or "")
    if policy == "FRED_H10_WEEKLY_1615_ET":
        return "매주 월 06:00"
    if (
        policy in {"MANUAL_OBSERVATION", "NOT_APPLICABLE"}
        or metadata.get("automation_enabled") is False
        or (
            lane in {"", "NO_SCHEDULER_LANE", "BROKER_SNAPSHOT"}
            and not (metadata.get("due_at") or metadata.get("pending_until"))
        )
    ):
        return "수동"
    if policy == "KRX_POST_CLOSE_2030":
        return "20:30 수집 예정"
    if policy == "KRX_NEXT_TRADING_DAY_0910":
        return "09:10 수집 예정"
    if lane == "KR_FUNDAMENTALS_WEEKLY":
        return "주 마지막 거래일 20:30 수집 예정"
    if lane in _FIXED_LANE_TIMES:
        return f"{_FIXED_LANE_TIMES[lane]} 수집 예정"
    due_at = metadata.get("due_at")
    if isinstance(due_at, str):
        try:
            due = datetime.fromisoformat(due_at)
        except ValueError:
            pass
        else:
            if due.tzinfo is not None and due.utcoffset() is not None:
                return f"{(due - timedelta(minutes=15)):%H:%M} 수집 예정"
    pending_until = metadata.get("pending_until")
    if isinstance(pending_until, str):
        try:
            pending = datetime.strptime(pending_until, "%H:%M") - timedelta(minutes=15)
        except ValueError:
            pass
        else:
            return f"{pending:%H:%M} 수집 예정"
    return "수동"


def _is_pending_collection(
    metadata: dict[str, object], *, age_sessions: int | None, now: datetime,
) -> bool:
    due_at = metadata.get("due_at")
    if not isinstance(due_at, str) or age_sessions is None or age_sessions < 1:
        return False
    try:
        due = datetime.fromisoformat(due_at)
    except ValueError:
        return False
    if due.tzinfo is None or due.utcoffset() is None:
        return False
    return now.astimezone(timezone.utc) < due.astimezone(timezone.utc)


def _health_row(
    row: object, *, metadata: dict[str, object], today: date, now: datetime,
) -> dict[str, object]:
    freshness_raw = str(getattr(row, "display_status"))
    freshness_label, freshness_class = FRESHNESS.get(
        freshness_raw, (freshness_raw, "unknown"),
    )
    cadence = str(getattr(row, "cadence"))
    role = str(getattr(row, "role"))
    dataset = str(getattr(row, "dataset"))
    age_sessions = _age_sessions(
        getattr(row, "latest"), today=today, calendar_name=metadata.get("calendar"),
    )
    age_badge = _age_badge(age_sessions)
    return {
        "dataset": dataset,
        "description": f"{CADENCE.get(cadence, cadence)} · {_dataset_subject(dataset, role)}",
        "latest": _display_date(getattr(row, "latest")),
        "expected": _display_date(getattr(row, "expected")),
        "age_sessions": age_sessions,
        "age_label": age_badge[0] if age_badge else None,
        "age_class": age_badge[1] if age_badge else None,
        "freshness": {"raw": freshness_raw, "label": freshness_label, "class": freshness_class},
        "next_collection": _next_collection_hint(metadata),
        "collection_pending": _is_pending_collection(
            metadata, age_sessions=age_sessions, now=now,
        ),
        "operational": _enum(getattr(row, "operational"), OPERATIONAL),
        "blocker": _enum(getattr(row, "blocker"), BLOCKERS),
        "automation": _automation(getattr(row, "automation")),
        "automated": str(getattr(row, "automation")).endswith(" / ENABLED"),
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


def _load_health_metadata(path: Path) -> dict[str, dict[str, object]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        datasets = payload.get("datasets", []) if isinstance(payload, dict) else []
        return {
            str(item["dataset"]): item
            for item in datasets
            if isinstance(item, dict) and isinstance(item.get("dataset"), str)
        }
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}


def build_data_page_context(
    project_root: Path, status_filter: str, *, now: datetime | None = None,
) -> dict[str, object]:
    reference = now or datetime.now(ZoneInfo("Asia/Seoul"))
    if reference.tzinfo is None or reference.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    reference = reference.astimezone(ZoneInfo("Asia/Seoul"))
    latest_artifact = project_root / "artifacts/daily_health/universe_data_v2_latest.json"
    service = DailyHealthArtifactService(
        project_root, latest_artifact if latest_artifact.is_file() else None,
    )
    view = service.load()
    selected = status_filter if status_filter in FILTERS else "OPERATIONAL"
    selected_rows = service.filter_rows(view.rows, selected) if view.artifact_state == "READY" else ()
    metadata = _load_health_metadata(service.artifact_path)
    all_projected = tuple(
        _health_row(
            row,
            metadata=metadata.get(str(row.dataset), {}),
            today=reference.date(),
            now=reference,
        )
        for row in view.rows
    )
    selected_ids = {row.dataset for row in selected_rows}
    projected = tuple(row for row in all_projected if row["dataset"] in selected_ids)
    groups = []
    for raw, (label, css_class) in FRESHNESS.items():
        grouped = tuple(sorted(
            (row for row in projected if row["freshness"]["raw"] == raw),
            key=lambda row: (
                row["age_sessions"] is None,
                -(row["age_sessions"] if row["age_sessions"] is not None else 0),
                row["dataset"],
            ),
        ))
        if grouped:
            groups.append({"raw": raw, "label": label, "class": css_class, "rows": grouped})
    freshness_counts = [
        {"raw": raw, "label": label, "class": css_class,
         "count": sum(row.display_status == raw for row in view.rows)}
        for raw, (label, css_class) in FRESHNESS.items()
    ]
    receipts = load_scheduler_receipts(project_root)
    show_result_code = sum(bool(row["has_result_code"]) for row in receipts) >= 2
    automated_ages = tuple(
        int(row["age_sessions"])
        for row in all_projected
        if (
            row["dataset"] in metadata
            and row["automated"]
            and row["age_sessions"] is not None
        )
    )
    return {
        "filters": FILTERS,
        "filter_labels": FILTER_LABELS,
        "selected_filter": selected,
        "health_state": view.artifact_state,
        "health_warning": view.warning,
        "unregistered_dataset_ids": view.unregistered_dataset_ids,
        "health_summary": summarize_health_artifact(view),
        "age_summary": {
            "today": automated_ages.count(0),
            "yesterday": automated_ages.count(1),
            "older": sum(age >= 2 for age in automated_ages),
        },
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
