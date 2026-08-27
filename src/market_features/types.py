from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re
from typing import Literal


@dataclass(frozen=True)
class FeatureDefinition:
    feature_name: str
    feature_version: int
    lookback_trading_days: int
    missing_policy: Literal["DROP_UNTIL_LOOKBACK_COMPLETE"]
    source_dataset: str
    source_contract_version: int
    pit_status: Literal["PIT_SAFE_EOD_T_PLUS_1"]


@dataclass(frozen=True)
class FrozenInputManifest:
    dataset: str
    contract_version: int
    coverage_start: str
    coverage_end: str
    rows: int
    files: int
    bytes: int
    root_manifest_sha256: str
    decision_rule: Literal["T_CLOSE_OBSERVED_USABLE_FROM_T_PLUS_1_DECISION"]

    def __post_init__(self) -> None:
        if self.rows < 1 or self.files < 1 or self.bytes < 1:
            raise ValueError("frozen input counts must be positive")
        if re.fullmatch(r"[0-9a-f]{64}", self.root_manifest_sha256) is None:
            raise ValueError("frozen input digest must be SHA-256")
        try:
            start = date.fromisoformat(self.coverage_start)
            end = date.fromisoformat(self.coverage_end)
        except ValueError as error:
            raise ValueError("frozen coverage must use ISO dates") from error
        if start > end:
            raise ValueError("frozen coverage is reversed")
