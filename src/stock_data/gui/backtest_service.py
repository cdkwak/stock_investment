from __future__ import annotations

from collections.abc import Callable
import csv
from dataclasses import asdict, dataclass, replace
import io
import json
import math
from datetime import date
import os
from pathlib import Path
import shutil
from types import MappingProxyType
from typing import Mapping
from uuid import uuid4

from market_backtest.ablation import FeatureFamilyStatus, build_ablation_plan
from market_backtest.crisis import CRISIS_WINDOWS, replay_crisis_windows
from market_backtest.experiments import (
    artifact_bytes_digest,
    canonical_json_digest,
)
from market_backtest.labels import (
    MAX_LABEL_HORIZON_TRADING_DAYS,
    build_forward_labels,
)
from market_backtest.phase1_replay import (
    BUNDLE_SCHEMA,
    DEFAULT_OUTPUT_RELATIVE,
    EXPECTED_FROZEN_DIGEST,
    Phase1ArtifactReceipt,
    Phase1ReplayReceipt,
    Phase1ReplayRequest,
    _load_verified_source,
    phase1_code_digest,
    run_phase1_replay,
)
from market_backtest.portfolio import (
    CLOSE_PROXY_V1,
    INSTRUMENT_CLAIM,
    KOSPI200_FROZEN_HOLDOUT_V1,
    PORTFOLIO_STATUS,
    simulate_kospi200_risk_off_portfolio,
)
from market_backtest.signals import (
    PREDEFINED_SMALL_GRID,
    SignalThresholds,
    build_descriptive_signals,
    evaluate_predefined_walk_forward,
    evaluate_signals,
)
from runtime_diagnostics import (
    RuntimeDiagnosticStore,
    artifact_identity,
    new_session_id,
    safe_record_failure,
)
from market_features.kospi200 import FEATURE_DEFINITIONS, build_kospi200_features


RESULT_RELATIVE_PATH = Path("artifacts/backtest/phase1_signal_replay/result.json")
EXPECTED_STATUS = "DESCRIPTIVE_SIGNAL_REPLAY_NOT_PORTFOLIO_BACKTEST"
EXPECTED_DATASET = "kr_kospi200_index_daily"
EXPECTED_CONTRACT_VERSION = 1
EXPECTED_DECISION_RULE = "T_CLOSE_OBSERVED_USABLE_FROM_T_PLUS_1_DECISION"
EXPECTED_METRICS_SCOPE = "DEVELOPMENT_ONLY_HOLDOUT_UNTOUCHED"
EXPECTED_EXPERIMENT_ID = "phase1_price_volatility_descriptive_v1"
_ACCEPTED_LEGACY_CODE_TREE_DIGEST = (
    "d2fa21fc268948db5fba779bb60e602c1b209c241813fb5a06f4d73ab7ba9f42"
)
_ACCEPTED_EXPLICIT_CODE_TREE_DIGEST = (
    "04a17779f6ecc42e7899755f274072ddd568cd225b7282174ead9d6375cc49db"
)
EXPECTED_BUNDLE_FILES = (
    "bundle.json",
    "experiments.json",
    "portfolio_ledger.json",
    "result.json",
    "signals.csv",
)
EXPECTED_BASE_FILES = tuple(
    name for name in EXPECTED_BUNDLE_FILES if name != "bundle.json"
)
EXPECTED_FROZEN_MANIFEST = {
    "dataset": EXPECTED_DATASET,
    "contract_version": EXPECTED_CONTRACT_VERSION,
    "coverage_start": "1990-01-03",
    "coverage_end": "2026-08-14",
    "rows": 9447,
    "files": 37,
    "bytes": 738068,
    "root_manifest_sha256": EXPECTED_FROZEN_DIGEST,
    "decision_rule": EXPECTED_DECISION_RULE,
}
EXPECTED_EXPERIMENT_KEYS = {
    "experiment_id",
    "frozen_input_digest",
    "feature_set",
    "feature_versions",
    "label_version",
    "split_policy",
    "purge",
    "embargo",
    "threshold_rule",
    "result_artifact",
    "code_version",
    "code_tree_digest",
    "threshold_values_digest",
    "signals_artifact_digest",
    "result_artifact_digest",
    "label_horizon_trading_days",
    "signal_pit_status",
    "holdout_results_reviewed",
}
EXPECTED_SIGNAL_COLUMNS = (
    "observation_date",
    "ticker",
    "date_semantics",
    "usable_from",
    "source_dataset",
    "source_contract_version",
    "pit_status",
    "high_realized_volatility",
    "large_drawdown",
    "below_moving_average",
    "negative_momentum",
    "risk_score",
    "risk_off_signal",
    "signal_version",
)
EXPECTED_SIGNAL_ROWS = 8_164
EXPECTED_DESCRIPTIVE_METRIC_KEYS = {
    "observations",
    "true_positive",
    "false_positive",
    "false_negative",
    "true_negative",
    "precision",
    "recall",
    "false_positive_rate",
    "event_prevalence",
    "pr_auc_average_precision",
    "mean_forward_return_20d",
    "mean_forward_max_drawdown_20d",
    "mean_mae_20d",
    "mean_mfe_20d",
}
EXPECTED_DESCRIPTIVE_COUNT_KEYS = {
    "observations",
    "true_positive",
    "false_positive",
    "false_negative",
    "true_negative",
}
EXPECTED_DESCRIPTIVE_RATE_KEYS = {
    "precision",
    "recall",
    "false_positive_rate",
    "event_prevalence",
    "pr_auc_average_precision",
}


@dataclass(frozen=True)
class BacktestInputCoverage:
    dataset: str
    contract_version: int
    coverage_start: str
    coverage_end: str
    rows: int
    files: int
    manifest_sha256: str
    decision_rule: str


@dataclass(frozen=True)
class NamedNumber:
    name: str
    value: int | float


@dataclass(frozen=True)
class CrisisReplaySummary:
    event: str
    start: str
    end: str
    status: str
    observations: int | None
    risk_off_observations: int | None
    mean_forward_20d_return: float | None
    worst_forward_20d_drawdown: float | None


@dataclass(frozen=True)
class BacktestHoldoutView:
    policy_id: str
    coverage_start: str
    coverage_end: str
    holdout_start: str
    development_observations: int
    holdout_observations: int
    results_reviewed: bool


@dataclass(frozen=True)
class BacktestCurvePoint:
    date: str
    nav: float
    drawdown: float
    target_position: int


@dataclass(frozen=True)
class BacktestPortfolioView:
    status: str
    instrument_claim: str
    assumptions: Mapping[str, int | float | str | bool]
    initial_nav: float
    ending_nav: float
    total_return: float
    annualized_return: float
    annualized_volatility: float
    max_drawdown: float
    trade_count: int
    total_turnover: float
    average_long_exposure: float
    transaction_cost_paid: float
    curve: tuple[BacktestCurvePoint, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "assumptions", MappingProxyType(dict(self.assumptions)),
        )

    @property
    def metrics(self) -> BacktestPortfolioView:
        """Expose the typed metric record expected by the GUI projection."""
        return self


@dataclass(frozen=True)
class BacktestExperimentView:
    artifact_state: str
    experiment_status: str
    input_coverage: BacktestInputCoverage | None
    feature_set: tuple[NamedNumber, ...]
    horizons: tuple[str, ...]
    signals: tuple[str, ...]
    metrics: tuple[NamedNumber, ...]
    crises: tuple[CrisisReplaySummary, ...]
    portfolio_scope: str
    source: str
    warning: str | None = None
    holdout: BacktestHoldoutView | None = None
    portfolio: BacktestPortfolioView | None = None
    bundle_receipt: Phase1ReplayReceipt | None = None


@dataclass(frozen=True)
class BacktestArtifactSnapshot:
    name: str
    body: bytes
    sha256: str


@dataclass(frozen=True)
class ValidatedBacktestBundle:
    view: BacktestExperimentView
    receipt: Phase1ReplayReceipt
    artifact_bodies: Mapping[str, bytes]

    def __post_init__(self) -> None:
        bodies = dict(self.artifact_bodies)
        if (
            type(self.view) is not BacktestExperimentView
            or type(self.receipt) is not Phase1ReplayReceipt
            or tuple(sorted(bodies)) != EXPECTED_BUNDLE_FILES
            or any(type(body) is not bytes or not body for body in bodies.values())
        ):
            raise ValueError("validated backtest bundle fields are invalid")
        object.__setattr__(
            self, "artifact_bodies", MappingProxyType(bodies),
        )

    def artifact_bytes(self, name: str) -> bytes:
        return self.artifact_bodies[name]

    @property
    def artifacts(self) -> tuple[BacktestArtifactSnapshot, ...]:
        return tuple(
            BacktestArtifactSnapshot(
                name=name,
                body=body,
                sha256=artifact_bytes_digest(body),
            )
            for name, body in sorted(self.artifact_bodies.items())
        )


@dataclass(frozen=True)
class BacktestExportReceipt:
    status: str
    destination: Path
    bundle_digest: str
    artifacts: tuple[Phase1ArtifactReceipt, ...]


@dataclass(frozen=True)
class BacktestRunOutcome:
    bundle: ValidatedBacktestBundle | None
    error: str | None

    def __post_init__(self) -> None:
        if (self.bundle is None) == (self.error is None):
            raise ValueError("backtest run outcome must contain one result")


class BacktestWorkflowError(RuntimeError):
    """Raised when a replay receipt, bundle, or exact export fails closed."""


# Compatibility for the already-landed GUI worker while D026 moves the
# workflow methods onto BacktestResultService.
BacktestReplayServiceError = BacktestWorkflowError
BacktestPortfolioPoint = BacktestCurvePoint


class BacktestResultService:
    """Read the accepted local result interface without running a backtest.

    The GUI adapter deliberately performs no feature, signal, label, or metric
    calculation. Unknown and malformed result schemas fail closed to an
    unavailable view.
    """

    def __init__(
        self,
        project_root: Path,
        result_path: Path | None = None,
        *,
        output_root: Path | None = None,
        runner: Callable[[Phase1ReplayRequest], Phase1ReplayReceipt] = run_phase1_replay,
        diagnostic_session_id: str | None = None,
    ) -> None:
        self.project_root = Path(project_root)
        if output_root is not None:
            self.output_root = Path(output_root)
        elif result_path is not None:
            self.output_root = Path(result_path).parent
        else:
            self.output_root = self.project_root / DEFAULT_OUTPUT_RELATIVE
        self.result_path = (
            Path(result_path)
            if result_path is not None
            else self.output_root / "result.json"
        )
        self.runner = runner
        self.diagnostic_session_id = diagnostic_session_id or new_session_id()

    def load(self) -> BacktestExperimentView:
        source = self._display_source()
        workflow = self._workflow()
        if workflow.has_exact_bundle_candidate():
            try:
                return workflow.load_validated_bundle().view
            except BacktestWorkflowError as error:
                return self._unavailable(
                    source,
                    f"strict local backtest bundle is invalid: {error}",
                )
        if not self.result_path.is_file():
            return self._unavailable(source, "local result artifact is missing")
        try:
            payload = json.loads(self.result_path.read_text(encoding="utf-8"))
            return self._parse(payload, source)
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError, KeyError) as error:
            return self._unavailable(source, f"local result artifact is invalid: {error}")

    def run_validated(
        self, *, diagnostic_run_id: str | None = None,
    ) -> ValidatedBacktestBundle:
        """Run the offline replay and validate its exact published generation."""
        correlated_run_id = diagnostic_run_id or getattr(
            self, "_diagnostic_run_id", None
        )
        return self._workflow().run(diagnostic_run_id=correlated_run_id)

    def load_validated_bundle(
        self,
        expected_receipt: Phase1ReplayReceipt | None = None,
    ) -> ValidatedBacktestBundle:
        """Load one exact five-file generation, optionally binding a receipt."""
        return self._workflow().load_validated_bundle(expected_receipt)

    def export_exact_bundle(
        self,
        accepted: ValidatedBacktestBundle,
        destination: Path,
    ) -> BacktestExportReceipt:
        """Write only accepted in-memory bytes to a new exact directory."""
        return self._workflow().export_exact(accepted, destination)

    def _workflow(self) -> BacktestReplayService:
        return BacktestReplayService(
            self.project_root,
            output_root=self.output_root,
            runner=self.runner,
            diagnostic_session_id=self.diagnostic_session_id,
        )

    def _parse(self, payload: object, source: str) -> BacktestExperimentView:
        if not isinstance(payload, dict):
            raise ValueError("result root must be an object")
        status = payload.get("status")
        if status != EXPECTED_STATUS:
            raise ValueError("result status is not the accepted non-portfolio experiment")

        manifest = self._mapping(payload.get("frozen_manifest"), "frozen_manifest")
        dataset = self._text(manifest, "dataset")
        contract_version = self._integer(manifest, "contract_version")
        decision_rule = self._text(manifest, "decision_rule")
        if dataset != EXPECTED_DATASET:
            raise ValueError("frozen manifest dataset is not the accepted GUI input")
        if contract_version != EXPECTED_CONTRACT_VERSION:
            raise ValueError("frozen manifest contract version is not accepted")
        if decision_rule != EXPECTED_DECISION_RULE:
            raise ValueError("frozen manifest decision rule is not accepted")
        coverage_start = self._iso_date(manifest, "coverage_start")
        coverage_end = self._iso_date(manifest, "coverage_end")
        if coverage_start > coverage_end:
            raise ValueError("frozen manifest coverage is reversed")
        if (
            set(manifest) != set(EXPECTED_FROZEN_MANIFEST)
            or any(
                type(manifest.get(key)) is not type(expected)
                or manifest.get(key) != expected
                for key, expected in EXPECTED_FROZEN_MANIFEST.items()
            )
        ):
            raise ValueError("frozen manifest differs from the fixed input")
        coverage = BacktestInputCoverage(
            dataset=dataset,
            contract_version=contract_version,
            coverage_start=coverage_start.isoformat(),
            coverage_end=coverage_end.isoformat(),
            rows=self._integer(manifest, "rows"),
            files=self._integer(manifest, "files"),
            manifest_sha256=self._sha256(manifest.get("root_manifest_sha256")),
            decision_rule=decision_rule,
        )
        threshold_payload = self._mapping(payload.get("thresholds"), "thresholds")
        expected_thresholds = asdict(SignalThresholds())
        if (
            set(threshold_payload) != set(expected_thresholds)
            or any(
                type(threshold_payload.get(key)) is not type(expected)
                or threshold_payload.get(key) != expected
                for key, expected in expected_thresholds.items()
            )
        ):
            raise ValueError("thresholds differ from the fixed signal contract")
        thresholds = self._numbers(threshold_payload)
        metrics = self._descriptive_metrics(
            self._mapping(payload.get("metrics"), "metrics")
        )
        crises = payload.get("crisis_replay")
        if not isinstance(crises, list):
            raise ValueError("crisis_replay must be an array")
        crisis_summaries = tuple(self._crisis(row) for row in crises)
        horizons = (
            "forward return: 20 trading days",
            "forward max drawdown: 20 trading days",
        ) if any(row.status == "DIAGNOSTIC_ONLY" for row in crisis_summaries) else ()

        minimum_conditions = dict((item.name, item.value) for item in thresholds).get("minimum_conditions")
        signal = "risk_off_signal (descriptive threshold rule)"
        if minimum_conditions is not None:
            signal += f": minimum {minimum_conditions:g} conditions"
        return BacktestExperimentView(
            artifact_state="READY",
            experiment_status=status,
            input_coverage=coverage,
            feature_set=thresholds,
            horizons=horizons,
            signals=(signal,),
            metrics=metrics,
            crises=crisis_summaries,
            portfolio_scope="NOT A PORTFOLIO BACKTEST - EQUITY CURVE UNAVAILABLE",
            source=source,
        )

    @staticmethod
    def _mapping(value: object, name: str) -> Mapping[str, object]:
        if not isinstance(value, dict) or not value:
            raise ValueError(f"{name} must be a non-empty object")
        return value

    @staticmethod
    def _text(mapping: Mapping[str, object], key: str) -> str:
        value = mapping.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(f"{key} must be non-empty text")
        return value

    @staticmethod
    def _integer(mapping: Mapping[str, object], key: str) -> int:
        value = mapping.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{key} must be a positive integer")
        return value

    @classmethod
    def _iso_date(cls, mapping: Mapping[str, object], key: str) -> date:
        value = cls._text(mapping, key)
        try:
            parsed = date.fromisoformat(value)
        except ValueError as error:
            raise ValueError(f"{key} must be an ISO date") from error
        if parsed.isoformat() != value:
            raise ValueError(f"{key} must be a canonical ISO date")
        return parsed

    @staticmethod
    def _sha256(value: object) -> str:
        if not isinstance(value, str) or len(value) != 64:
            raise ValueError("root_manifest_sha256 must be SHA-256")
        try:
            int(value, 16)
        except ValueError as error:
            raise ValueError("root_manifest_sha256 must be hexadecimal") from error
        return value

    @staticmethod
    def _numbers(mapping: Mapping[str, object]) -> tuple[NamedNumber, ...]:
        result = []
        for name, value in mapping.items():
            if not isinstance(name, str) or not name:
                raise ValueError("numeric field names must be non-empty text")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{name} must be numeric")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
            result.append(NamedNumber(name, value))
        return tuple(result)

    @classmethod
    def _descriptive_metrics(
        cls, mapping: Mapping[str, object],
    ) -> tuple[NamedNumber, ...]:
        if set(mapping) != EXPECTED_DESCRIPTIVE_METRIC_KEYS:
            raise ValueError("descriptive metric schema differs")
        counts: dict[str, int] = {}
        for key in EXPECTED_DESCRIPTIVE_COUNT_KEYS:
            value = mapping.get(key)
            if type(value) is not int or value < 0:
                raise ValueError(f"{key} must be a non-negative integer")
            counts[key] = value
        if (
            counts["observations"] < 1
            or counts["observations"]
            != counts["true_positive"]
            + counts["false_positive"]
            + counts["false_negative"]
            + counts["true_negative"]
        ):
            raise ValueError("descriptive confusion counts do not reconcile")
        numbers: dict[str, float] = {}
        for key in EXPECTED_DESCRIPTIVE_METRIC_KEYS - EXPECTED_DESCRIPTIVE_COUNT_KEYS:
            value = mapping.get(key)
            if type(value) is not float or not math.isfinite(value):
                raise ValueError(f"{key} must be exact finite floating-point")
            numbers[key] = value
        if any(not 0.0 <= numbers[key] <= 1.0 for key in EXPECTED_DESCRIPTIVE_RATE_KEYS):
            raise ValueError("descriptive rate metric is outside [0, 1]")

        def ratio(numerator: int, denominator: int) -> float:
            return numerator / denominator if denominator else 0.0

        expected_rates = {
            "precision": ratio(
                counts["true_positive"],
                counts["true_positive"] + counts["false_positive"],
            ),
            "recall": ratio(
                counts["true_positive"],
                counts["true_positive"] + counts["false_negative"],
            ),
            "false_positive_rate": ratio(
                counts["false_positive"],
                counts["false_positive"] + counts["true_negative"],
            ),
            "event_prevalence": ratio(
                counts["true_positive"] + counts["false_negative"],
                counts["observations"],
            ),
        }
        if any(
            not math.isclose(
                numbers[key], expected, rel_tol=1e-15, abs_tol=1e-15,
            )
            for key, expected in expected_rates.items()
        ):
            raise ValueError("descriptive rate metrics do not reconcile")
        if (
            numbers["mean_forward_return_20d"] < -1.0
            or not -1.0 <= numbers["mean_forward_max_drawdown_20d"] <= 0.0
            or not -1.0 <= numbers["mean_mae_20d"] <= 0.0
            or numbers["mean_mfe_20d"] < -1.0
        ):
            raise ValueError("descriptive outcome metric range differs")
        return cls._numbers(mapping)

    @classmethod
    def _crisis(cls, value: object) -> CrisisReplaySummary:
        if not isinstance(value, dict):
            raise ValueError("crisis_replay rows must be objects")
        event = cls._text(value, "event")
        start = cls._iso_date(value, "start")
        end = cls._iso_date(value, "end")
        if start > end:
            raise ValueError("crisis replay date range is reversed")
        status = cls._text(value, "status")
        if status == "UNTOUCHED_HOLDOUT":
            required = {"event", "start", "end", "status", "holdout_observations_excluded"}
            if set(value) != required or value.get("holdout_observations_excluded") != "NOT_INSPECTED":
                raise ValueError("untouched holdout row must use the exact non-inspection schema")
            return CrisisReplaySummary(
                event, start.isoformat(), end.isoformat(), status, None, None, None, None,
            )
        if status != "DIAGNOSTIC_ONLY":
            raise ValueError("crisis replay status is unsupported")
        if end.isoformat() >= KOSPI200_FROZEN_HOLDOUT_V1.holdout_start:
            raise ValueError("diagnostic crisis replay crosses the holdout")
        required = {
            "observations", "risk_off_observations", "mean_forward_20d_return",
            "worst_forward_20d_drawdown",
        }
        if not required.issubset(value):
            raise ValueError("diagnostic crisis replay fields are incomplete")
        observations = cls._positive_integer(value, "observations")
        risk_off = cls._nonnegative_integer(value, "risk_off_observations")
        if risk_off > observations:
            raise ValueError("risk_off_observations exceeds observations")
        mean_return = cls._finite_number(value, "mean_forward_20d_return")
        worst_drawdown = cls._finite_number(value, "worst_forward_20d_drawdown")
        return CrisisReplaySummary(
            event, start.isoformat(), end.isoformat(), status, observations, risk_off,
            mean_return, worst_drawdown,
        )

    @staticmethod
    def _positive_integer(mapping: Mapping[str, object], key: str) -> int:
        value = mapping.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{key} must be a positive integer")
        return value

    @staticmethod
    def _nonnegative_integer(mapping: Mapping[str, object], key: str) -> int:
        value = mapping.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{key} must be a non-negative integer")
        return value

    @staticmethod
    def _finite_number(mapping: Mapping[str, object], key: str) -> float:
        value = mapping.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"{key} must be finite numeric")
        return float(value)

    def _display_source(self) -> str:
        try:
            return self.result_path.resolve().relative_to(self.project_root.resolve()).as_posix()
        except ValueError:
            return str(self.result_path.resolve())

    @staticmethod
    def _unavailable(source: str, warning: str) -> BacktestExperimentView:
        return BacktestExperimentView(
            artifact_state="RESULT NOT AVAILABLE",
            experiment_status="N/A",
            input_coverage=None,
            feature_set=(),
            horizons=(),
            signals=(),
            metrics=(),
            crises=(),
            portfolio_scope="NOT A PORTFOLIO BACKTEST - EQUITY CURVE UNAVAILABLE",
            source=source,
            warning=warning,
        )

    def unavailable(self, warning: str) -> BacktestExperimentView:
        return self._unavailable(self._display_source(), warning)


class BacktestReplayService:
    """Run and validate the fixed Phase-1 replay for the GUI boundary.

    A returned runner receipt is never sufficient by itself.  This adapter
    reads the exact published five-file generation once, cross-checks every
    receipt and semantic boundary, and retains immutable bytes for rendering
    and export.
    """

    _WINDOWS_REPARSE_POINT = 0x0400
    _PORTFOLIO_METRIC_NAMES = (
        "observations",
        "intervals",
        "initial_nav",
        "ending_nav",
        "total_return",
        "annualized_return",
        "annualized_volatility",
        "max_drawdown",
        "trade_count",
        "total_turnover",
        "average_long_exposure",
        "transaction_cost_paid",
    )
    _LEDGER_KEYS = {
        "date", "close", "signal_observation_date", "usable_from",
        "risk_off_signal", "position_before", "target_position",
        "market_return", "gross_portfolio_return", "trade_notional",
        "turnover", "transaction_cost", "cash", "units", "asset_value",
        "nav_before_cost", "nav", "net_return", "drawdown",
    }
    _LEDGER_NUMERIC_KEYS = _LEDGER_KEYS - {
        "date", "signal_observation_date", "usable_from", "risk_off_signal",
        "position_before", "target_position",
    }

    def __init__(
        self,
        project_root: Path,
        *,
        output_root: Path | None = None,
        runner: Callable[[Phase1ReplayRequest], Phase1ReplayReceipt] = run_phase1_replay,
        diagnostic_session_id: str | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        selected_output = (
            Path(output_root)
            if output_root is not None
            else self.project_root / DEFAULT_OUTPUT_RELATIVE
        )
        self.output_root = selected_output.resolve()
        self.runner = runner
        self.session_id = diagnostic_session_id or new_session_id()

    def has_exact_bundle_candidate(self) -> bool:
        if any(
            (self.output_root / name).exists()
            or (self.output_root / name).is_symlink()
            for name in ("bundle.json", "portfolio_ledger.json")
        ):
            return True
        result_path = self.output_root / "result.json"
        if result_path.is_file():
            try:
                result = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                result = None
            if type(result) is dict and "portfolio_foundation" in result:
                return True
        prefix = f".{self.output_root.name}.phase1-replay"
        return any(
            path.exists() or path.is_symlink()
            for path in (
                self.output_root.parent / f"{prefix}.stage",
                self.output_root.parent / f"{prefix}.backup",
                self.output_root.parent / f"{prefix}.journal.json",
                self.output_root.parent / f"{prefix}.journal.tmp",
            )
        )

    def run(
        self, *, diagnostic_run_id: str | None = None,
    ) -> ValidatedBacktestBundle:
        run_id = diagnostic_run_id or new_session_id()
        try:
            receipt = self.runner(Phase1ReplayRequest(
                project_root=self.project_root,
                output_root=self.output_root,
            ))
            return self.validate_receipt(receipt)
        except Exception as error:
            artifacts = tuple(
                identity
                for name in EXPECTED_BUNDLE_FILES
                if (identity := artifact_identity(
                    self.project_root, self.output_root / name
                )) is not None
            )
            safe_record_failure(
                RuntimeDiagnosticStore(
                    self.project_root / "artifacts/runtime_logs/application"
                ),
                project_root=self.project_root, domain="GUI",
                kind="TERMINAL_FAILURE", session_id=self.session_id,
                run_id=run_id, code="BACKTEST_WORKER_FAILED",
                stage="REPLAY", error=error, artifacts=artifacts,
            )
            if isinstance(error, BacktestReplayServiceError):
                raise
            raise BacktestReplayServiceError("offline replay failed") from error

    def load_validated_bundle(
        self,
        expected_receipt: Phase1ReplayReceipt | None = None,
    ) -> ValidatedBacktestBundle:
        snapshots = self._snapshot_directory(self.output_root)
        records = self._records(snapshots)
        try:
            receipt = Phase1ReplayReceipt(
                schema=BUNDLE_SCHEMA,
                status="READY",
                output_root=self.output_root,
                frozen_input_digest=EXPECTED_FROZEN_DIGEST,
                bundle_digest=self._records_digest(records),
                artifacts=records,
            )
        except ValueError as error:
            raise BacktestReplayServiceError(
                "published replay receipt could not be reconstructed"
            ) from error
        if expected_receipt is not None:
            if type(expected_receipt) is not Phase1ReplayReceipt:
                raise BacktestReplayServiceError("expected receipt type differs")
            try:
                Phase1ReplayReceipt.__post_init__(expected_receipt)
            except ValueError as error:
                raise BacktestReplayServiceError("expected receipt is invalid") from error
            if expected_receipt != receipt:
                raise BacktestReplayServiceError(
                    "expected receipt does not match published bytes"
                )
            receipt = expected_receipt
        return self._validate_snapshot(receipt, snapshots)

    def validate_receipt(
        self, receipt: Phase1ReplayReceipt,
    ) -> ValidatedBacktestBundle:
        if type(receipt) is not Phase1ReplayReceipt:
            raise BacktestReplayServiceError("runner returned an invalid receipt type")
        try:
            Phase1ReplayReceipt.__post_init__(receipt)
        except ValueError as error:
            raise BacktestReplayServiceError("runner receipt is invalid") from error
        if receipt.output_root != self.output_root:
            raise BacktestReplayServiceError("runner receipt output root differs")
        snapshots = self._snapshot_directory(receipt.output_root)
        return self._validate_snapshot(receipt, snapshots)

    def export_exact(
        self, bundle: ValidatedBacktestBundle, destination: Path,
    ) -> BacktestExportReceipt:
        if type(bundle) is not ValidatedBacktestBundle:
            raise BacktestReplayServiceError("an exact validated bundle is required")
        snapshots = self._snapshots_from_bodies(bundle.artifact_bodies)
        validated = self._validate_snapshot(
            bundle.receipt,
            snapshots,
            bind_project_source=False,
        )
        if validated.view != bundle.view:
            raise BacktestReplayServiceError("validated bundle view differs")

        requested = Path(destination)
        if (
            not requested.is_absolute()
            or not requested.name
            or requested.name in {".", ".."}
            or ".." in requested.parts
            or requested.name.rstrip(" .") != requested.name
        ):
            raise BacktestReplayServiceError("export destination must be absolute")
        lexical_parent = requested.parent.absolute()
        try:
            parent = requested.parent.resolve()
        except OSError as error:
            raise BacktestReplayServiceError(
                "export destination parent is unavailable"
            ) from error
        target = parent / requested.name
        if (
            lexical_parent != parent
            or not parent.is_dir()
            or parent.is_symlink()
            or self._is_reparse(parent)
            or target.exists()
            or target.is_symlink()
        ):
            raise BacktestReplayServiceError(
                "export destination must be a new plain directory"
            )
        source = bundle.receipt.output_root
        if target.is_relative_to(source) or source.is_relative_to(target):
            raise BacktestReplayServiceError("export destination overlaps source bundle")
        protected = (
            self.project_root / "data",
            self.project_root / "artifacts/backtest/frozen_inputs",
        )
        if any(target == root or target.is_relative_to(root) for root in protected):
            raise BacktestReplayServiceError("export destination is protected")

        stage = parent / f".{target.name}.backtest-export-{uuid4().hex}.stage"
        try:
            stage.mkdir(parents=False, exist_ok=False)
            for artifact in snapshots:
                with (stage / artifact.name).open("xb") as stream:
                    stream.write(artifact.body)
                    stream.flush()
                    os.fsync(stream.fileno())
            exported = self._snapshot_directory(stage)
            if exported != snapshots:
                raise BacktestReplayServiceError("export readback differs")
            records = self._records(exported)
            digest = self._records_digest(records)
            if records != bundle.receipt.artifacts or digest != bundle.receipt.bundle_digest:
                raise BacktestReplayServiceError("export receipt readback differs")
            if target.exists() or target.is_symlink():
                raise BacktestReplayServiceError("export destination now exists")
            stage.rename(target)
            return BacktestExportReceipt(
                status="EXPORTED",
                destination=target,
                bundle_digest=digest,
                artifacts=records,
            )
        except Exception as error:
            if stage.is_dir() and not stage.is_symlink():
                shutil.rmtree(stage)
            if isinstance(error, BacktestReplayServiceError):
                raise
            raise BacktestReplayServiceError("exact bundle export failed") from error

    @staticmethod
    def _snapshots_from_bodies(
        bodies: Mapping[str, bytes],
    ) -> tuple[BacktestArtifactSnapshot, ...]:
        copied = dict(bodies)
        if tuple(sorted(copied)) != EXPECTED_BUNDLE_FILES or any(
            type(body) is not bytes or not body for body in copied.values()
        ):
            raise BacktestReplayServiceError("accepted artifact bodies differ")
        return tuple(
            BacktestArtifactSnapshot(
                name=name,
                body=copied[name],
                sha256=artifact_bytes_digest(copied[name]),
            )
            for name in EXPECTED_BUNDLE_FILES
        )

    @classmethod
    def _is_reparse(cls, path: Path) -> bool:
        try:
            return bool(
                getattr(path.lstat(), "st_file_attributes", 0)
                & cls._WINDOWS_REPARSE_POINT
            )
        except OSError:
            return True

    @classmethod
    def _snapshot_directory(
        cls, root: Path,
    ) -> tuple[BacktestArtifactSnapshot, ...]:
        root = Path(root)
        try:
            before_root = root.lstat()
        except OSError as error:
            raise BacktestReplayServiceError("replay bundle is unavailable") from error
        if (
            not root.is_absolute()
            or not root.is_dir()
            or root.is_symlink()
            or cls._is_reparse(root)
            or root.resolve() != root
        ):
            raise BacktestReplayServiceError("replay bundle root is unsafe")
        try:
            entries = tuple(sorted(root.iterdir(), key=lambda item: item.name))
        except OSError as error:
            raise BacktestReplayServiceError("replay bundle inventory is unreadable") from error
        if tuple(path.name for path in entries) != EXPECTED_BUNDLE_FILES:
            raise BacktestReplayServiceError("replay bundle inventory differs")

        snapshots: list[BacktestArtifactSnapshot] = []
        try:
            for path in entries:
                before = path.lstat()
                if (
                    not path.is_file()
                    or path.is_symlink()
                    or cls._is_reparse(path)
                    or path.resolve() != path
                    or getattr(before, "st_nlink", 1) != 1
                ):
                    raise BacktestReplayServiceError(
                        "replay bundle contains an unsafe artifact"
                    )
                body = path.read_bytes()
                after = path.lstat()
                if (
                    (before.st_dev, before.st_ino, before.st_size)
                    != (after.st_dev, after.st_ino, after.st_size)
                    or len(body) != after.st_size
                ):
                    raise BacktestReplayServiceError(
                        "replay artifact changed while reading"
                    )
                snapshots.append(BacktestArtifactSnapshot(
                    name=path.name,
                    body=body,
                    sha256=artifact_bytes_digest(body),
                ))
            after_root = root.lstat()
        except OSError as error:
            raise BacktestReplayServiceError("replay artifact is unreadable") from error
        if (
            (before_root.st_dev, before_root.st_ino)
            != (after_root.st_dev, after_root.st_ino)
        ):
            raise BacktestReplayServiceError("replay bundle changed while reading")
        return tuple(snapshots)

    @staticmethod
    def _records(
        snapshots: tuple[BacktestArtifactSnapshot, ...],
    ) -> tuple[Phase1ArtifactReceipt, ...]:
        try:
            return tuple(
                Phase1ArtifactReceipt(
                    name=artifact.name,
                    bytes=len(artifact.body),
                    sha256=artifact.sha256,
                )
                for artifact in snapshots
            )
        except ValueError as error:
            raise BacktestReplayServiceError("artifact receipt is invalid") from error

    @staticmethod
    def _records_digest(records: tuple[Phase1ArtifactReceipt, ...]) -> str:
        return canonical_json_digest([
            asdict(record) for record in sorted(records, key=lambda item: item.name)
        ])

    def _validate_snapshot(
        self,
        receipt: Phase1ReplayReceipt,
        snapshots: tuple[BacktestArtifactSnapshot, ...],
        *,
        bind_project_source: bool = True,
    ) -> ValidatedBacktestBundle:
        if type(receipt) is not Phase1ReplayReceipt:
            raise BacktestReplayServiceError("receipt type differs")
        try:
            Phase1ReplayReceipt.__post_init__(receipt)
        except ValueError as error:
            raise BacktestReplayServiceError("receipt fields differ") from error
        if (
            receipt.output_root != self.output_root
            or receipt.schema != BUNDLE_SCHEMA
            or receipt.status != "READY"
            or receipt.frozen_input_digest != EXPECTED_FROZEN_DIGEST
            or tuple(item.name for item in snapshots) != EXPECTED_BUNDLE_FILES
        ):
            raise BacktestReplayServiceError("receipt identity differs")
        records = self._records(snapshots)
        if (
            records != receipt.artifacts
            or self._records_digest(records) != receipt.bundle_digest
        ):
            raise BacktestReplayServiceError("receipt does not match artifact bytes")
        bodies = {artifact.name: artifact.body for artifact in snapshots}
        try:
            view = self._validate_artifact_bodies(
                receipt,
                bodies,
                bind_project_source=bind_project_source,
            )
            return ValidatedBacktestBundle(view, receipt, bodies)
        except BacktestReplayServiceError:
            raise
        except (ArithmeticError, KeyError, TypeError, ValueError) as error:
            raise BacktestReplayServiceError(
                "replay bundle semantic validation failed"
            ) from error

    @staticmethod
    def _json_object(body: bytes, label: str) -> dict[str, object]:
        def reject_duplicates(
            pairs: list[tuple[str, object]],
        ) -> dict[str, object]:
            value: dict[str, object] = {}
            for key, item in pairs:
                if key in value:
                    raise BacktestReplayServiceError(
                        f"{label} contains duplicate keys"
                    )
                value[key] = item
            return value

        def reject_constant(value: str) -> object:
            raise BacktestReplayServiceError(
                f"{label} contains a non-finite number"
            )

        try:
            value = json.loads(
                body.decode("utf-8"),
                object_pairs_hook=reject_duplicates,
                parse_constant=reject_constant,
            )
        except BacktestReplayServiceError:
            raise
        except (UnicodeError, json.JSONDecodeError) as error:
            raise BacktestReplayServiceError(f"{label} is invalid JSON") from error
        if type(value) is not dict:
            raise BacktestReplayServiceError(f"{label} root must be an object")
        return value

    @staticmethod
    def _canonical_json(payload: object, *, pretty: bool = False) -> bytes:
        options: dict[str, object] = {
            "ensure_ascii": False,
            "sort_keys": True,
            "allow_nan": False,
        }
        if pretty:
            options["indent"] = 2
        else:
            options["separators"] = (",", ":")
        return (json.dumps(payload, **options) + "\n").encode("utf-8")

    @staticmethod
    def _finite_number(value: object, label: str) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise BacktestReplayServiceError(f"{label} must be numeric")
        number = float(value)
        if not math.isfinite(number):
            raise BacktestReplayServiceError(f"{label} must be finite")
        return number

    @staticmethod
    def _is_sha256(value: object) -> bool:
        return (
            type(value) is str
            and len(value) == 64
            and all(character in "0123456789abcdef" for character in value)
        )

    @staticmethod
    def _exact_nonnegative_integer(value: object, label: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise BacktestReplayServiceError(
                f"{label} must be a non-negative integer"
            )
        return value

    @staticmethod
    def _exact_typed_mapping(
        value: object, expected: Mapping[str, object],
    ) -> bool:
        return (
            type(value) is dict
            and set(value) == set(expected)
            and all(
                type(value[key]) is type(item) and value[key] == item
                for key, item in expected.items()
            )
        )

    @classmethod
    def _exact_json_value(cls, value: object, expected: object) -> bool:
        return cls._canonical_json(value) == cls._canonical_json(expected)

    def _source_bound_projection(self) -> dict[str, object]:
        try:
            manifest, source, holdout = _load_verified_source(self.project_root)
            if (
                not self._exact_json_value(
                    asdict(manifest), EXPECTED_FROZEN_MANIFEST,
                )
                or holdout != KOSPI200_FROZEN_HOLDOUT_V1
            ):
                raise BacktestReplayServiceError(
                    "project frozen source identity differs"
                )
            development_source = source.loc[
                source["date"].lt(holdout.holdout_start)
            ].reset_index(drop=True)
            if len(development_source) != holdout.development_observations:
                raise BacktestReplayServiceError(
                    "project development source coverage differs"
                )

            features = build_kospi200_features(development_source)
            labels = build_forward_labels(development_source)
            thresholds = SignalThresholds()
            signals = build_descriptive_signals(features, thresholds)
            if (
                tuple(signals.columns) != EXPECTED_SIGNAL_COLUMNS
                or len(signals) != EXPECTED_SIGNAL_ROWS
            ):
                raise BacktestReplayServiceError(
                    "project source signal projection differs"
                )
            signals_body = signals.to_csv(
                index=False, lineterminator="\n",
            ).encode("utf-8")
            metrics = evaluate_signals(signals, labels)
            grid = [
                {**row, "thresholds": asdict(row["thresholds"])}
                for row in evaluate_predefined_walk_forward(features, labels)
            ]
            crises = replay_crisis_windows(
                signals,
                labels,
                holdout_start=holdout.holdout_start,
            )
            ablation = [
                asdict(step)
                for step in build_ablation_plan({
                    "PRICE": FeatureFamilyStatus.AVAILABLE,
                    "VOLATILITY": FeatureFamilyStatus.AVAILABLE,
                    "FX": FeatureFamilyStatus.BLOCKED,
                    "BREADTH": FeatureFamilyStatus.BLOCKED,
                    "FLOW": FeatureFamilyStatus.BLOCKED,
                    "DERIVATIVES": FeatureFamilyStatus.BLOCKED,
                })
            ]
            portfolio = simulate_kospi200_risk_off_portfolio(
                development_source,
                signals,
                holdout,
            )
            return {
                "signals_body": signals_body,
                "thresholds": asdict(thresholds),
                "metrics": metrics,
                "grid": grid,
                "crises": crises,
                "ablation": ablation,
                "portfolio_root": {
                    "schema": "market-backtest-close-proxy-ledger/v1",
                    "simulation": asdict(portfolio),
                },
                "feature_versions": [
                    f"{definition.feature_name}:v{definition.feature_version}"
                    for definition in FEATURE_DEFINITIONS
                ],
                "code_tree_digest": phase1_code_digest(self.project_root),
            }
        except BacktestReplayServiceError:
            raise
        except Exception as error:
            raise BacktestReplayServiceError(
                "project frozen source could not be verified and replayed"
            ) from error

    def _validate_threshold_contract(
        self,
        result: Mapping[str, object],
        experiment: Mapping[str, object],
    ) -> None:
        active = asdict(SignalThresholds())
        grid_contract = tuple(
            (baseline_id, asdict(thresholds))
            for baseline_id, thresholds in PREDEFINED_SMALL_GRID
        )
        if not self._exact_typed_mapping(result.get("thresholds"), active):
            raise BacktestReplayServiceError("active signal thresholds differ")
        grid = result.get("predefined_small_grid")
        if type(grid) is not list or len(grid) != len(grid_contract):
            raise BacktestReplayServiceError("predefined signal grid differs")
        for row, (baseline_id, thresholds) in zip(grid, grid_contract, strict=True):
            if (
                type(row) is not dict
                or set(row) != {
                    "baseline_id", "folds", "test_observations",
                    "thresholds", "metrics",
                }
                or row.get("baseline_id") != baseline_id
                or type(row.get("folds")) is not int
                or row["folds"] != 22
                or type(row.get("test_observations")) is not int
                or row["test_observations"] != 5_420
                or not self._exact_typed_mapping(row.get("thresholds"), thresholds)
                or type(row.get("metrics")) is not dict
                or not row["metrics"]
                or any(
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(float(value))
                    for value in row["metrics"].values()
                )
            ):
                raise BacktestReplayServiceError("predefined signal grid differs")
        expected_digest = canonical_json_digest({
            "active": active,
            "grid": list(grid_contract),
        })
        if experiment.get("threshold_values_digest") != expected_digest:
            raise BacktestReplayServiceError("threshold contract digest differs")

    def _validate_crisis_contract(self, value: object) -> None:
        if type(value) is not list or len(value) != len(CRISIS_WINDOWS):
            raise BacktestReplayServiceError("crisis replay windows differ")
        holdout_start = KOSPI200_FROZEN_HOLDOUT_V1.holdout_start
        diagnostic_keys = {
            "event", "start", "end", "status", "observations",
            "risk_off_observations", "adverse_observations", "event_precision",
            "event_recall", "first_risk_off_date", "worst_forward_20d_drawdown",
            "mean_forward_20d_return", "holdout_observations_excluded",
        }
        sealed_keys = {
            "event", "start", "end", "status", "holdout_observations_excluded",
        }
        for row, (event, (start, end)) in zip(
            value, CRISIS_WINDOWS.items(), strict=True,
        ):
            expected_sealed = start >= holdout_start
            if (
                type(row) is not dict
                or row.get("event") != event
                or row.get("start") != start
                or row.get("end") != end
            ):
                raise BacktestReplayServiceError("crisis replay identity differs")
            if expected_sealed:
                if (
                    set(row) != sealed_keys
                    or row.get("status") != "UNTOUCHED_HOLDOUT"
                    or row.get("holdout_observations_excluded") != "NOT_INSPECTED"
                ):
                    raise BacktestReplayServiceError(
                        "sealed crisis window exposed outcomes"
                    )
                continue
            if (
                set(row) != diagnostic_keys
                or row.get("status") != "DIAGNOSTIC_ONLY"
                or end >= holdout_start
                or type(row.get("holdout_observations_excluded")) is not int
                or row["holdout_observations_excluded"] != 0
            ):
                raise BacktestReplayServiceError(
                    "development crisis window crosses holdout"
                )
            for key in (
                "observations", "risk_off_observations", "adverse_observations",
            ):
                if type(row.get(key)) is not int or row[key] < 0:
                    raise BacktestReplayServiceError(
                        "development crisis counts differ"
                    )
            if (
                row["observations"] < 1
                or row["risk_off_observations"] > row["observations"]
                or row["adverse_observations"] > row["observations"]
            ):
                raise BacktestReplayServiceError(
                    "development crisis counts differ"
                )
            for key in (
                "event_precision", "event_recall", "worst_forward_20d_drawdown",
                "mean_forward_20d_return",
            ):
                self._finite_number(row.get(key), f"crisis {key}")
            first = row.get("first_risk_off_date")
            if type(first) is not str or not start <= first <= end:
                raise BacktestReplayServiceError(
                    "development crisis first signal date differs"
                )

    def _validate_artifact_bodies(
        self,
        receipt: Phase1ReplayReceipt,
        bodies: Mapping[str, bytes],
        *,
        bind_project_source: bool,
    ) -> BacktestExperimentView:
        bundle = self._json_object(bodies["bundle.json"], "bundle manifest")
        if self._canonical_json(bundle) != bodies["bundle.json"]:
            raise BacktestReplayServiceError("bundle manifest is not canonical")
        base_records = tuple(
            record for record in receipt.artifacts if record.name != "bundle.json"
        )
        if (
            set(bundle) != {
                "schema", "frozen_input_digest", "artifact_set_sha256", "artifacts",
            }
            or bundle.get("schema") != BUNDLE_SCHEMA
            or bundle.get("frozen_input_digest") != EXPECTED_FROZEN_DIGEST
            or bundle.get("artifact_set_sha256") != self._records_digest(base_records)
            or not self._exact_json_value(
                bundle.get("artifacts"),
                [asdict(record) for record in base_records],
            )
        ):
            raise BacktestReplayServiceError("bundle manifest receipts differ")

        result = self._json_object(bodies["result.json"], "result")
        experiments = self._json_object(bodies["experiments.json"], "experiments")
        portfolio_root = self._json_object(
            bodies["portfolio_ledger.json"], "portfolio ledger",
        )
        if self._canonical_json(result, pretty=True) != bodies["result.json"]:
            raise BacktestReplayServiceError("result is not canonical")
        if self._canonical_json(experiments) != bodies["experiments.json"]:
            raise BacktestReplayServiceError("experiments are not canonical")
        if self._canonical_json(portfolio_root) != bodies["portfolio_ledger.json"]:
            raise BacktestReplayServiceError("portfolio ledger is not canonical")

        manifest = result.get("frozen_manifest")
        holdout = result.get("untouched_holdout_policy")
        if (
            set(result) != {
                "status", "frozen_manifest", "thresholds",
                "untouched_holdout_policy", "development_metrics", "metrics",
                "metrics_scope", "predefined_small_grid",
                "crisis_replay_development_only", "crisis_replay",
                "feature_family_ablation_plan", "portfolio_foundation",
                "experiment_id",
            }
            or type(manifest) is not dict
            or set(manifest) != set(EXPECTED_FROZEN_MANIFEST)
            or any(
                type(manifest.get(key)) is not type(expected)
                or manifest.get(key) != expected
                for key, expected in EXPECTED_FROZEN_MANIFEST.items()
            )
            or result.get("status") != EXPECTED_STATUS
            or result.get("experiment_id") != EXPECTED_EXPERIMENT_ID
            or result.get("metrics_scope") != EXPECTED_METRICS_SCOPE
            or not self._exact_json_value(
                result.get("development_metrics"), result.get("metrics"),
            )
            or type(result.get("metrics")) is not dict
            or not result["metrics"]
            or type(result.get("thresholds")) is not dict
            or not result["thresholds"]
            or type(result.get("predefined_small_grid")) is not list
            or not result["predefined_small_grid"]
            or type(result.get("feature_family_ablation_plan")) is not list
            or not result["feature_family_ablation_plan"]
            or not self._exact_json_value(
                result.get("crisis_replay_development_only"),
                result.get("crisis_replay"),
            )
            or not self._exact_json_value(
                holdout, asdict(KOSPI200_FROZEN_HOLDOUT_V1),
            )
            or type(holdout.get("results_reviewed")) is not bool
            or holdout["results_reviewed"] is not False
        ):
            raise BacktestReplayServiceError("result frozen or holdout scope differs")
        self._validate_crisis_contract(result["crisis_replay"])

        if (
            set(experiments) != {"version", "experiments"}
            or type(experiments.get("version")) is not int
            or experiments["version"] != 1
            or type(experiments.get("experiments")) is not list
            or len(experiments["experiments"]) != 1
            or type(experiments["experiments"][0]) is not dict
        ):
            raise BacktestReplayServiceError("experiment registry shape differs")
        experiment = experiments["experiments"][0]
        projection = (
            self._source_bound_projection() if bind_project_source else None
        )
        expected_result_path = self.output_root / "result.json"
        try:
            expected_result_artifact = expected_result_path.relative_to(
                self.project_root
            ).as_posix()
        except ValueError:
            expected_result_artifact = expected_result_path.as_posix()
        expected_feature_versions = [
            f"{definition.feature_name}:v{definition.feature_version}"
            for definition in FEATURE_DEFINITIONS
        ]
        if (
            set(experiment) != EXPECTED_EXPERIMENT_KEYS
            or experiment.get("experiment_id") != EXPECTED_EXPERIMENT_ID
            or experiment.get("frozen_input_digest") != EXPECTED_FROZEN_DIGEST
            or experiment.get("feature_set") != ["PRICE", "VOLATILITY"]
            or experiment.get("feature_versions") != expected_feature_versions
            or experiment.get("label_version") != "forward_outcomes:v1"
            or experiment.get("split_policy") != "PURGED_EXPANDING_WALK_FORWARD"
            or type(experiment.get("purge")) is not int
            or experiment["purge"] != 60
            or type(experiment.get("embargo")) is not int
            or experiment["embargo"] != 5
            or type(experiment.get("label_horizon_trading_days")) is not int
            or experiment["label_horizon_trading_days"]
            != MAX_LABEL_HORIZON_TRADING_DAYS
            or experiment.get("threshold_rule")
            != "PREDEFINED_SMALL_GRID_NO_WINNER_SELECTION"
            or experiment.get("code_version") != "BACKTEST_PHASE1_FOUNDATION_V1"
            or experiment.get("signal_pit_status") != "PIT_SAFE_EOD_T_PLUS_1"
            or any(
                not self._is_sha256(experiment.get(key))
                for key in ("code_tree_digest", "threshold_values_digest")
            )
            or type(experiment.get("holdout_results_reviewed")) is not bool
            or experiment["holdout_results_reviewed"] is not False
            or experiment.get("signals_artifact_digest")
            != artifact_bytes_digest(bodies["signals.csv"])
            or experiment.get("result_artifact_digest")
            != artifact_bytes_digest(bodies["result.json"])
            or experiment.get("result_artifact") != expected_result_artifact
            or (
                projection is not None
                and experiment.get("code_tree_digest")
                != projection["code_tree_digest"]
                and not (
                    experiment.get("code_tree_digest")
                    == _ACCEPTED_LEGACY_CODE_TREE_DIGEST
                    and projection["code_tree_digest"]
                    == _ACCEPTED_EXPLICIT_CODE_TREE_DIGEST
                )
            )
        ):
            raise BacktestReplayServiceError("experiment digest or holdout scope differs")
        self._validate_threshold_contract(result, experiment)

        if projection is not None and (
            bodies["signals.csv"] != projection["signals_body"]
            or not self._exact_json_value(
                result.get("thresholds"), projection["thresholds"],
            )
            or not self._exact_json_value(
                result.get("metrics"), projection["metrics"],
            )
            or not self._exact_json_value(
                result.get("development_metrics"), projection["metrics"],
            )
            or not self._exact_json_value(
                result.get("predefined_small_grid"), projection["grid"],
            )
            or not self._exact_json_value(
                result.get("crisis_replay"), projection["crises"],
            )
            or not self._exact_json_value(
                result.get("crisis_replay_development_only"),
                projection["crises"],
            )
            or not self._exact_json_value(
                result.get("feature_family_ablation_plan"),
                projection["ablation"],
            )
            or not self._exact_json_value(
                portfolio_root, projection["portfolio_root"],
            )
        ):
            raise BacktestReplayServiceError(
                "bundle differs from the project frozen-source replay"
            )

        signal_rows = self._validate_signals(bodies["signals.csv"])
        portfolio = self._validate_portfolio(portfolio_root, signal_rows)
        foundation = result.get("portfolio_foundation")
        portfolio_digest = artifact_bytes_digest(bodies["portfolio_ledger.json"])
        if (
            type(foundation) is not dict
            or set(foundation) != {
                "status", "instrument_claim", "metrics",
                "ledger_artifact", "ledger_artifact_digest",
            }
            or foundation.get("status") != PORTFOLIO_STATUS
            or foundation.get("instrument_claim") != INSTRUMENT_CLAIM
            or foundation.get("ledger_artifact") != "portfolio_ledger.json"
            or foundation.get("ledger_artifact_digest") != portfolio_digest
            or not self._exact_json_value(
                foundation.get("metrics"),
                portfolio_root["simulation"]["metrics"],
            )
        ):
            raise BacktestReplayServiceError("result portfolio binding differs")

        try:
            base_view = BacktestResultService(
                self.project_root,
                self.output_root / "result.json",
            )._parse(result, self._display_source())
        except (TypeError, ValueError, KeyError) as error:
            raise BacktestReplayServiceError("result GUI projection is invalid") from error
        return replace(
            base_view,
            portfolio_scope=(
                "DEVELOPMENT-ONLY CLOSE PROXY · NOT AN EXECUTABLE INSTRUMENT"
            ),
            holdout=BacktestHoldoutView(**holdout),
            portfolio=portfolio,
            bundle_receipt=receipt,
        )

    @staticmethod
    def _validate_signals(body: bytes) -> tuple[dict[str, str], ...]:
        try:
            text = body.decode("utf-8")
            rows = list(csv.reader(io.StringIO(text, newline="")))
        except (UnicodeError, csv.Error) as error:
            raise BacktestReplayServiceError("signals CSV is invalid") from error
        if (
            not text.endswith("\n")
            or "\r" in text
            or len(rows) != EXPECTED_SIGNAL_ROWS + 1
        ):
            raise BacktestReplayServiceError("signals CSV framing differs")
        header = rows[0]
        folded = [name.casefold() for name in header]
        if (
            tuple(header) != EXPECTED_SIGNAL_COLUMNS
            or len(header) != len(set(header))
            or len(folded) != len(set(folded))
            or any(
                name.startswith(("forward_", "label_", "outcome_"))
                or name in {"mae_20d", "mfe_20d"}
                for name in folded
            )
        ):
            raise BacktestReplayServiceError("signals schema crosses outcome scope")

        result: list[dict[str, str]] = []
        previous_date: str | None = None
        for values in rows[1:]:
            if len(values) != len(header):
                raise BacktestReplayServiceError("signals row width differs")
            row = dict(zip(header, values, strict=True))
            observation_date = row["observation_date"]
            try:
                parsed = date.fromisoformat(observation_date)
                usable_date = date.fromisoformat(row["usable_from"][:10])
            except (TypeError, ValueError) as error:
                raise BacktestReplayServiceError("signals date is invalid") from error
            flag_names = (
                "high_realized_volatility", "large_drawdown",
                "below_moving_average", "negative_momentum",
            )
            flags_are_boolean = all(
                row[name] in {"True", "False"} for name in flag_names
            )
            score = sum(row[name] == "True" for name in flag_names)
            if (
                parsed.isoformat() != observation_date
                or row["usable_from"]
                != f"{usable_date.isoformat()}T09:00:00+09:00"
                or not observation_date < usable_date.isoformat()
                or observation_date >= KOSPI200_FROZEN_HOLDOUT_V1.holdout_start
                or (previous_date is not None and previous_date >= observation_date)
                or row["ticker"] != "1028"
                or row["date_semantics"] != "KRX_TRADING_DATE_DAILY_FINAL"
                or row["source_dataset"] != EXPECTED_DATASET
                or row["source_contract_version"] != "1"
                or row["pit_status"] != "PIT_SAFE_EOD_T_PLUS_1"
                or row["signal_version"] != "1"
                or row["risk_off_signal"] not in {"True", "False"}
                or row["risk_score"] not in {"0", "1", "2", "3", "4"}
                or not flags_are_boolean
                or int(row["risk_score"]) != score
                or (row["risk_off_signal"] == "True") != (score >= 2)
            ):
                raise BacktestReplayServiceError("signals retained-row identity differs")
            previous_date = observation_date
            result.append(row)
        return tuple(result)

    def _display_source(self) -> str:
        try:
            return (self.output_root / "result.json").relative_to(
                self.project_root
            ).as_posix()
        except ValueError:
            return (self.output_root / "result.json").as_posix()

    def _validate_portfolio(
        self,
        root: Mapping[str, object],
        signals: tuple[dict[str, str], ...],
    ) -> BacktestPortfolioView:
        if set(root) != {"schema", "simulation"} or root.get("schema") != (
            "market-backtest-close-proxy-ledger/v1"
        ):
            raise BacktestReplayServiceError("portfolio root schema differs")
        simulation = root.get("simulation")
        if type(simulation) is not dict or set(simulation) != {
            "status", "instrument_claim", "source_dataset",
            "source_contract_version", "holdout_policy_id", "holdout_start",
            "assumptions", "ledger", "metrics",
        }:
            raise BacktestReplayServiceError("portfolio simulation schema differs")
        assumptions = simulation.get("assumptions")
        expected_assumptions = asdict(CLOSE_PROXY_V1)
        if (
            simulation.get("status") != PORTFOLIO_STATUS
            or simulation.get("instrument_claim") != INSTRUMENT_CLAIM
            or simulation.get("source_dataset") != EXPECTED_DATASET
            or type(simulation.get("source_contract_version")) is not int
            or simulation["source_contract_version"] != EXPECTED_CONTRACT_VERSION
            or simulation.get("holdout_policy_id")
            != KOSPI200_FROZEN_HOLDOUT_V1.policy_id
            or simulation.get("holdout_start")
            != KOSPI200_FROZEN_HOLDOUT_V1.holdout_start
            or type(assumptions) is not dict
            or set(assumptions) != set(expected_assumptions)
            or any(
                type(assumptions.get(key)) is not type(expected)
                or assumptions.get(key) != expected
                for key, expected in expected_assumptions.items()
            )
        ):
            raise BacktestReplayServiceError("portfolio fixed scope differs")
        ledger = simulation.get("ledger")
        metrics = simulation.get("metrics")
        if (
            type(ledger) is not list
            or len(ledger) < 2
            or len(ledger) != len(signals) + 1
            or type(metrics) is not dict
        ):
            raise BacktestReplayServiceError("portfolio content is incomplete")
        if set(metrics) != set(self._PORTFOLIO_METRIC_NAMES):
            raise BacktestReplayServiceError("portfolio metric schema differs")

        observations = self._exact_nonnegative_integer(
            metrics.get("observations"), "portfolio observations",
        )
        intervals = self._exact_nonnegative_integer(
            metrics.get("intervals"), "portfolio intervals",
        )
        trade_count = self._exact_nonnegative_integer(
            metrics.get("trade_count"), "portfolio trade_count",
        )
        metric_values: dict[str, float] = {}
        for name, value in metrics.items():
            if name in {"observations", "intervals", "trade_count"}:
                continue
            if type(value) is not float:
                raise BacktestReplayServiceError(
                    f"portfolio {name} must be exact floating-point"
                )
            metric_values[name] = self._finite_number(value, f"portfolio {name}")
        if observations != len(ledger) or intervals != len(ledger) - 1:
            raise BacktestReplayServiceError("portfolio observation counts differ")

        points: list[BacktestCurvePoint] = []
        dates: list[str] = []
        rows: list[dict[str, object]] = []
        for index, value in enumerate(ledger):
            if type(value) is not dict or set(value) != self._LEDGER_KEYS:
                raise BacktestReplayServiceError("portfolio ledger row schema differs")
            try:
                parsed_date = date.fromisoformat(value["date"])
            except (TypeError, ValueError) as error:
                raise BacktestReplayServiceError("portfolio date is invalid") from error
            if parsed_date.isoformat() != value["date"]:
                raise BacktestReplayServiceError("portfolio date is not canonical")
            if value["date"] >= KOSPI200_FROZEN_HOLDOUT_V1.holdout_start:
                raise BacktestReplayServiceError("portfolio entered untouched holdout")
            if dates and dates[-1] >= value["date"]:
                raise BacktestReplayServiceError("portfolio dates are not increasing")
            dates.append(value["date"])
            for name in self._LEDGER_NUMERIC_KEYS:
                if type(value[name]) is not float:
                    raise BacktestReplayServiceError(
                        f"ledger {name} must be exact floating-point"
                    )
                self._finite_number(value[name], f"ledger {name}")
            for name in ("position_before", "target_position"):
                if type(value[name]) is not int or value[name] not in {0, 1}:
                    raise BacktestReplayServiceError("portfolio position differs")
            if index == 0:
                if (
                    any(value[name] is not None for name in (
                    "signal_observation_date", "usable_from", "risk_off_signal",
                    ))
                    or value["position_before"] != CLOSE_PROXY_V1.cash_position
                    or value["target_position"] != CLOSE_PROXY_V1.cash_position
                ):
                    raise BacktestReplayServiceError("initial ledger row differs")
            else:
                signal = signals[index - 1]
                if (
                    type(value["signal_observation_date"]) is not str
                    or type(value["usable_from"]) is not str
                    or type(value["risk_off_signal"]) is not bool
                    or value["signal_observation_date"] != signal["observation_date"]
                    or value["signal_observation_date"] != rows[-1]["date"]
                    or value["usable_from"] != signal["usable_from"]
                    or value["usable_from"] != f'{value["date"]}T09:00:00+09:00'
                    or value["risk_off_signal"]
                    != (signal["risk_off_signal"] == "True")
                    or value["position_before"] != rows[-1]["target_position"]
                    or value["target_position"]
                    != (
                        CLOSE_PROXY_V1.cash_position
                        if value["risk_off_signal"]
                        else CLOSE_PROXY_V1.long_position
                    )
                ):
                    raise BacktestReplayServiceError(
                        "portfolio signal identity differs"
                    )
            nav = float(value["nav"])
            drawdown = float(value["drawdown"])
            if (
                nav <= 0.0
                or float(value["close"]) <= 0.0
                or not -1.0 < drawdown <= 0.0
                or any(float(value[name]) < 0.0 for name in (
                    "trade_notional", "turnover", "transaction_cost", "cash",
                    "units", "asset_value", "nav_before_cost",
                ))
            ):
                raise BacktestReplayServiceError("portfolio ledger values differ")
            rows.append(value)
            points.append(BacktestCurvePoint(
                value["date"], nav, drawdown, value["target_position"],
            ))

        def reconcile(actual: object, expected: float, label: str) -> None:
            if not math.isclose(
                float(actual), expected, rel_tol=1e-11, abs_tol=1e-13,
            ):
                raise BacktestReplayServiceError(f"ledger {label} does not reconcile")

        initial = rows[0]
        reconcile(initial["cash"], CLOSE_PROXY_V1.initial_nav, "initial cash")
        reconcile(initial["nav_before_cost"], CLOSE_PROXY_V1.initial_nav, "initial NAV")
        reconcile(initial["nav"], CLOSE_PROXY_V1.initial_nav, "initial NAV")
        for name in (
            "market_return", "gross_portfolio_return", "trade_notional",
            "turnover", "transaction_cost", "units", "asset_value",
            "net_return", "drawdown",
        ):
            reconcile(initial[name], 0.0, f"initial {name}")

        running_peak = CLOSE_PROXY_V1.initial_nav
        for index, row in enumerate(rows[1:], start=1):
            prior = rows[index - 1]
            close = float(row["close"])
            prior_close = float(prior["close"])
            asset_before = float(prior["units"]) * close
            nav_before = float(prior["cash"]) + asset_before
            market_return = close / prior_close - 1.0
            gross_return = nav_before / float(prior["nav"]) - 1.0
            position_before = int(row["position_before"])
            target_position = int(row["target_position"])
            trade_notional = 0.0
            transaction_cost = 0.0
            cash = float(prior["cash"])
            units = float(prior["units"])
            cost_rate = CLOSE_PROXY_V1.one_way_transaction_cost_rate
            if target_position != position_before:
                if (
                    position_before == CLOSE_PROXY_V1.cash_position
                    and target_position == CLOSE_PROXY_V1.long_position
                ):
                    trade_notional = nav_before / (1.0 + cost_rate)
                    transaction_cost = trade_notional * cost_rate
                    cash = 0.0
                    units = trade_notional / close
                elif (
                    position_before == CLOSE_PROXY_V1.long_position
                    and target_position == CLOSE_PROXY_V1.cash_position
                ):
                    trade_notional = asset_before
                    transaction_cost = trade_notional * cost_rate
                    cash = float(prior["cash"]) + trade_notional - transaction_cost
                    units = 0.0
                else:
                    raise BacktestReplayServiceError(
                        "portfolio position transition differs"
                    )
            asset_value = units * close
            nav = cash + asset_value
            turnover = trade_notional / nav_before
            net_return = nav / float(prior["nav"]) - 1.0
            running_peak = max(running_peak, nav)
            drawdown = nav / running_peak - 1.0
            expected_row = {
                "market_return": market_return,
                "gross_portfolio_return": gross_return,
                "trade_notional": trade_notional,
                "turnover": turnover,
                "transaction_cost": transaction_cost,
                "cash": cash,
                "units": units,
                "asset_value": asset_value,
                "nav_before_cost": nav_before,
                "nav": nav,
                "net_return": net_return,
                "drawdown": drawdown,
            }
            for name, expected_value in expected_row.items():
                reconcile(row[name], expected_value, name)
            reconcile(row["nav"], nav_before - transaction_cost, "self financing")

        assumptions = expected_assumptions
        initial_nav = float(rows[0]["nav"])
        ending_nav = float(rows[-1]["nav"])
        expected = {
            "initial_nav": initial_nav,
            "ending_nav": ending_nav,
            "total_return": ending_nav / initial_nav - 1.0,
            "max_drawdown": min(float(row["drawdown"]) for row in rows),
            "total_turnover": sum(float(row["turnover"]) for row in rows),
            "average_long_exposure": sum(
                int(row["position_before"]) for row in rows[1:]
            ) / intervals,
            "transaction_cost_paid": sum(
                float(row["transaction_cost"]) for row in rows
            ),
        }
        expected["annualized_return"] = (
            (ending_nav / initial_nav)
            ** (assumptions["annualization_sessions"] / intervals)
            - 1.0
        )
        returns = [float(row["net_return"]) for row in rows[1:]]
        mean_return = sum(returns) / len(returns)
        expected["annualized_volatility"] = 0.0
        if len(returns) >= 2:
            variance = sum(
                (value - mean_return) ** 2 for value in returns
            ) / (len(returns) - 1)
            expected["annualized_volatility"] = math.sqrt(
                variance * assumptions["annualization_sessions"]
            )
        for name, value in expected.items():
            if not math.isclose(
                metric_values[name], value, rel_tol=1e-12, abs_tol=1e-14,
            ):
                raise BacktestReplayServiceError(
                    f"portfolio {name} does not reconcile"
                )
        if trade_count != sum(float(row["trade_notional"]) > 0.0 for row in rows):
            raise BacktestReplayServiceError("portfolio trade_count does not reconcile")
        if not math.isclose(
            metric_values["initial_nav"], CLOSE_PROXY_V1.initial_nav,
            rel_tol=0.0, abs_tol=0.0,
        ):
            raise BacktestReplayServiceError("portfolio initial NAV differs")

        return BacktestPortfolioView(
            status=PORTFOLIO_STATUS,
            instrument_claim=INSTRUMENT_CLAIM,
            assumptions=assumptions,
            initial_nav=metric_values["initial_nav"],
            ending_nav=metric_values["ending_nav"],
            total_return=metric_values["total_return"],
            annualized_return=metric_values["annualized_return"],
            annualized_volatility=metric_values["annualized_volatility"],
            max_drawdown=metric_values["max_drawdown"],
            trade_count=trade_count,
            total_turnover=metric_values["total_turnover"],
            average_long_exposure=metric_values["average_long_exposure"],
            transaction_cost_paid=metric_values["transaction_cost_paid"],
            curve=tuple(points),
        )


__all__ = [
    "BacktestArtifactSnapshot",
    "BacktestCurvePoint",
    "BacktestExperimentView",
    "BacktestExportReceipt",
    "BacktestHoldoutView",
    "BacktestInputCoverage",
    "BacktestPortfolioPoint",
    "BacktestPortfolioView",
    "BacktestReplayService",
    "BacktestReplayServiceError",
    "BacktestResultService",
    "BacktestRunOutcome",
    "BacktestWorkflowError",
    "CrisisReplaySummary",
    "NamedNumber",
    "ValidatedBacktestBundle",
]
