from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Iterable


@dataclass(frozen=True)
class ExperimentRecord:
    experiment_id: str
    frozen_input_digest: str
    feature_set: tuple[str, ...]
    feature_versions: tuple[str, ...]
    label_version: str
    split_policy: str
    purge: int
    embargo: int
    threshold_rule: str
    result_artifact: str
    code_version: str
    code_tree_digest: str
    threshold_values_digest: str
    signals_artifact_digest: str
    result_artifact_digest: str
    label_horizon_trading_days: int
    signal_pit_status: str
    holdout_results_reviewed: bool = False

    def __post_init__(self) -> None:
        if not self.experiment_id or not self.feature_set or not self.feature_versions:
            raise ValueError("experiment identity and feature versions are required")
        digests = (
            self.frozen_input_digest, self.code_tree_digest,
            self.threshold_values_digest, self.signals_artifact_digest,
            self.result_artifact_digest,
        )
        if any(re.fullmatch(r"[0-9a-f]{64}", digest) is None for digest in digests):
            raise ValueError("experiment digests must be SHA-256")
        if self.purge < 0 or self.embargo < 0:
            raise ValueError("purge and embargo cannot be negative")
        if self.label_horizon_trading_days < 1 or self.purge < self.label_horizon_trading_days:
            raise ValueError("purge must cover label_horizon_trading_days")
        if self.signal_pit_status != "PIT_SAFE_EOD_T_PLUS_1":
            raise ValueError("experiment signal PIT status is invalid")
        if not all((self.label_version, self.split_policy, self.threshold_rule,
                    self.result_artifact, self.code_version)):
            raise ValueError("experiment identity fields cannot be empty")
        if self.holdout_results_reviewed:
            raise ValueError("sealed holdout results cannot be registered")


def artifact_bytes_digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def canonical_json_digest(payload: object) -> str:
    body = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return artifact_bytes_digest(body)


def code_tree_digest(root: Path, relative_paths: Iterable[Path | str]) -> str:
    resolved_root = root.resolve()
    names = sorted({Path(path).as_posix() for path in relative_paths})
    if not names:
        raise ValueError("code tree cannot be empty")
    digest = hashlib.sha256()
    for name in names:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("code tree path escapes root")
        path = (resolved_root / relative).resolve()
        if not path.is_relative_to(resolved_root) or path.is_symlink() or not path.is_file():
            raise ValueError("code tree path is not a regular owned file")
        digest.update(relative.as_posix().encode("utf-8") + b"\0")
        digest.update(artifact_bytes_digest(path.read_bytes()).encode("ascii") + b"\n")
    return digest.hexdigest()


def serialize_experiment_registry(records: tuple[ExperimentRecord, ...]) -> str:
    ids = [record.experiment_id for record in records]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate experiment_id")
    payload = {
        "version": 1,
        "experiments": [
            asdict(record) for record in sorted(records, key=lambda item: item.experiment_id)
        ],
    }
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ) + "\n"


__all__ = [
    "ExperimentRecord", "artifact_bytes_digest", "canonical_json_digest",
    "code_tree_digest", "serialize_experiment_registry",
]
