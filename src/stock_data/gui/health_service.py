from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from stock_data.orchestration.daily_operations import DATASET_UNIVERSE


HEALTH_RELATIVE_PATH = Path("artifacts/daily_health/universe_data_v2_20260819.json")
HEALTH_FILTERS = ("OPERATIONAL", "DAILY", "BLOCKED", "RESEARCH/STATIC", "ALL")
MANAGED_ACCEPTABLE_FRESHNESS = frozenset({"CURRENT", "EXPECTED_LAG"})
DECISION_HOLD_DEPENDENCY_MAP = {
    "KOSPI200_BREADTH_DEPENDENCY_FRESHNESS_UNRESOLVED": (
        "kr_index_constituent_daily",
        "kr_kospi200_constituent_price_daily",
        "kr_kospi200_breadth_daily",
    ),
}
DECISION_HOLD_FRESHNESS = frozenset({"STALE", "UNKNOWN"})


@dataclass(frozen=True)
class HealthDatasetRow:
    dataset: str
    role: str
    cadence: str
    latest: str
    expected: str
    freshness: str
    operational: str
    blocker: str
    pit: str
    automation: str
    source: str
    runtime_coverage: str
    display_consumer_eligibility: str = "UNKNOWN"
    display_consumer_reason: str = "UNKNOWN"
    research_consumer_eligibility: str = "UNKNOWN"
    research_consumer_reason: str = "UNKNOWN"
    predictive_consumer_eligibility: str = "UNKNOWN"
    predictive_consumer_reason: str = "UNKNOWN"


@dataclass(frozen=True)
class HealthArtifactView:
    artifact_state: str
    source: str
    rows: tuple[HealthDatasetRow, ...]
    warning: str | None = None


def summarize_health_artifact(view: HealthArtifactView) -> dict[str, object]:
    """Project the exact retained universe rows into the Dashboard summary."""
    if type(view) is not HealthArtifactView or view.artifact_state != "READY" or not view.rows:
        return {
            "overall": "UNKNOWN", "current": 0, "expected_lag": 0,
            "stale": 0, "operational_blocked": 0,
            "predictive_blocked": 0, "research_only": 0, "failed": 1,
            "managed_total": 0, "managed_acceptable": 0,
            "managed_current": 0, "managed_expected_lag": 0,
            "managed_stale": 0, "managed_unknown": 0,
            "managed_not_applicable": 0,
            "display_total": 0, "display_stale": 0,
            "display_unknown": 0, "display_gap": 0,
            "decision_hold_causes": (),
            "source": getattr(view, "source", "local health artifact"),
        }
    freshness = [row.freshness for row in view.rows]
    current = freshness.count("CURRENT")
    expected_lag = freshness.count("EXPECTED_LAG")
    stale = freshness.count("STALE")
    unknown = freshness.count("UNKNOWN")
    operational_blocked = sum(row.operational == "BLOCKED" for row in view.rows)
    predictive_blocked = sum(row.pit == "PIT_BLOCKED" for row in view.rows)
    research_only = sum(row.pit == "RESEARCH_ONLY" for row in view.rows)
    managed_rows = tuple(
        row for row in view.rows if row.automation.endswith(" / ENABLED")
    )
    managed_freshness = [row.freshness for row in managed_rows]
    managed_current = managed_freshness.count("CURRENT")
    managed_expected_lag = managed_freshness.count("EXPECTED_LAG")
    managed_stale = managed_freshness.count("STALE")
    managed_unknown = managed_freshness.count("UNKNOWN")
    managed_not_applicable = managed_freshness.count("NOT_APPLICABLE")
    managed_acceptable = managed_current + managed_expected_lag
    display_rows = tuple(
        row for row in view.rows
        if row.display_consumer_eligibility in {"ELIGIBLE", "LIMITED"}
    )
    display_freshness = [row.freshness for row in display_rows]
    display_stale = display_freshness.count("STALE")
    display_unknown = display_freshness.count("UNKNOWN")
    display_gap = display_stale + display_unknown
    decision_hold_causes = tuple(
        cause
        for cause, dataset_ids in DECISION_HOLD_DEPENDENCY_MAP.items()
        if any(
            row.dataset in dataset_ids and row.freshness in DECISION_HOLD_FRESHNESS
            for row in view.rows
        )
    )
    overall = (
        "UNKNOWN"
        if not managed_rows
        else "DEGRADED"
        if managed_acceptable != len(managed_rows) or display_gap
        else "EXPECTED_LAG"
        if managed_expected_lag
        else "CURRENT"
    )
    return {
        "overall": overall, "current": current, "expected_lag": expected_lag,
        "stale": stale, "operational_blocked": operational_blocked,
        "predictive_blocked": predictive_blocked,
        "research_only": research_only, "failed": unknown,
        "managed_total": len(managed_rows),
        "managed_acceptable": managed_acceptable,
        "managed_current": managed_current,
        "managed_expected_lag": managed_expected_lag,
        "managed_stale": managed_stale,
        "managed_unknown": managed_unknown,
        "managed_not_applicable": managed_not_applicable,
        "display_total": len(display_rows),
        "display_stale": display_stale,
        "display_unknown": display_unknown,
        "display_gap": display_gap,
        "decision_hold_causes": decision_hold_causes,
        "source": view.source,
    }


class DailyHealthArtifactService:
    """Strict read-only adapter for one retained DailyHealthReport artifact."""

    def __init__(self, project_root: Path, artifact_path: Path | None = None):
        self.project_root = Path(project_root)
        self.artifact_path = Path(artifact_path) if artifact_path else self.project_root / HEALTH_RELATIVE_PATH

    def load(self) -> HealthArtifactView:
        source = self._display_source()
        if not self.artifact_path.is_file():
            return HealthArtifactView("REPORT NOT AVAILABLE", source, (), "local health artifact is missing")
        try:
            payload = json.loads(self.artifact_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or not isinstance(payload.get("datasets"), list):
                raise ValueError("health artifact must contain a datasets array")
            health_rows = {row.dataset: row for row in (self._parse_row(item) for item in payload["datasets"])}
            if not health_rows:
                raise ValueError("health artifact datasets array is empty")
            if len(health_rows) != len(payload["datasets"]):
                raise ValueError("health artifact contains duplicate dataset rows")
            rows = tuple(
                health_rows.get(dataset_id, self._universe_only_row(dataset_id))
                for dataset_id in DATASET_UNIVERSE
            )
            return HealthArtifactView("READY", source, rows)
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            return HealthArtifactView("REPORT NOT AVAILABLE", source, (), f"local health artifact is invalid: {error}")

    @staticmethod
    def filter_rows(rows: tuple[HealthDatasetRow, ...], status_filter: str) -> tuple[HealthDatasetRow, ...]:
        if status_filter not in HEALTH_FILTERS:
            raise ValueError(f"unsupported health filter: {status_filter}")
        if status_filter == "ALL":
            return rows
        if status_filter == "OPERATIONAL":
            return tuple(row for row in rows if row.operational not in {"NOT_APPLICABLE", "BLOCKED"})
        if status_filter == "DAILY":
            return tuple(row for row in rows if row.cadence == "DAILY")
        if status_filter == "BLOCKED":
            return tuple(row for row in rows if row.operational == "BLOCKED")
        return tuple(row for row in rows if row.automation.startswith(("RESEARCH_ONLY", "NO_REFRESH")))

    @classmethod
    def _parse_row(cls, value: object) -> HealthDatasetRow:
        if not isinstance(value, dict):
            raise ValueError("health dataset rows must be objects")
        dataset = cls._required_text(value, "dataset", legacy="dataset_id")
        latest = cls._date_or_na(value.get("latest", value.get("actual_latest")), "latest")
        expected = cls._date_or_na(value.get("expected", value.get("expected_latest")), "expected")
        freshness = cls._compatibility_freshness(
            latest, expected, value.get("freshness", value.get("freshness_status")),
        )
        universe = DATASET_UNIVERSE.get(dataset)
        if universe is None:
            raise ValueError(f"health dataset is outside the typed universe: {dataset}")
        artifact_blocker = value.get("blocker")
        blocker = (
            artifact_blocker.strip()
            if isinstance(artifact_blocker, str) and artifact_blocker.strip()
            else universe.operational_blocker_reason.value
            if universe.operational_blocker_reason is not None
            else "N/A"
        )
        return HealthDatasetRow(
            dataset=dataset,
            role=universe.data_role.value,
            cadence=universe.data_grain.value,
            latest=latest,
            expected=expected,
            freshness=freshness,
            operational=universe.operational_status.value,
            blocker=blocker,
            pit=universe.predictive_pit_status.value,
            automation=(
                f"{universe.automation_policy.value} / "
                f"{'ENABLED' if universe.automation_enabled else 'DISABLED'}"
            ),
            source=universe.source,
            runtime_coverage=cls._optional_text(value.get("runtime_coverage")),
            display_consumer_eligibility=universe.display_consumer_eligibility.value,
            display_consumer_reason=universe.display_consumer_reason.value,
            research_consumer_eligibility=universe.research_consumer_eligibility.value,
            research_consumer_reason=universe.research_consumer_reason.value,
            predictive_consumer_eligibility=universe.predictive_consumer_eligibility.value,
            predictive_consumer_reason=universe.predictive_consumer_reason.value,
        )

    @staticmethod
    def _universe_only_row(dataset: str) -> HealthDatasetRow:
        universe = DATASET_UNIVERSE[dataset]
        return HealthDatasetRow(
            dataset=dataset,
            role=universe.data_role.value,
            cadence=universe.data_grain.value,
            latest=universe.retained_latest or "N/A",
            expected="N/A",
            freshness="UNKNOWN",
            operational=universe.operational_status.value,
            blocker=(
                universe.operational_blocker_reason.value
                if universe.operational_blocker_reason is not None else "N/A"
            ),
            pit=universe.predictive_pit_status.value,
            automation=(
                f"{universe.automation_policy.value} / "
                f"{'ENABLED' if universe.automation_enabled else 'DISABLED'}"
            ),
            source=universe.source,
            runtime_coverage="NOT_PROBED",
            display_consumer_eligibility=universe.display_consumer_eligibility.value,
            display_consumer_reason=universe.display_consumer_reason.value,
            research_consumer_eligibility=universe.research_consumer_eligibility.value,
            research_consumer_reason=universe.research_consumer_reason.value,
            predictive_consumer_eligibility=universe.predictive_consumer_eligibility.value,
            predictive_consumer_reason=universe.predictive_consumer_reason.value,
        )

    @staticmethod
    def _compatibility_freshness(latest: str, expected: str, explicit: object) -> str:
        if explicit in {"EXPECTED_LAG", "NOT_APPLICABLE"}:
            return str(explicit)
        if latest == "N/A" or expected == "N/A":
            return "UNKNOWN"
        latest_date = date.fromisoformat(latest)
        expected_date = date.fromisoformat(expected)
        if latest_date == expected_date or (
            explicit == "CURRENT" and latest_date > expected_date
        ):
            return "CURRENT"
        if latest_date < expected_date:
            return "STALE"
        return "UNKNOWN"

    @classmethod
    def _required_text(cls, mapping: dict, key: str, *, legacy: str) -> str:
        value = mapping.get(key, mapping.get(legacy))
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} must be non-empty text")
        return value.strip()

    @staticmethod
    def _optional_text(value: object) -> str:
        return value.strip() if isinstance(value, str) and value.strip() else "UNKNOWN"

    @classmethod
    def _enum(cls, value: object, allowed: set[str]) -> str:
        text = cls._optional_text(value)
        return text if text in allowed else "UNKNOWN"

    @staticmethod
    def _date_or_na(value: object, name: str) -> str:
        if value is None:
            return "N/A"
        if not isinstance(value, str):
            raise ValueError(f"{name} must be an ISO date or null")
        try:
            parsed = date.fromisoformat(value)
        except ValueError as error:
            raise ValueError(f"{name} must be an ISO date or null") from error
        if parsed.isoformat() != value:
            raise ValueError(f"{name} must be a canonical ISO date")
        return value

    def _display_source(self) -> str:
        try:
            return self.artifact_path.resolve().relative_to(self.project_root.resolve()).as_posix()
        except ValueError:
            return str(self.artifact_path.resolve())
