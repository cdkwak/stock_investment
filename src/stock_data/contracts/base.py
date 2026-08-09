from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ColumnContract:
    name: str
    dtype: str
    nullable: bool
    unit: str | None = None
    description: str | None = None


@dataclass(frozen=True)
class DatasetContract:
    name: str
    version: int
    status: str
    description: str
    source: str
    layer: str
    storage_format: str
    frequency: str
    timezone: str | None
    primary_key: tuple[str, ...]
    sort_key: tuple[str, ...]
    partition_by: tuple[str, ...]
    columns: tuple[ColumnContract, ...]

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(column.name for column in self.columns)

