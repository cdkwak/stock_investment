"""Typed, offline, recoverable Phase-1 replay runner.

The computation is bound to the accepted frozen KOSPI200 input.  Publication
uses a same-parent directory transaction so readers observe either the prior
complete bundle or the new complete bundle, never a per-file mixture.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
from typing import Callable, Mapping

import pandas as pd

from market_features.kospi200 import FEATURE_DEFINITIONS, build_kospi200_features

from .ablation import FeatureFamilyStatus, build_ablation_plan
from .crisis import replay_crisis_windows
from .experiments import (
    ExperimentRecord,
    artifact_bytes_digest,
    canonical_json_digest,
    code_tree_digest,
    serialize_experiment_registry,
)
from .holdout import CoverageHoldout, define_untouched_holdout
from .labels import MAX_LABEL_HORIZON_TRADING_DAYS, build_forward_labels
from .portfolio import (
    KOSPI200_FROZEN_HOLDOUT_V1,
    simulate_kospi200_risk_off_portfolio,
)
from .signals import (
    PREDEFINED_SMALL_GRID,
    SignalThresholds,
    build_descriptive_signals,
    evaluate_predefined_walk_forward,
    evaluate_signals,
)


EXPECTED_FROZEN_DIGEST = (
    "a9229374d82aca29bd792230752ff050f266968c496477223400d1c87b2cc713"
)
DEFAULT_OUTPUT_RELATIVE = Path("artifacts/backtest/phase1_signal_replay")
BUNDLE_SCHEMA = "market-backtest-phase1-replay/v1"

PHASE1_DEPENDENCY_MANIFEST_SCHEMA = "phase1-code-dependencies/v1"
PHASE1_DEPENDENCY_PATHS = (
    Path("src/market_backtest/ablation.py"),
    Path("src/market_backtest/crisis.py"),
    Path("src/market_backtest/experiments.py"),
    Path("src/market_backtest/holdout.py"),
    Path("src/market_backtest/labels.py"),
    Path("src/market_backtest/phase1_replay.py"),
    Path("src/market_backtest/portfolio.py"),
    Path("src/market_backtest/signals.py"),
    Path("src/market_backtest/walk_forward.py"),
    Path("src/market_features/frozen.py"),
    Path("src/market_features/kospi200.py"),
    Path("src/market_features/types.py"),
)

_RESULT_STATUS = "DESCRIPTIVE_SIGNAL_REPLAY_NOT_PORTFOLIO_BACKTEST"
_BASE_ARTIFACTS = (
    "signals.csv",
    "result.json",
    "experiments.json",
    "portfolio_ledger.json",
)
_BUNDLE_FILE = "bundle.json"
_OWNED_FILES = frozenset((*_BASE_ARTIFACTS, _BUNDLE_FILE))
_LEGACY_FILES = frozenset(("signals.csv", "result.json", "experiments.json"))
_WINDOWS_REPARSE_POINT = 0x0400
_PHASE_ORDER = (
    "PREPARING",
    "STAGED",
    "BACKUP_PENDING",
    "PUBLISH_PENDING",
    "VERIFY_PENDING",
    "VERIFIED",
    "BACKUP_RETIRING",
)
_PHASES = frozenset(_PHASE_ORDER)
_RESERVED_OUTPUT_SUFFIXES = (
    ".phase1-replay.lock",
    ".phase1-replay.stage",
    ".phase1-replay.backup",
    ".phase1-replay.journal.json",
    ".phase1-replay.journal.tmp",
)


class Phase1ReplayError(RuntimeError):
    """Raised when replay computation or bundle publication fails closed."""


def phase1_code_digest(project_root: Path) -> str:
    """Hash only the explicit, versioned Phase-1 semantic dependency set."""
    paths = PHASE1_DEPENDENCY_PATHS
    names = tuple(path.as_posix() for path in paths)
    if (
        PHASE1_DEPENDENCY_MANIFEST_SCHEMA != "phase1-code-dependencies/v1"
        or not paths
        or names != tuple(sorted(names))
        or len(names) != len(set(names))
        or any(
            path.is_absolute()
            or ".." in path.parts
            or path.suffix != ".py"
            for path in paths
        )
    ):
        raise Phase1ReplayError("Phase-1 dependency manifest is invalid")
    try:
        return code_tree_digest(Path(project_root), paths)
    except (OSError, ValueError) as error:
        raise Phase1ReplayError(
            "Phase-1 dependency manifest cannot be verified"
        ) from error


@dataclass(frozen=True, slots=True)
class Phase1ReplayRequest:
    project_root: Path
    output_root: Path | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_root", Path(self.project_root))
        if self.output_root is not None:
            object.__setattr__(self, "output_root", Path(self.output_root))


@dataclass(frozen=True, slots=True)
class Phase1ArtifactReceipt:
    name: str
    bytes: int
    sha256: str

    def __post_init__(self) -> None:
        if (
            type(self.name) is not str
            or self.name not in _OWNED_FILES
            or type(self.bytes) is not int
            or self.bytes < 1
            or not _is_sha256(self.sha256)
        ):
            raise ValueError("Phase-1 artifact receipt is invalid")


@dataclass(frozen=True, slots=True)
class Phase1ReplayReceipt:
    schema: str
    status: str
    output_root: Path
    frozen_input_digest: str
    bundle_digest: str
    artifacts: tuple[Phase1ArtifactReceipt, ...]

    def __post_init__(self) -> None:
        if (
            self.schema != BUNDLE_SCHEMA
            or self.status != "READY"
            or not isinstance(self.output_root, Path)
            or not self.output_root.is_absolute()
            or self.frozen_input_digest != EXPECTED_FROZEN_DIGEST
            or not _is_sha256(self.bundle_digest)
            or type(self.artifacts) is not tuple
            or any(
                type(item) is not Phase1ArtifactReceipt
                for item in self.artifacts
            )
            or tuple(item.name for item in self.artifacts)
            != tuple(sorted(_OWNED_FILES))
            or self.bundle_digest != _records_digest(self.artifacts)
        ):
            raise ValueError("Phase-1 replay receipt is invalid")


@dataclass(frozen=True, slots=True)
class _ReplayBundle:
    frozen_input_digest: str
    bodies: tuple[tuple[str, bytes], ...]

    def body_map(self) -> dict[str, bytes]:
        return dict(self.bodies)


PromotionHook = Callable[[str], None]


def _entry_exists(path: Path) -> bool:
    return os.path.lexists(path)


def _absolute_plain_path(path: Path, *, label: str) -> Path:
    """Return one canonical absolute path after rejecting existing redirects."""
    absolute = Path(os.path.abspath(path))
    current = Path(absolute.anchor)
    for component in absolute.parts[1:]:
        current /= component
        if not os.path.lexists(current):
            continue
        try:
            status = current.lstat()
        except OSError as error:
            raise Phase1ReplayError(f"cannot inspect {label} topology") from error
        if current.is_symlink() or bool(
            getattr(status, "st_file_attributes", 0) & _WINDOWS_REPARSE_POINT
        ):
            raise Phase1ReplayError(f"{label} contains a redirected path component")
    # Windows 8.3 names are aliases, not reparse points.  Canonicalizing after
    # the redirect walk prevents a short-name spelling from bypassing protected
    # root comparisons while keeping all later transaction paths consistent.
    return Path(os.path.realpath(absolute))


def _assert_output_scope(project_root: Path, output_root: Path) -> None:
    project_backtest_root = project_root / "artifacts/backtest"
    project_test_root = project_root / "artifacts/test_tmp"
    project_agent_temp_root = project_root / ".tmp/agents"
    protected_roots = (
        project_root / "data",
        project_root / "artifacts/backtest/frozen_inputs",
    )
    if any(
        output_root == protected or output_root.is_relative_to(protected)
        for protected in protected_roots
    ):
        raise Phase1ReplayError("output root is inside a protected data root")
    allowed_project_roots = (project_backtest_root, project_test_root)
    below_standard_root = any(
        output_root != allowed and output_root.is_relative_to(allowed)
        for allowed in allowed_project_roots
    )
    below_agent_temp = False
    if output_root.is_relative_to(project_agent_temp_root):
        agent_relative = output_root.relative_to(project_agent_temp_root)
        below_agent_temp = len(agent_relative.parts) >= 2
    if (
        output_root.is_relative_to(project_root)
        and not below_standard_root
        and not below_agent_temp
    ):
        raise Phase1ReplayError(
            "project-local output root must be below artifacts/backtest "
            "or artifacts/test_tmp, or inside an owned .tmp/agents directory"
        )
    if any(
        (folded := component.rstrip(" .").casefold())
        != component.casefold()
        or (
            folded.startswith(".")
            and any(folded.endswith(suffix) for suffix in _RESERVED_OUTPUT_SUFFIXES)
        )
        for component in output_root.parts[1:]
    ):
        raise Phase1ReplayError("output root uses a reserved transaction name")


def _lock_path(output_root: Path) -> Path:
    return output_root.parent / f".{output_root.name}.phase1-replay.lock"


def _assert_not_nested_in_replay_namespace(
    project_root: Path, output_root: Path,
) -> None:
    """Protect every prior/active replay root as an indivisible namespace."""
    filesystem_root = Path(output_root.anchor)
    try:
        trusted_boundary = Path(os.path.commonpath((project_root, output_root)))
    except ValueError:
        trusted_boundary = filesystem_root
    for ancestor in output_root.parents:
        # The shared project/output container cannot itself be an exact replay
        # root because it also owns the project branch.  Stopping here avoids
        # probing unrelated inaccessible user-profile ancestors.
        if ancestor in {trusted_boundary, filesystem_root}:
            break
        if _entry_exists(_lock_path(ancestor)):
            raise Phase1ReplayError(
                "output root is nested inside a Phase-1 replay namespace"
            )
        if not ancestor.is_dir():
            continue
        try:
            names = {entry.name for entry in ancestor.iterdir()}
        except OSError as error:
            raise Phase1ReplayError(
                "cannot inspect an output ancestor namespace"
            ) from error
        if _OWNED_FILES.issubset(names) or _LEGACY_FILES.issubset(names):
            raise Phase1ReplayError(
                "output root is nested inside a Phase-1 replay namespace"
            )


def _lock_stream(stream: object) -> None:
    stream.seek(0)  # type: ignore[attr-defined]
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
        return
    import fcntl

    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]


def _unlock_stream(stream: object) -> None:
    stream.seek(0)  # type: ignore[attr-defined]
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
        return
    import fcntl

    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]


def _assert_lock_handle(path: Path, stream: object) -> None:
    try:
        path_status = path.lstat()
        handle_status = os.fstat(stream.fileno())  # type: ignore[attr-defined]
    except OSError as error:
        raise Phase1ReplayError("Phase-1 output lock topology changed") from error
    if (
        path.is_symlink()
        or bool(
            getattr(path_status, "st_file_attributes", 0)
            & _WINDOWS_REPARSE_POINT
        )
        or (path_status.st_dev, path_status.st_ino)
        != (handle_status.st_dev, handle_status.st_ino)
        or path_status.st_nlink != 1
        or handle_status.st_nlink != 1
    ):
        raise Phase1ReplayError("Phase-1 output lock topology changed")


@contextmanager
def _exclusive_output_lock(output_root: Path) -> Iterator[Callable[[], None]]:
    """Hold a crash-released, output-specific lock across recovery and compute."""
    path = _lock_path(output_root)
    parent = output_root.parent
    try:
        parent_status = parent.lstat()
    except OSError as error:
        raise Phase1ReplayError("Phase-1 output parent is unavailable") from error
    if (
        not parent.is_dir()
        or parent.is_symlink()
        or bool(
            getattr(parent_status, "st_file_attributes", 0)
            & _WINDOWS_REPARSE_POINT
        )
    ):
        raise Phase1ReplayError("Phase-1 output parent is unsafe")
    parent_identity = (parent_status.st_dev, parent_status.st_ino)
    flags = os.O_RDWR | os.O_CREAT
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags | os.O_EXCL, 0o600)
    except FileExistsError:
        if not _plain_file(path):
            raise Phase1ReplayError("Phase-1 output lock path is unsafe")
        try:
            descriptor = os.open(path, flags & ~os.O_CREAT, 0o600)
        except OSError as error:
            raise Phase1ReplayError("Phase-1 output lock is unavailable") from error
    except OSError as error:
        raise Phase1ReplayError("Phase-1 output lock is unavailable") from error
    stream = os.fdopen(descriptor, "r+b", buffering=0)
    locked = False

    def assert_scope() -> None:
        try:
            current_parent = parent.lstat()
        except OSError as error:
            raise Phase1ReplayError(
                "Phase-1 output parent topology changed"
            ) from error
        if (
            not parent.is_dir()
            or parent.is_symlink()
            or bool(
                getattr(current_parent, "st_file_attributes", 0)
                & _WINDOWS_REPARSE_POINT
            )
            or (current_parent.st_dev, current_parent.st_ino) != parent_identity
        ):
            raise Phase1ReplayError("Phase-1 output parent topology changed")
        _absolute_plain_path(output_root, label="output root")
        _assert_lock_handle(path, stream)

    try:
        assert_scope()
        try:
            _lock_stream(stream)
        except OSError as error:
            raise Phase1ReplayError(
                "another Phase-1 replay is already active for this output"
            ) from error
        locked = True
        assert_scope()
        stream.seek(0, os.SEEK_END)
        if stream.tell() != 0:
            raise Phase1ReplayError("Phase-1 output lock metadata differs")
        yield assert_scope
    finally:
        if locked:
            _unlock_stream(stream)
        stream.close()


def _is_sha256(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _json_bytes(payload: object, *, pretty: bool = False) -> bytes:
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


def _artifact_receipt(name: str, body: bytes) -> Phase1ArtifactReceipt:
    return Phase1ArtifactReceipt(
        name=name,
        bytes=len(body),
        sha256=artifact_bytes_digest(body),
    )


def _records_digest(records: tuple[Phase1ArtifactReceipt, ...]) -> str:
    payload = [asdict(record) for record in sorted(records, key=lambda item: item.name)]
    return canonical_json_digest(payload)


def _bind_bundle(
    artifacts: Mapping[str, bytes], *, frozen_input_digest: str,
) -> _ReplayBundle:
    if frozen_input_digest != EXPECTED_FROZEN_DIGEST:
        raise Phase1ReplayError("frozen KOSPI200 manifest digest differs")
    if set(artifacts) != set(_BASE_ARTIFACTS):
        raise Phase1ReplayError("Phase-1 bundle artifact set is incomplete")
    receipts = tuple(
        _artifact_receipt(name, artifacts[name]) for name in sorted(_BASE_ARTIFACTS)
    )
    manifest = {
        "schema": BUNDLE_SCHEMA,
        "frozen_input_digest": frozen_input_digest,
        "artifact_set_sha256": _records_digest(receipts),
        "artifacts": [asdict(receipt) for receipt in receipts],
    }
    bodies = dict(artifacts)
    bodies[_BUNDLE_FILE] = _json_bytes(manifest)
    return _ReplayBundle(
        frozen_input_digest=frozen_input_digest,
        bodies=tuple((name, bodies[name]) for name in sorted(bodies)),
    )


def _load_verified_source(
    project_root: Path,
) -> tuple[object, pd.DataFrame, CoverageHoldout]:
    from market_features.frozen import verify_frozen_kospi200
    from stock_data.contracts.kospi200_index_daily import (
        KR_KOSPI200_INDEX_DAILY,
    )
    from stock_data.storage.contract_parquet import read_dataset
    from stock_data.validation.kospi200_index_daily import (
        validate_kospi200_index_daily,
    )

    dataset_root = (
        project_root
        / "artifacts/backtest/frozen_inputs/kr_kospi200_index_daily"
        / EXPECTED_FROZEN_DIGEST
    )
    manifest_path = project_root / "artifacts/backtest/kospi200_frozen_manifest.json"
    if (
        not manifest_path.is_file()
        or manifest_path.is_symlink()
        or manifest_path.resolve() != manifest_path.absolute()
    ):
        raise Phase1ReplayError("frozen KOSPI200 manifest path is not an exact file")
    try:
        expected_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise Phase1ReplayError("frozen KOSPI200 manifest is unreadable") from error
    if (
        type(expected_manifest) is not dict
        or expected_manifest.get("root_manifest_sha256")
        != EXPECTED_FROZEN_DIGEST
    ):
        raise Phase1ReplayError("frozen KOSPI200 manifest digest differs")
    try:
        manifest = verify_frozen_kospi200(dataset_root, expected_manifest)
    except Exception as error:
        raise Phase1ReplayError("frozen KOSPI200 verification failed") from error
    if manifest.root_manifest_sha256 != EXPECTED_FROZEN_DIGEST:
        raise Phase1ReplayError("frozen KOSPI200 manifest digest differs")
    try:
        source = read_dataset(
            dataset_root,
            KR_KOSPI200_INDEX_DAILY,
            validate_kospi200_index_daily,
        )
        readback_manifest = verify_frozen_kospi200(
            dataset_root,
            expected_manifest,
        )
    except Exception as error:
        raise Phase1ReplayError(
            "frozen KOSPI200 input changed or failed during read"
        ) from error
    if readback_manifest != manifest:
        raise Phase1ReplayError("frozen KOSPI200 input changed during read")
    holdout = define_untouched_holdout(source["date"])
    if holdout != KOSPI200_FROZEN_HOLDOUT_V1:
        raise Phase1ReplayError("frozen KOSPI200 holdout identity differs")
    return manifest, source, holdout


def _build_replay_bundle(
    project_root: Path,
    output_root: Path | None = None,
) -> _ReplayBundle:
    manifest, source, holdout = _load_verified_source(project_root)
    effective_output = (
        output_root
        if output_root is not None
        else project_root / DEFAULT_OUTPUT_RELATIVE
    )
    result_path = effective_output / "result.json"
    try:
        result_artifact = result_path.relative_to(project_root).as_posix()
    except ValueError:
        result_artifact = result_path.as_posix()

    # Only source dates define the frozen boundary.  Feature and outcome-label
    # construction receive the development slice and cannot inspect holdout rows.
    development_source = source.loc[
        source["date"].lt(holdout.holdout_start)
    ].reset_index(drop=True)
    if len(development_source) != holdout.development_observations:
        raise Phase1ReplayError("development slice count differs from frozen holdout")

    features = build_kospi200_features(development_source)
    labels = build_forward_labels(development_source)
    thresholds = SignalThresholds()
    signals = build_descriptive_signals(features, thresholds)
    metrics = evaluate_signals(signals, labels)
    grid = evaluate_predefined_walk_forward(features, labels)
    crises = replay_crisis_windows(
        signals,
        labels,
        holdout_start=holdout.holdout_start,
    )
    ablation = build_ablation_plan({
        "PRICE": FeatureFamilyStatus.AVAILABLE,
        "VOLATILITY": FeatureFamilyStatus.AVAILABLE,
        "FX": FeatureFamilyStatus.BLOCKED,
        "BREADTH": FeatureFamilyStatus.BLOCKED,
        "FLOW": FeatureFamilyStatus.BLOCKED,
        "DERIVATIVES": FeatureFamilyStatus.BLOCKED,
    })
    portfolio = simulate_kospi200_risk_off_portfolio(
        development_source,
        signals,
        holdout,
    )

    signals_body = signals.to_csv(
        index=False, lineterminator="\n",
    ).encode("utf-8")
    portfolio_body = _json_bytes({
        "schema": "market-backtest-close-proxy-ledger/v1",
        "simulation": asdict(portfolio),
    })
    portfolio_digest = artifact_bytes_digest(portfolio_body)
    result_payload = {
        "status": _RESULT_STATUS,
        "frozen_manifest": asdict(manifest),
        "thresholds": asdict(thresholds),
        "untouched_holdout_policy": asdict(holdout),
        "development_metrics": metrics,
        "metrics": metrics,
        "metrics_scope": "DEVELOPMENT_ONLY_HOLDOUT_UNTOUCHED",
        "predefined_small_grid": [
            {**row, "thresholds": asdict(row["thresholds"])} for row in grid
        ],
        "crisis_replay_development_only": crises,
        "crisis_replay": crises,
        "feature_family_ablation_plan": [asdict(step) for step in ablation],
        "portfolio_foundation": {
            "status": portfolio.status,
            "instrument_claim": portfolio.instrument_claim,
            "metrics": asdict(portfolio.metrics),
            "ledger_artifact": "portfolio_ledger.json",
            "ledger_artifact_digest": portfolio_digest,
        },
        "experiment_id": "phase1_price_volatility_descriptive_v1",
    }
    result_body = _json_bytes(result_payload, pretty=True)

    threshold_payload = {
        "active": asdict(thresholds),
        "grid": [
            (name, asdict(values)) for name, values in PREDEFINED_SMALL_GRID
        ],
    }
    experiment = ExperimentRecord(
        experiment_id="phase1_price_volatility_descriptive_v1",
        frozen_input_digest=manifest.root_manifest_sha256,
        feature_set=("PRICE", "VOLATILITY"),
        feature_versions=tuple(
            f"{definition.feature_name}:v{definition.feature_version}"
            for definition in FEATURE_DEFINITIONS
        ),
        label_version="forward_outcomes:v1",
        split_policy="PURGED_EXPANDING_WALK_FORWARD",
        purge=60,
        embargo=5,
        threshold_rule="PREDEFINED_SMALL_GRID_NO_WINNER_SELECTION",
        result_artifact=result_artifact,
        code_version="BACKTEST_PHASE1_FOUNDATION_V1",
        code_tree_digest=phase1_code_digest(project_root),
        threshold_values_digest=canonical_json_digest(threshold_payload),
        signals_artifact_digest=artifact_bytes_digest(signals_body),
        result_artifact_digest=artifact_bytes_digest(result_body),
        label_horizon_trading_days=MAX_LABEL_HORIZON_TRADING_DAYS,
        signal_pit_status="PIT_SAFE_EOD_T_PLUS_1",
    )
    experiments_body = serialize_experiment_registry((experiment,)).encode("utf-8")
    return _bind_bundle(
        {
            "signals.csv": signals_body,
            "result.json": result_body,
            "experiments.json": experiments_body,
            "portfolio_ledger.json": portfolio_body,
        },
        frozen_input_digest=manifest.root_manifest_sha256,
    )


def _plain_file(path: Path) -> bool:
    return (
        path.is_file()
        and not path.is_symlink()
        and path.resolve() == path.absolute()
    )


def _inventory(root: Path) -> tuple[Phase1ArtifactReceipt, ...]:
    if (
        not root.is_dir()
        or root.is_symlink()
        or root.resolve() != root.absolute()
    ):
        raise Phase1ReplayError("Phase-1 bundle root is not an exact directory")
    entries = tuple(root.iterdir())
    if any(not _plain_file(path) for path in entries):
        raise Phase1ReplayError("Phase-1 bundle contains a non-regular entry")
    if any(path.name not in _OWNED_FILES for path in entries):
        raise Phase1ReplayError("Phase-1 bundle contains an unowned file")
    try:
        return tuple(
            _artifact_receipt(path.name, path.read_bytes())
            for path in sorted(entries, key=lambda item: item.name)
        )
    except (OSError, ValueError) as error:
        raise Phase1ReplayError("Phase-1 bundle inventory is unreadable") from error


def _directory_digest(records: tuple[Phase1ArtifactReceipt, ...]) -> str:
    return _records_digest(records)


def _verify_bundle(root: Path) -> tuple[tuple[Phase1ArtifactReceipt, ...], str]:
    records = _inventory(root)
    if {record.name for record in records} != _OWNED_FILES:
        raise Phase1ReplayError("Phase-1 bundle file inventory differs")
    manifest_path = root / _BUNDLE_FILE
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise Phase1ReplayError("Phase-1 bundle manifest is unreadable") from error
    expected_keys = {
        "schema", "frozen_input_digest", "artifact_set_sha256", "artifacts",
    }
    if (
        type(manifest) is not dict
        or set(manifest) != expected_keys
        or manifest.get("schema") != BUNDLE_SCHEMA
        or manifest.get("frozen_input_digest") != EXPECTED_FROZEN_DIGEST
        or not _is_sha256(manifest.get("artifact_set_sha256"))
        or type(manifest.get("artifacts")) is not list
    ):
        raise Phase1ReplayError("Phase-1 bundle manifest schema differs")
    declared: list[Phase1ArtifactReceipt] = []
    try:
        for item in manifest["artifacts"]:
            if type(item) is not dict or set(item) != {"name", "bytes", "sha256"}:
                raise ValueError
            declared.append(Phase1ArtifactReceipt(**item))
    except (TypeError, ValueError) as error:
        raise Phase1ReplayError("Phase-1 bundle manifest entries differ") from error
    declared_tuple = tuple(sorted(declared, key=lambda item: item.name))
    observed_base = tuple(
        record for record in records if record.name != _BUNDLE_FILE
    )
    if (
        tuple(record.name for record in declared_tuple) != tuple(sorted(_BASE_ARTIFACTS))
        or declared_tuple != observed_base
        or manifest["artifact_set_sha256"] != _records_digest(declared_tuple)
    ):
        raise Phase1ReplayError("Phase-1 bundle digest readback differs")
    canonical_manifest = _json_bytes(manifest)
    if manifest_path.read_bytes() != canonical_manifest:
        raise Phase1ReplayError("Phase-1 bundle manifest is not canonical")
    return records, _directory_digest(records)


def _verify_legacy_bundle(root: Path) -> str:
    records = _inventory(root)
    if frozenset(record.name for record in records) != _LEGACY_FILES:
        raise Phase1ReplayError("legacy Phase-1 output file inventory differs")
    bodies = {
        name: (root / name).read_bytes() for name in sorted(_LEGACY_FILES)
    }
    try:
        result = json.loads(bodies["result.json"].decode("utf-8"))
        registry = json.loads(bodies["experiments.json"].decode("utf-8"))
        header = bodies["signals.csv"].decode("utf-8").splitlines()[0].split(",")
        holdout = result["untouched_holdout_policy"]
        frozen_digest = result["frozen_manifest"]["root_manifest_sha256"]
        experiments = registry["experiments"]
        experiment = experiments[0]
    except (
        UnicodeError,
        json.JSONDecodeError,
        KeyError,
        IndexError,
        TypeError,
    ) as error:
        raise Phase1ReplayError("legacy Phase-1 output is invalid") from error
    required_signal_columns = {
        "observation_date",
        "usable_from",
        "risk_score",
        "risk_off_signal",
        "signal_version",
    }
    if (
        type(result) is not dict
        or result.get("status") != _RESULT_STATUS
        or frozen_digest != EXPECTED_FROZEN_DIGEST
        or type(holdout) is not dict
        or holdout != asdict(KOSPI200_FROZEN_HOLDOUT_V1)
        or type(holdout.get("results_reviewed")) is not bool
        or holdout["results_reviewed"] is not False
        or type(registry) is not dict
        or registry.get("version") != 1
        or type(experiments) is not list
        or len(experiments) != 1
        or type(experiment) is not dict
        or experiment.get("frozen_input_digest") != EXPECTED_FROZEN_DIGEST
        or type(experiment.get("holdout_results_reviewed")) is not bool
        or experiment["holdout_results_reviewed"] is not False
        or experiment.get("signals_artifact_digest")
        != artifact_bytes_digest(bodies["signals.csv"])
        or experiment.get("result_artifact_digest")
        != artifact_bytes_digest(bodies["result.json"])
        or not required_signal_columns.issubset(header)
        or len(header) != len(set(header))
        or any(
            column.startswith(("forward_", "label_"))
            or column in {"mae_20d", "mfe_20d"}
            for column in header
        )
    ):
        raise Phase1ReplayError("legacy Phase-1 output is invalid")
    return _directory_digest(records)


def _verify_prior(root: Path) -> str:
    records = _inventory(root)
    names = frozenset(record.name for record in records)
    if names == _OWNED_FILES:
        _verified, digest = _verify_bundle(root)
        return digest
    if names != _LEGACY_FILES:
        raise Phase1ReplayError("existing Phase-1 output has unowned or missing files")
    return _verify_legacy_bundle(root)


def _transaction_paths(output_root: Path) -> tuple[Path, Path, Path, Path]:
    parent = output_root.parent
    prefix = f".{output_root.name}.phase1-replay"
    return (
        parent / f"{prefix}.stage",
        parent / f"{prefix}.backup",
        parent / f"{prefix}.journal.json",
        parent / f"{prefix}.journal.tmp",
    )


def _marker_bytes(payload: Mapping[str, object]) -> bytes:
    return _json_bytes(dict(payload))


def _write_marker(marker: Path, temporary: Path, payload: Mapping[str, object]) -> None:
    if _entry_exists(temporary):
        raise Phase1ReplayError("Phase-1 transaction marker temporary already exists")
    body = _marker_bytes(payload)
    temporary.parent.mkdir(parents=True, exist_ok=True)
    with temporary.open("xb") as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(marker)


_MARKER_KEYS = {
    "schema",
    "phase",
    "output_name",
    "stage_name",
    "backup_name",
    "had_live",
    "original_digest",
    "expected_digest",
}


def _validate_marker_payload(
    output_root: Path,
    payload: object,
    *,
    label: str,
) -> dict[str, object]:
    stage, backup, _marker, _temporary = _transaction_paths(output_root)
    if (
        type(payload) is not dict
        or set(payload) != _MARKER_KEYS
        or payload.get("schema") != BUNDLE_SCHEMA
        or payload.get("phase") not in _PHASES
        or payload.get("output_name") != output_root.name
        or payload.get("stage_name") != stage.name
        or payload.get("backup_name") != backup.name
        or type(payload.get("had_live")) is not bool
        or not _is_sha256(payload.get("expected_digest"))
        or (
            payload.get("original_digest") is not None
            and not _is_sha256(payload.get("original_digest"))
        )
        or bool(payload.get("had_live"))
        != (payload.get("original_digest") is not None)
    ):
        raise Phase1ReplayError(f"{label} schema differs")
    return payload


def _load_marker_payload(
    output_root: Path,
    path: Path,
    *,
    label: str,
) -> dict[str, object]:
    if not _plain_file(path):
        raise Phase1ReplayError(f"{label} is unsafe")
    try:
        body = path.read_bytes()
        payload = json.loads(body.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise Phase1ReplayError(f"{label} is invalid") from error
    validated = _validate_marker_payload(
        output_root,
        payload,
        label=label,
    )
    if body != _marker_bytes(validated):
        raise Phase1ReplayError(f"{label} is not canonical")
    return validated


def _pending_marker_follows(
    current: dict[str, object], pending: dict[str, object],
) -> bool:
    if any(
        current[key] != pending[key]
        for key in _MARKER_KEYS - {"phase"}
    ):
        return False
    current_index = _PHASE_ORDER.index(str(current["phase"]))
    pending_index = _PHASE_ORDER.index(str(pending["phase"]))
    return pending_index in {current_index, current_index + 1}


def _read_marker(
    output_root: Path,
    marker: Path,
    temporary: Path,
    *,
    scope_assertion: Callable[[], None] | None = None,
) -> dict[str, object] | None:
    def assert_scope() -> None:
        if scope_assertion is not None:
            scope_assertion()

    marker_payload: dict[str, object] | None = None
    marker_error: Phase1ReplayError | None = None
    if _entry_exists(marker):
        try:
            marker_payload = _load_marker_payload(
                output_root,
                marker,
                label="Phase-1 transaction marker",
            )
        except Phase1ReplayError as error:
            marker_error = error

    if _entry_exists(temporary):
        try:
            pending_payload = _load_marker_payload(
                output_root,
                temporary,
                label="Phase-1 marker temporary",
            )
        except Phase1ReplayError:
            if marker_payload is None:
                if marker_error is not None or _entry_exists(marker):
                    raise
                stage, backup, _marker, _temporary = _transaction_paths(
                    output_root
                )
                if (
                    not _plain_file(temporary)
                    or _entry_exists(stage)
                    or _entry_exists(backup)
                ):
                    raise
                if _entry_exists(output_root):
                    _verify_prior(output_root)
                # PREPARING writes the first marker before either transaction
                # directory exists.  In exactly that topology a partial plain
                # temporary is crash debris and carries no recovery state.
                assert_scope()
                temporary.unlink()
                assert_scope()
                return None
            # A canonical committed marker is sufficient recovery evidence.  A
            # partial next-marker write is transaction-owned crash debris and
            # must not permanently block recovery or overwrite that evidence.
            assert_scope()
            temporary.unlink()
            assert_scope()
            return marker_payload
        if (
            marker_payload is not None
            and not _pending_marker_follows(marker_payload, pending_payload)
        ):
            raise Phase1ReplayError("Phase-1 marker temporary progression differs")
        assert_scope()
        temporary.replace(marker)
        assert_scope()
        return _load_marker_payload(
            output_root,
            marker,
            label="Phase-1 transaction marker",
        )
    if marker_payload is not None:
        return marker_payload
    if marker_error is not None:
        raise marker_error
    return None


def _assert_owned_partial_directory(root: Path) -> None:
    if (
        not root.is_dir()
        or root.is_symlink()
        or root.resolve() != root.absolute()
    ):
        raise Phase1ReplayError("Phase-1 transaction directory is unsafe")
    entries = tuple(root.iterdir())
    if (
        any(not _plain_file(path) for path in entries)
        or not {path.name for path in entries}.issubset(_OWNED_FILES)
    ):
        raise Phase1ReplayError("Phase-1 transaction directory contains an unowned file")


def _remove_partial_stage(stage: Path) -> None:
    if not _entry_exists(stage):
        return
    _assert_owned_partial_directory(stage)
    shutil.rmtree(stage)


def _retire_uncommitted_output(
    output_root: Path,
    stage: Path,
    expected_digest: str,
) -> None:
    """Move our uncommitted candidate aside before restoring prior live data."""
    if _entry_exists(stage):
        raise Phase1ReplayError("Phase-1 stage conflicts with promoted output")
    try:
        if _verify_bundle(output_root)[1] != expected_digest:
            raise Phase1ReplayError("promoted Phase-1 bundle digest differs")
    except Phase1ReplayError:
        # A failed readback may leave an owned, partial candidate.  Validate
        # topology before moving it; unknown entries are never moved or deleted.
        _assert_owned_partial_directory(output_root)
    output_root.replace(stage)


def _remove_verified_directory(
    root: Path, expected_digest: str, *, prior: bool = False,
) -> None:
    observed = _verify_prior(root) if prior else _verify_bundle(root)[1]
    if observed != expected_digest:
        raise Phase1ReplayError("Phase-1 transaction directory digest differs")
    shutil.rmtree(root)


def _recover(
    output_root: Path,
    *,
    scope_assertion: Callable[[], None] | None = None,
) -> str:
    def assert_scope() -> None:
        if scope_assertion is not None:
            scope_assertion()

    assert_scope()
    stage, backup, marker, temporary = _transaction_paths(output_root)
    payload = _read_marker(
        output_root,
        marker,
        temporary,
        scope_assertion=assert_scope,
    )
    assert_scope()
    if payload is None:
        if _entry_exists(stage) or _entry_exists(backup):
            raise Phase1ReplayError("orphan Phase-1 transaction directory exists")
        return "NONE"

    expected_digest = str(payload["expected_digest"])
    original_digest = payload.get("original_digest")
    if payload["phase"] in {"VERIFIED", "BACKUP_RETIRING"}:
        try:
            live_is_expected = (
                _entry_exists(output_root)
                and _verify_bundle(output_root)[1] == expected_digest
            )
        except Phase1ReplayError:
            live_is_expected = False
        if live_is_expected:
            if _entry_exists(backup) and original_digest is None:
                raise Phase1ReplayError("unexpected Phase-1 backup exists")
            if payload["phase"] == "VERIFIED":
                if _entry_exists(backup):
                    if _verify_prior(backup) != original_digest:
                        raise Phase1ReplayError("Phase-1 backup digest differs")
                assert_scope()
                _phase(payload, "BACKUP_RETIRING", marker, temporary)
            if _entry_exists(backup):
                # BACKUP_RETIRING binds the verified live generation. Cleanup
                # may resume after an interrupted recursive removal, so a
                # now-partial backup is removed only from this exact owned path.
                assert_scope()
                _remove_partial_stage(backup)
                assert_scope()
            if _entry_exists(stage):
                raise Phase1ReplayError("unexpected Phase-1 stage exists")
            assert_scope()
            marker.unlink()
            assert_scope()
            return "FINALIZED"
        # The marker and directory change are separate operations.  If live
        # bytes change after readback, a stale VERIFIED marker must not outrank
        # the still-complete digest-bound original backup.  Fall through to the
        # rollback topology below; a partially retired backup still fails closed.

    if bool(payload["had_live"]):
        if _entry_exists(backup):
            if _verify_prior(backup) != original_digest:
                raise Phase1ReplayError("Phase-1 backup digest differs")
            if _entry_exists(output_root):
                assert_scope()
                _retire_uncommitted_output(
                    output_root,
                    stage,
                    expected_digest,
                )
                assert_scope()
            assert_scope()
            backup.replace(output_root)
            assert_scope()
        elif (
            not _entry_exists(output_root)
            or _verify_prior(output_root) != original_digest
        ):
            raise Phase1ReplayError("original Phase-1 output is unavailable")
    else:
        if _entry_exists(backup):
            raise Phase1ReplayError("unexpected Phase-1 backup exists")
        if _entry_exists(output_root):
            assert_scope()
            _retire_uncommitted_output(
                output_root,
                stage,
                expected_digest,
            )
            assert_scope()

    assert_scope()
    _remove_partial_stage(stage)
    assert_scope()
    marker.unlink()
    assert_scope()
    return "ROLLED_BACK"


def _write_stage(
    stage: Path,
    bundle: _ReplayBundle,
    *,
    promotion_hook: PromotionHook | None = None,
    scope_assertion: Callable[[], None] | None = None,
) -> str:
    stage.mkdir(parents=False, exist_ok=False)
    for name, body in bundle.bodies:
        with (stage / name).open("xb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
    if promotion_hook is not None:
        promotion_hook("after_stage_write")
    if scope_assertion is not None:
        scope_assertion()
    records, digest = _verify_bundle(stage)
    if tuple((record.name, record.sha256) for record in records) != tuple(
        (name, artifact_bytes_digest(body)) for name, body in bundle.bodies
    ):
        raise Phase1ReplayError("staged Phase-1 bundle readback differs")
    return digest


def _phase(
    payload: dict[str, object], value: str, marker: Path, temporary: Path,
) -> None:
    payload["phase"] = value
    _write_marker(marker, temporary, payload)


def _publish_bundle(
    output_root: Path,
    bundle: _ReplayBundle,
    *,
    promotion_hook: PromotionHook | None = None,
    scope_assertion: Callable[[], None] | None = None,
) -> Phase1ReplayReceipt:
    def assert_scope() -> None:
        if scope_assertion is not None:
            scope_assertion()

    assert_scope()
    stage, backup, marker, temporary = _transaction_paths(output_root)
    if any(_entry_exists(path) for path in (marker, temporary, stage, backup)):
        raise Phase1ReplayError("Phase-1 transaction was not recovered before publish")
    had_live = _entry_exists(output_root)
    original_digest = _verify_prior(output_root) if had_live else None
    expected_receipts = tuple(
        _artifact_receipt(name, body) for name, body in bundle.bodies
    )
    expected_digest = _directory_digest(expected_receipts)
    payload: dict[str, object] = {
        "schema": BUNDLE_SCHEMA,
        "phase": "PREPARING",
        "output_name": output_root.name,
        "stage_name": stage.name,
        "backup_name": backup.name,
        "had_live": had_live,
        "original_digest": original_digest,
        "expected_digest": expected_digest,
    }
    try:
        assert_scope()
        _write_marker(marker, temporary, payload)
        if promotion_hook is not None:
            promotion_hook("before_stage_write")
        assert_scope()
        staged_digest = _write_stage(
            stage,
            bundle,
            promotion_hook=promotion_hook,
            scope_assertion=assert_scope,
        )
        if staged_digest != expected_digest:
            raise Phase1ReplayError("staged Phase-1 directory digest differs")
        assert_scope()
        _phase(payload, "STAGED", marker, temporary)
        if promotion_hook is not None:
            promotion_hook("after_stage_readback")
        assert_scope()
        if _verify_bundle(stage)[1] != expected_digest:
            raise Phase1ReplayError("staged Phase-1 bundle changed before promotion")
        if had_live and _verify_prior(output_root) != original_digest:
            raise Phase1ReplayError("existing Phase-1 output changed before promotion")

        _phase(payload, "BACKUP_PENDING", marker, temporary)
        if promotion_hook is not None:
            promotion_hook("before_live_backup")
        assert_scope()
        if had_live:
            output_root.replace(backup)
        if promotion_hook is not None:
            promotion_hook("after_live_backup")

        assert_scope()
        _phase(payload, "PUBLISH_PENDING", marker, temporary)
        if promotion_hook is not None:
            promotion_hook("before_live_publish")
        assert_scope()
        stage.replace(output_root)
        if promotion_hook is not None:
            promotion_hook("after_live_publish")

        assert_scope()
        _phase(payload, "VERIFY_PENDING", marker, temporary)
        if promotion_hook is not None:
            promotion_hook("before_live_readback")
        assert_scope()
        records, observed_digest = _verify_bundle(output_root)
        if observed_digest != expected_digest:
            raise Phase1ReplayError("promoted Phase-1 bundle readback differs")
        if promotion_hook is not None:
            promotion_hook("after_live_readback")
        assert_scope()
        records, observed_digest = _verify_bundle(output_root)
        if observed_digest != expected_digest:
            raise Phase1ReplayError(
                "promoted Phase-1 bundle changed after readback"
            )
        _phase(payload, "VERIFIED", marker, temporary)
        if promotion_hook is not None:
            promotion_hook("after_verified")
        assert_scope()
        if _recover(
            output_root,
            scope_assertion=assert_scope,
        ) != "FINALIZED":
            raise Phase1ReplayError(
                "verified Phase-1 publication was rolled back before return"
            )
        assert_scope()
        return Phase1ReplayReceipt(
            schema=BUNDLE_SCHEMA,
            status="READY",
            output_root=output_root,
            frozen_input_digest=bundle.frozen_input_digest,
            bundle_digest=observed_digest,
            artifacts=records,
        )
    except Exception:
        # Never follow a replaced parent while attempting rollback.  The lock
        # and transaction evidence remain together in the original directory
        # and can be recovered only after its lexical topology is restored.
        assert_scope()
        if _entry_exists(marker) or _entry_exists(temporary):
            try:
                _recover(output_root, scope_assertion=assert_scope)
            except Exception as recovery_error:
                raise Phase1ReplayError(
                    "Phase-1 publication failed and recovery could not complete"
                ) from recovery_error
        raise


def run_phase1_replay(
    request: Phase1ReplayRequest,
    *,
    _promotion_hook: PromotionHook | None = None,
) -> Phase1ReplayReceipt:
    """Compute and atomically publish the fixed offline Phase-1 bundle."""
    if type(request) is not Phase1ReplayRequest:
        raise TypeError("request must be an exact Phase1ReplayRequest")
    project_root = _absolute_plain_path(request.project_root, label="project root")
    if not project_root.is_dir():
        raise Phase1ReplayError("project root is unavailable")
    output_root = (
        _absolute_plain_path(request.output_root, label="output root")
        if request.output_root is not None
        else _absolute_plain_path(
            project_root / DEFAULT_OUTPUT_RELATIVE,
            label="output root",
        )
    )
    if project_root.is_relative_to(output_root):
        raise Phase1ReplayError("output root cannot contain the project root")
    if output_root == Path(output_root.anchor):
        raise Phase1ReplayError("output root cannot replace a filesystem root")
    _assert_output_scope(project_root, output_root)
    _assert_not_nested_in_replay_namespace(project_root, output_root)
    output_root.parent.mkdir(parents=True, exist_ok=True)
    _absolute_plain_path(output_root, label="output root")
    transaction_paths = _transaction_paths(output_root)
    if (
        _entry_exists(output_root)
        and not any(_entry_exists(path) for path in transaction_paths)
    ):
        # Refuse an unowned live target before even creating its sibling lock.
        # A genuine interrupted transaction is recovered under the lock below.
        _verify_prior(output_root)
    with _exclusive_output_lock(output_root) as assert_scope:
        assert_scope()
        _recover(output_root, scope_assertion=assert_scope)
        assert_scope()
        if _entry_exists(output_root):
            _verify_prior(output_root)
        bundle = _build_replay_bundle(project_root, output_root)
        assert_scope()
        return _publish_bundle(
            output_root,
            bundle,
            promotion_hook=_promotion_hook,
            scope_assertion=assert_scope,
        )


__all__ = [
    "BUNDLE_SCHEMA",
    "DEFAULT_OUTPUT_RELATIVE",
    "EXPECTED_FROZEN_DIGEST",
    "Phase1ArtifactReceipt",
    "Phase1ReplayError",
    "Phase1ReplayReceipt",
    "Phase1ReplayRequest",
    "run_phase1_replay",
]
