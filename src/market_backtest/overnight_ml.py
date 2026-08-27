"""Bounded, resumable, development-only ML research over the frozen input.

The runner verifies the accepted KOSPI200 input, slices source rows before the
sealed holdout boundary, and only then builds features and outcome labels.  It
uses a single-process Optuna/SQLite study so interruption never requires a new
provider call or a loss of completed trials.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any

import numpy as np
import optuna
import pandas as pd
import sklearn
from sklearn.base import ClassifierMixin
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, brier_score_loss, log_loss
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from market_features.kospi200 import FEATURE_DEFINITIONS, build_kospi200_features

from .experiments import code_tree_digest
from .holdout import CoverageHoldout
from .labels import MAX_LABEL_HORIZON_TRADING_DAYS, build_forward_labels
from .phase1_replay import (
    EXPECTED_FROZEN_DIGEST,
    Phase1ReplayError,
    _absolute_plain_path,
    _assert_output_scope,
    _load_verified_source,
)
from .walk_forward import WalkForwardSplit, expanding_walk_forward


ML_SCHEMA = "market-backtest-overnight-ml/v1"
DEFAULT_OUTPUT_RELATIVE = Path("artifacts/backtest/ml_overnight")
DEFAULT_DURATION_SECONDS = 8 * 60 * 60
MAX_DURATION_SECONDS = 8 * 60 * 60
BASE_RANDOM_SEED = 20260826
TARGET_COLUMN = "forward_max_drawdown_20d"
TARGET_EVENT_THRESHOLD = -0.10
FEATURE_COLUMNS = tuple(
    definition.feature_name for definition in FEATURE_DEFINITIONS
)
MINIMUM_TRAIN = 2520
TEST_SIZE = 252
PURGE = MAX_LABEL_HORIZON_TRADING_DAYS
EMBARGO = 5


class OvernightMLError(RuntimeError):
    """Raised when an overnight study cannot preserve its safety boundary."""


@dataclass(frozen=True, slots=True)
class OvernightMLRequest:
    project_root: Path
    output_root: Path | None = None
    duration_seconds: int = DEFAULT_DURATION_SECONDS
    max_trials: int | None = None
    keep_awake: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_root", Path(self.project_root))
        if self.output_root is not None:
            object.__setattr__(self, "output_root", Path(self.output_root))
        if (
            type(self.duration_seconds) is not int
            or not 1 <= self.duration_seconds <= MAX_DURATION_SECONDS
        ):
            raise ValueError("duration_seconds must be in [1, 28800]")
        if self.max_trials is not None and (
            type(self.max_trials) is not int or self.max_trials < 1
        ):
            raise ValueError("max_trials must be a positive integer")
        if type(self.keep_awake) is not bool:
            raise ValueError("keep_awake must be boolean")


@dataclass(frozen=True, slots=True)
class MLDevelopmentData:
    frame: pd.DataFrame
    splits: tuple[WalkForwardSplit, ...]
    holdout: CoverageHoldout

    def __post_init__(self) -> None:
        if self.frame.empty or not self.splits:
            raise ValueError("ML development data must be non-empty and split")


@dataclass(frozen=True, slots=True)
class OvernightMLReceipt:
    output_root: Path
    status: str
    completed_trials: int
    pruned_trials: int
    failed_trials: int
    consumed_seconds: float
    remaining_seconds: float
    best_trial_number: int | None
    holdout_results_reviewed: bool = False

    def __post_init__(self) -> None:
        if self.holdout_results_reviewed:
            raise ValueError("overnight ML must not review the sealed holdout")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _json_bytes(value: object) -> bytes:
    return (
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{os.getpid()}.tmp"
    try:
        with temporary.open("xb") as stream:
            stream.write(_json_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _read_exact_json(path: Path, *, label: str) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        raise OvernightMLError(f"{label} is not an exact regular file")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise OvernightMLError(f"{label} is unreadable") from error
    if type(value) is not dict:
        raise OvernightMLError(f"{label} schema differs")
    return value


@contextmanager
def _exclusive_study_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open("a+b")
    try:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise OvernightMLError("another overnight ML runner owns this output") from error
        yield
    finally:
        try:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()


@contextmanager
def _keep_system_awake(enabled: bool) -> Iterator[None]:
    if not enabled or os.name != "nt":
        yield
        return
    import ctypes

    continuous = 0x80000000
    system_required = 0x00000001
    result = ctypes.windll.kernel32.SetThreadExecutionState(  # type: ignore[attr-defined]
        continuous | system_required
    )
    if result == 0:
        raise OvernightMLError("Windows keep-awake request failed")
    try:
        yield
    finally:
        ctypes.windll.kernel32.SetThreadExecutionState(continuous)  # type: ignore[attr-defined]


def prepare_ml_development_data(
    source: pd.DataFrame,
    holdout: CoverageHoldout,
    *,
    minimum_train: int = MINIMUM_TRAIN,
    test_size: int = TEST_SIZE,
    purge: int = PURGE,
    embargo: int = EMBARGO,
) -> MLDevelopmentData:
    """Build ML inputs after source-level holdout isolation and validate clocks."""
    if purge < MAX_LABEL_HORIZON_TRADING_DAYS:
        raise ValueError("ML purge must cover the maximum label horizon")
    if "date" not in source.columns:
        raise ValueError("ML source requires date")
    development_source = source.loc[
        source["date"].lt(holdout.holdout_start)
    ].reset_index(drop=True)
    if len(development_source) != holdout.development_observations:
        raise ValueError("ML development source count differs from holdout policy")
    if development_source.empty or not development_source["date"].lt(
        holdout.holdout_start
    ).all():
        raise ValueError("ML development source crossed the holdout boundary")

    features = build_kospi200_features(development_source)
    labels = build_forward_labels(development_source)
    feature_columns = list(FEATURE_COLUMNS)
    if any(column not in features.columns for column in feature_columns):
        raise ValueError("ML feature contract differs")
    if any(column.startswith(("forward_", "label_")) for column in features.columns):
        raise ValueError("label namespace leaked into ML features")
    if not features["pit_status"].eq("PIT_SAFE_EOD_T_PLUS_1").all():
        raise ValueError("ML features are not exact T+1 PIT-safe inputs")

    feature_view = features.loc[:, [
        "observation_date", "usable_from", "ticker", "date_semantics",
        "source_dataset", "source_contract_version", "feature_set_version",
        "pit_status", *feature_columns,
    ]]
    label_view = labels.loc[:, [
        "observation_date", "ticker", "date_semantics", "label_available_at",
        "label_version", TARGET_COLUMN,
    ]]
    aligned = feature_view.merge(
        label_view,
        on=["observation_date", "ticker", "date_semantics"],
        how="inner",
        validate="one_to_one",
    ).sort_values("observation_date", kind="stable").reset_index(drop=True)
    if aligned.empty or aligned["observation_date"].duplicated().any():
        raise ValueError("ML feature/label alignment is empty or duplicated")
    if not aligned["observation_date"].lt(holdout.holdout_start).all():
        raise ValueError("ML aligned frame crossed the holdout boundary")
    values = aligned.loc[:, feature_columns].to_numpy(dtype="float64")
    outcomes = aligned[TARGET_COLUMN].to_numpy(dtype="float64")
    if not np.isfinite(values).all() or not np.isfinite(outcomes).all():
        raise ValueError("ML inputs contain non-finite values")

    usable = pd.to_datetime(
        aligned["usable_from"], format="ISO8601", errors="raise", utc=True,
    )
    label_available = pd.to_datetime(
        aligned["label_available_at"], format="ISO8601", errors="raise", utc=True,
    )
    if not usable.lt(label_available).all():
        raise ValueError("ML decision clock can see its own outcome label")
    splits = expanding_walk_forward(
        observations=len(aligned), minimum_train=minimum_train,
        test_size=test_size, purge=purge, embargo=embargo,
    )
    for split in splits:
        train_available = label_available.iloc[split.train_start:split.train_end]
        test_usable = usable.iloc[split.test_start:split.test_end]
        if train_available.empty or test_usable.empty or not (
            train_available.max() < test_usable.min()
        ):
            raise ValueError("ML split exposes unavailable training labels")
    aligned["adverse_event"] = aligned[TARGET_COLUMN].le(
        TARGET_EVENT_THRESHOLD
    )
    if aligned["adverse_event"].nunique(dropna=False) != 2:
        raise ValueError("ML development target requires both event classes")
    return MLDevelopmentData(aligned, splits, holdout)


def _balanced_sample_weight(y: np.ndarray) -> np.ndarray:
    positives = int(y.sum())
    negatives = int(len(y) - positives)
    if positives == 0 or negatives == 0:
        raise ValueError("ML fold requires both event classes")
    return np.where(
        y == 1,
        len(y) / (2.0 * positives),
        len(y) / (2.0 * negatives),
    )


def _build_model(trial: optuna.Trial, *, fold: int) -> tuple[ClassifierMixin, bool]:
    model_family = trial.suggest_categorical(
        "model_family", ("logistic", "hist_gradient_boosting", "random_forest")
    )
    balanced = trial.suggest_categorical("balanced_weight", (False, True))
    seed = BASE_RANDOM_SEED + trial.number * 101 + fold
    if model_family == "logistic":
        model: ClassifierMixin = make_pipeline(
            StandardScaler(),
            LogisticRegression(
                C=trial.suggest_float("logistic_c", 1e-4, 1e3, log=True),
                class_weight="balanced" if balanced else None,
                max_iter=2000,
                solver="lbfgs",
                random_state=seed,
            ),
        )
        return model, False
    if model_family == "hist_gradient_boosting":
        depth_choice = trial.suggest_categorical("hist_max_depth", (0, 3, 5, 8))
        model = HistGradientBoostingClassifier(
            learning_rate=trial.suggest_float("hist_learning_rate", 0.01, 0.3, log=True),
            max_iter=trial.suggest_int("hist_max_iter", 100, 600, step=50),
            max_leaf_nodes=trial.suggest_int("hist_max_leaf_nodes", 7, 63, step=4),
            max_depth=None if depth_choice == 0 else depth_choice,
            min_samples_leaf=trial.suggest_int("hist_min_samples_leaf", 10, 100, step=5),
            l2_regularization=trial.suggest_float("hist_l2", 1e-8, 10.0, log=True),
            random_state=seed,
        )
        return model, balanced
    depth_choice = trial.suggest_categorical("forest_max_depth", (0, 3, 5, 8, 12))
    model = RandomForestClassifier(
        n_estimators=trial.suggest_int("forest_estimators", 100, 700, step=50),
        max_depth=None if depth_choice == 0 else depth_choice,
        min_samples_leaf=trial.suggest_int("forest_min_samples_leaf", 2, 80),
        max_features=trial.suggest_categorical("forest_max_features", ("sqrt", "log2", 0.75)),
        class_weight="balanced_subsample" if balanced else None,
        n_jobs=1,
        random_state=seed,
    )
    return model, False


def _prediction_digest(
    dates: list[str], probabilities: np.ndarray, outcomes: np.ndarray,
) -> str:
    digest = hashlib.sha256()
    for date_value, probability, outcome in zip(
        dates, probabilities, outcomes, strict=True,
    ):
        digest.update(
            f"{date_value},{probability:.17g},{int(outcome)}\n".encode("ascii")
        )
    return digest.hexdigest()


def evaluate_ml_trial(
    trial: optuna.Trial, data: MLDevelopmentData,
) -> float:
    """Return development-only out-of-fold average precision for one trial."""
    threshold = trial.suggest_float("decision_threshold", 0.15, 0.85, step=0.05)
    x = data.frame.loc[:, list(FEATURE_COLUMNS)].to_numpy(dtype="float64")
    y = data.frame["adverse_event"].to_numpy(dtype="int8")
    probabilities: list[np.ndarray] = []
    outcomes: list[np.ndarray] = []
    dates: list[str] = []
    for fold, split in enumerate(data.splits):
        x_train = x[split.train_start:split.train_end]
        y_train = y[split.train_start:split.train_end]
        x_test = x[split.test_start:split.test_end]
        y_test = y[split.test_start:split.test_end]
        if len(np.unique(y_train)) != 2 or len(np.unique(y_test)) < 1:
            raise optuna.TrialPruned("walk-forward fold lacks a train class")
        model, explicit_weight = _build_model(trial, fold=fold)
        if explicit_weight:
            model.fit(x_train, y_train, sample_weight=_balanced_sample_weight(y_train))
        else:
            model.fit(x_train, y_train)
        fold_probability = np.asarray(
            model.predict_proba(x_test)[:, 1], dtype="float64"
        )
        if not np.isfinite(fold_probability).all():
            raise OvernightMLError("ML model emitted non-finite probabilities")
        probabilities.append(fold_probability)
        outcomes.append(y_test)
        dates.extend(
            data.frame["observation_date"].iloc[
                split.test_start:split.test_end
            ].astype(str)
        )
        partial_probability = np.concatenate(probabilities)
        partial_outcome = np.concatenate(outcomes)
        partial_ap = float(average_precision_score(partial_outcome, partial_probability))
        trial.report(partial_ap, fold)
        if trial.should_prune():
            raise optuna.TrialPruned()

    probability = np.concatenate(probabilities)
    outcome = np.concatenate(outcomes)
    prediction = probability >= threshold
    tp = int(np.logical_and(prediction, outcome == 1).sum())
    fp = int(np.logical_and(prediction, outcome == 0).sum())
    fn = int(np.logical_and(~prediction, outcome == 1).sum())
    tn = int(np.logical_and(~prediction, outcome == 0).sum())
    ap = float(average_precision_score(outcome, probability))
    clipped = np.clip(probability, 1e-12, 1.0 - 1e-12)
    attributes: dict[str, int | float | str | bool] = {
        "schema": ML_SCHEMA,
        "folds": len(data.splits),
        "test_observations": len(outcome),
        "average_precision": ap,
        "brier_score": float(brier_score_loss(outcome, probability)),
        "log_loss": float(log_loss(outcome, clipped, labels=[0, 1])),
        "precision": tp / (tp + fp) if tp + fp else 0.0,
        "recall": tp / (tp + fn) if tp + fn else 0.0,
        "false_positive_rate": fp / (fp + tn) if fp + tn else 0.0,
        "event_prevalence": float(outcome.mean()),
        "prediction_sha256": _prediction_digest(dates, probability, outcome),
        "frozen_input_digest": EXPECTED_FROZEN_DIGEST,
        "signal_pit_status": "PIT_SAFE_EOD_T_PLUS_1",
        "holdout_results_reviewed": False,
    }
    for key, value in attributes.items():
        trial.set_user_attr(key, value)
    return ap


def _code_digest(project_root: Path) -> str:
    paths = (
        "scripts/run_overnight_ml.py",
        "src/market_backtest/overnight_ml.py",
        "src/market_backtest/phase1_replay.py",
        "src/market_backtest/holdout.py",
        "src/market_backtest/labels.py",
        "src/market_backtest/walk_forward.py",
        "src/market_features/frozen.py",
        "src/market_features/kospi200.py",
        "src/market_features/types.py",
    )
    return code_tree_digest(project_root, paths)


def _configuration(
    request: OvernightMLRequest,
    data: MLDevelopmentData,
    *,
    code_digest: str,
) -> dict[str, object]:
    return {
        "schema": ML_SCHEMA,
        "frozen_input_digest": EXPECTED_FROZEN_DIGEST,
        "feature_columns": list(FEATURE_COLUMNS),
        "feature_versions": [
            f"{item.feature_name}:v{item.feature_version}"
            for item in FEATURE_DEFINITIONS
        ],
        "label_version": "forward_outcomes:v1",
        "target": TARGET_COLUMN,
        "target_event": f"{TARGET_COLUMN}<={TARGET_EVENT_THRESHOLD}",
        "split_policy": "PURGED_EXPANDING_WALK_FORWARD",
        "minimum_train": MINIMUM_TRAIN,
        "test_size": TEST_SIZE,
        "purge": PURGE,
        "embargo": EMBARGO,
        "folds": len(data.splits),
        "development_rows": len(data.frame),
        "holdout_policy": asdict(data.holdout),
        "holdout_results_reviewed": False,
        "models": ["logistic", "hist_gradient_boosting", "random_forest"],
        "objective": "MAXIMIZE_DEVELOPMENT_OOF_AVERAGE_PRECISION",
        "sampler": "TPESampler(seed=20260826)",
        "pruner": "MedianPruner(startup=6,warmup_folds=2)",
        "duration_seconds": request.duration_seconds,
        "parallel_jobs": 1,
        "code_tree_sha256": code_digest,
        "libraries": {
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "scikit_learn": sklearn.__version__,
            "optuna": optuna.__version__,
        },
    }


def _trial_counts(study: optuna.Study) -> Counter[str]:
    return Counter(trial.state.name for trial in study.trials)


def _best_payload(study: optuna.Study) -> dict[str, object] | None:
    complete = [
        trial for trial in study.trials
        if trial.state is optuna.trial.TrialState.COMPLETE
    ]
    if not complete:
        return None
    best = study.best_trial
    return {
        "trial_number": best.number,
        "development_oof_average_precision": best.value,
        "parameters": best.params,
        "metrics": best.user_attrs,
        "status": "DEVELOPMENT_CANDIDATE_NOT_HOLDOUT_VALIDATED",
    }


def _state_payload(
    *,
    status: str,
    request: OvernightMLRequest,
    study: optuna.Study,
    consumed_seconds: float,
    started_at: str,
    error_type: str | None = None,
) -> dict[str, object]:
    counts = _trial_counts(study)
    payload: dict[str, object] = {
        "schema": ML_SCHEMA,
        "status": status,
        "pid": os.getpid(),
        "started_at": started_at,
        "updated_at": _utc_now(),
        "budget_seconds": request.duration_seconds,
        "consumed_seconds": round(consumed_seconds, 6),
        "remaining_seconds": round(
            max(0.0, request.duration_seconds - consumed_seconds), 6
        ),
        "trial_counts": dict(sorted(counts.items())),
        "best": _best_payload(study),
        "frozen_input_digest": EXPECTED_FROZEN_DIGEST,
        "holdout_results_reviewed": False,
        "keep_awake": request.keep_awake,
    }
    if error_type is not None:
        payload["error_type"] = error_type
    return payload


def _write_state_and_summary(
    output_root: Path,
    *,
    status: str,
    request: OvernightMLRequest,
    study: optuna.Study,
    consumed_seconds: float,
    started_at: str,
    error_type: str | None = None,
) -> None:
    state = _state_payload(
        status=status, request=request, study=study,
        consumed_seconds=consumed_seconds, started_at=started_at,
        error_type=error_type,
    )
    _atomic_json(output_root / "state.json", state)
    _atomic_json(output_root / "summary.json", {
        "schema": ML_SCHEMA,
        "status": status,
        "trial_counts": state["trial_counts"],
        "best": state["best"],
        "metrics_scope": "DEVELOPMENT_ONLY_HOLDOUT_UNTOUCHED",
        "holdout_results_reviewed": False,
        "updated_at": state["updated_at"],
    })


def _enqueue_baselines(study: optuna.Study) -> None:
    if study.trials:
        return
    study.enqueue_trial({
        "model_family": "logistic", "balanced_weight": True,
        "logistic_c": 1.0, "decision_threshold": 0.5,
    })
    study.enqueue_trial({
        "model_family": "hist_gradient_boosting", "balanced_weight": True,
        "hist_max_depth": 3, "hist_learning_rate": 0.05,
        "hist_max_iter": 250, "hist_max_leaf_nodes": 31,
        "hist_min_samples_leaf": 20, "hist_l2": 0.01,
        "decision_threshold": 0.5,
    })
    study.enqueue_trial({
        "model_family": "random_forest", "balanced_weight": True,
        "forest_max_depth": 8, "forest_estimators": 300,
        "forest_min_samples_leaf": 20, "forest_max_features": "sqrt",
        "decision_threshold": 0.5,
    })


def run_overnight_ml(request: OvernightMLRequest) -> OvernightMLReceipt:
    project_root = _absolute_plain_path(request.project_root, label="project root")
    output_root = _absolute_plain_path(
        request.output_root
        if request.output_root is not None
        else project_root / DEFAULT_OUTPUT_RELATIVE,
        label="ML output root",
    )
    _assert_output_scope(project_root, output_root)
    if output_root.is_relative_to(
        project_root / "artifacts/backtest/frozen_inputs"
    ):
        raise OvernightMLError("ML output cannot enter the frozen-input root")
    output_root.mkdir(parents=True, exist_ok=True)
    started_at = _utc_now()
    with _exclusive_study_lock(output_root / ".overnight-ml.lock"):
        try:
            manifest, source, holdout = _load_verified_source(project_root)
        except Phase1ReplayError as error:
            raise OvernightMLError("frozen ML input verification failed") from error
        if manifest.root_manifest_sha256 != EXPECTED_FROZEN_DIGEST:
            raise OvernightMLError("frozen ML input digest differs")
        data = prepare_ml_development_data(source, holdout)
        configuration = _configuration(
            request, data, code_digest=_code_digest(project_root),
        )
        config_path = output_root / "config.json"
        if config_path.exists():
            if _read_exact_json(config_path, label="ML configuration") != configuration:
                raise OvernightMLError(
                    "existing ML output belongs to a different configuration"
                )
        else:
            _atomic_json(config_path, configuration)

        state_path = output_root / "state.json"
        previous_consumed = 0.0
        if state_path.exists():
            previous = _read_exact_json(state_path, label="ML state")
            prior_started_at = previous.get("started_at")
            if type(prior_started_at) is not str or not prior_started_at:
                raise OvernightMLError("ML state started_at differs")
            started_at = prior_started_at
            value = previous.get("consumed_seconds", 0.0)
            if type(value) not in (int, float) or not np.isfinite(value) or value < 0:
                raise OvernightMLError("ML state consumed_seconds differs")
            previous_consumed = float(value)

        database_path = output_root / "study.sqlite3"
        if database_path.exists() and (
            database_path.is_symlink() or not database_path.is_file()
        ):
            raise OvernightMLError("ML study database is not an exact file")
        storage_url = f"sqlite:///{database_path.as_posix()}"
        storage = optuna.storages.RDBStorage(
            url=storage_url,
            engine_kwargs={"connect_args": {"timeout": 30.0}},
            heartbeat_interval=60,
            grace_period=180,
        )
        optuna.logging.set_verbosity(optuna.logging.WARNING)
        study_name = f"kospi200-ml-{EXPECTED_FROZEN_DIGEST[:12]}"
        study = optuna.create_study(
            study_name=study_name,
            storage=storage,
            direction="maximize",
            sampler=optuna.samplers.TPESampler(seed=BASE_RANDOM_SEED),
            pruner=optuna.pruners.MedianPruner(
                n_startup_trials=6, n_warmup_steps=2, interval_steps=1,
            ),
            load_if_exists=True,
        )
        _enqueue_baselines(study)
        remaining = max(0.0, request.duration_seconds - previous_consumed)
        if remaining <= 0:
            _write_state_and_summary(
                output_root, status="TIME_BUDGET_COMPLETE", request=request,
                study=study, consumed_seconds=request.duration_seconds,
                started_at=started_at,
            )
        else:
            session_start = time.monotonic()

            def consumed() -> float:
                return min(
                    float(request.duration_seconds),
                    previous_consumed + (time.monotonic() - session_start),
                )

            def checkpoint(
                current_study: optuna.Study, _trial: optuna.trial.FrozenTrial,
            ) -> None:
                _write_state_and_summary(
                    output_root, status="RUNNING", request=request,
                    study=current_study, consumed_seconds=consumed(),
                    started_at=started_at,
                )

            _write_state_and_summary(
                output_root, status="RUNNING", request=request, study=study,
                consumed_seconds=previous_consumed, started_at=started_at,
            )
            try:
                with _keep_system_awake(request.keep_awake):
                    study.optimize(
                        lambda trial: evaluate_ml_trial(trial, data),
                        timeout=remaining,
                        n_trials=request.max_trials,
                        n_jobs=1,
                        callbacks=(checkpoint,),
                        gc_after_trial=True,
                        show_progress_bar=False,
                    )
            except Exception as error:
                elapsed = consumed()
                _write_state_and_summary(
                    output_root, status="FAILED", request=request, study=study,
                    consumed_seconds=elapsed, started_at=started_at,
                    error_type=type(error).__name__,
                )
                raise
            elapsed = consumed()
            final_status = (
                "TIME_BUDGET_COMPLETE"
                if elapsed >= request.duration_seconds - 0.5
                else "PAUSED_MAX_TRIALS"
            )
            _write_state_and_summary(
                output_root, status=final_status, request=request, study=study,
                consumed_seconds=elapsed, started_at=started_at,
            )

        state = _read_exact_json(state_path, label="ML state")
        counts = state.get("trial_counts", {})
        if type(counts) is not dict:
            raise OvernightMLError("ML state trial counts differ")
        best = state.get("best")
        best_number = None
        if best is not None:
            if type(best) is not dict or type(best.get("trial_number")) is not int:
                raise OvernightMLError("ML state best trial differs")
            best_number = int(best["trial_number"])
        return OvernightMLReceipt(
            output_root=output_root,
            status=str(state["status"]),
            completed_trials=int(counts.get("COMPLETE", 0)),
            pruned_trials=int(counts.get("PRUNED", 0)),
            failed_trials=int(counts.get("FAIL", 0)),
            consumed_seconds=float(state["consumed_seconds"]),
            remaining_seconds=float(state["remaining_seconds"]),
            best_trial_number=best_number,
        )


def read_overnight_ml_status(output_root: Path) -> dict[str, Any]:
    return _read_exact_json(Path(output_root) / "state.json", label="ML state")


__all__ = [
    "DEFAULT_DURATION_SECONDS", "DEFAULT_OUTPUT_RELATIVE", "FEATURE_COLUMNS",
    "MAX_DURATION_SECONDS", "MLDevelopmentData", "ML_SCHEMA",
    "OvernightMLError", "OvernightMLReceipt", "OvernightMLRequest",
    "evaluate_ml_trial", "prepare_ml_development_data",
    "read_overnight_ml_status", "run_overnight_ml",
]
