from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import tempfile


@dataclass
class BackfillState:
    path: Path
    dataset: str
    completed_partitions: set[str]
    valid_empty_partitions: set[str]
    failed_partitions: dict[str, str]
    staged_partitions: set[str] | None = None

    @classmethod
    def load(cls, path: Path, dataset: str) -> "BackfillState":
        if not path.exists():
            return cls(path, dataset, set(), set(), {}, set())
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("dataset") != dataset:
            raise ValueError("checkpoint dataset does not match")
        return cls(
            path, dataset, set(payload.get("completed_partitions", [])),
            set(payload.get("valid_empty_partitions", [])),
            dict(payload.get("failed_partitions", {})),
            set(payload.get("staged_partitions", [])),
        )

    def pending(self, partitions) -> list[str]:
        done = self.completed_partitions | self.valid_empty_partitions
        return [str(value) for value in partitions if str(value) not in done]

    def mark_completed(self, partition: str) -> None:
        self.completed_partitions.add(partition)
        if self.staged_partitions is not None:
            self.staged_partitions.discard(partition)
        self.failed_partitions.pop(partition, None)
        self.save()

    def mark_valid_empty(self, partition: str) -> None:
        self.valid_empty_partitions.add(partition)
        if self.staged_partitions is not None:
            self.staged_partitions.discard(partition)
        self.failed_partitions.pop(partition, None)
        self.save()

    def mark_failed(self, partition: str, error_type: str) -> None:
        self.failed_partitions[partition] = error_type
        self.save()

    def mark_staged(self, partition: str) -> None:
        if self.staged_partitions is None:
            self.staged_partitions = set()
        self.staged_partitions.add(partition)
        self.failed_partitions.pop(partition, None)
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", suffix=".json.tmp", prefix=self.path.stem+"_",
                dir=self.path.parent, delete=False,
            ) as temporary:
                json.dump({
                    "dataset": self.dataset,
                    "completed_partitions": sorted(self.completed_partitions),
                    "valid_empty_partitions": sorted(self.valid_empty_partitions),
                    "failed_partitions": self.failed_partitions,
                    "staged_partitions": sorted(self.staged_partitions or set()),
                }, temporary, ensure_ascii=False, indent=2)
                temporary_path = Path(temporary.name)
            temporary_path.replace(self.path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
