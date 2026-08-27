"""Strict, read-only parser for retained source-routing inventory CSVs."""
from __future__ import annotations

import csv
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class SourceRoutingRegistryError(ValueError): pass

class GuiGate(StrEnum):
    NO = "NO"; STATUS_ONLY = "STATUS_ONLY"; DESCRIPTIVE = "YES_DESCRIPTIVE"; DIRECT = "YES_DIRECT"; BOUNDARY = "YES_OR_BOUNDARY"
class BacktestGate(StrEnum):
    NO = "NO"; BOUNDARY_ONLY = "BOUNDARY_ONLY"; FROZEN_ONLY = "YES_FROZEN_PHASE1"
class PitGate(StrEnum):
    NON_PREDICTIVE = "NON_PREDICTIVE"; BLOCKED = "PIT_BLOCKED"; BLOCKED_OR_NOT_INPUT = "PIT_BLOCKED_OR_NOT_INPUT"; LIMITED = "PIT_LIMITED"; SAFE = "PIT_SAFE"; SAFE_BOUNDARY = "PIT_SAFE_BOUNDARY"; RESEARCH_ONLY = "RESEARCH_ONLY"; UNVERIFIED = "UNVERIFIED_ROUTE_SPECIFIC"
class FallbackGate(StrEnum):
    CROSS_CHECK_ONLY = "CROSS_CHECK_ONLY"; EXACT_ACCEPTED = "EXACT_ACCEPTED_LANES_ONLY"; EXACT_ALLOWLIST = "EXACT_ALLOWLIST_ONLY"; EXCLUDE_INDEPENDENT = "EXCLUDE_AS_INDEPENDENT_FALLBACK"; EXCLUDE_OFFICIAL = "EXCLUDE_AS_OFFICIAL_FALLBACK"; EXCLUDE_UNIVERSE = "EXCLUDE_AS_OFFICIAL_UNIVERSE"; HOLD_CURRENT_CROSSCHECK = "HOLD_CURRENT_CROSS_CHECK"; HOLD_CURRENT_ONLY = "HOLD_CURRENT_ONLY"; HOLD_NO_PIT = "HOLD_NO_PIT"; HOLD_OR_EXCLUDE = "HOLD_OR_EXCLUDE_PER_DATASET"; HOLD_RESEARCH = "HOLD_RESEARCH_ONLY"; HOLD_ROUTE = "HOLD_ROUTE_SPECIFIC"; NO_AUTO = "NO_AUTOMATIC_FALLBACK"; NOT_UNTIL_CONTRACTED = "NOT_A_FALLBACK_UNTIL_CONTRACTED"; NOT_APPLICABLE = "NOT_APPLICABLE"; VIXCLS_ONLY = "ONLY_VIXCLS_SCHEMA_FALLBACK_ACCEPTED"; PRIMARY = "PRIMARY"; RESEARCH_METADATA = "RESEARCH_METADATA_ONLY"

_COLUMNS = ("record_id","korean_description","existing_retained_source","ls_kb_toss_exact_route_or_noncoverage","pykrx_fdr_yfinance_candidate_or_constraint","preferred_current_display_priority","achievable_interval","latest_validation_outcome","gui_use","backtest_use","pit_finality","automatic_fallback_eligibility","exact_blocker","evidence","followup_ur")

def _head(value: str) -> str: return value.split(";", 1)[0].strip()

@dataclass(frozen=True)
class SourceRouteRecord:
    record_id: str; korean_description: str; retained_source: str; broker_route: str; library_candidate: str; display_priority: str; interval: str; validation: str; gui_gate: GuiGate; backtest_gate: BacktestGate; pit_gate: PitGate; fallback_gate: FallbackGate; blocker: str; evidence: str; followup_ur: str
    @property
    def automatic_fallback_allowed(self) -> bool:
        # One accepted parser fallback is intentionally narrow; all broad,
        # cross-check, primary, candidate and exact-lane labels are not an
        # authorization for automatic source substitution.
        return self.fallback_gate is FallbackGate.VIXCLS_ONLY
    @property
    def backtest_promotion_allowed(self) -> bool: return False

class SourceRoutingRegistry:
    def __init__(self, records: tuple[SourceRouteRecord, ...]) -> None:
        if not records: raise SourceRoutingRegistryError("source-routing registry is empty")
        ids = [record.record_id for record in records]
        if len(ids) != len(set(ids)): raise SourceRoutingRegistryError("duplicate source-routing record_id")
        self._records = {record.record_id: record for record in records}
    def __getitem__(self, record_id: str) -> SourceRouteRecord:
        try: return self._records[record_id]
        except KeyError as error: raise SourceRoutingRegistryError("unknown source-routing record_id") from error
    def __len__(self) -> int: return len(self._records)
    @classmethod
    def from_csv(cls, path: Path, *, expected_ids: frozenset[str] | None = None) -> "SourceRoutingRegistry":
        with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if tuple(reader.fieldnames or ()) != _COLUMNS: raise SourceRoutingRegistryError("source-routing CSV schema mismatch")
            records=[]
            for row in reader:
                if set(row) != set(_COLUMNS) or not all(row.get(name, "").strip() for name in ("record_id","korean_description","existing_retained_source","broker_route".replace("broker_route","ls_kb_toss_exact_route_or_noncoverage"),"evidence")):
                    raise SourceRoutingRegistryError("source-routing CSV has missing required value")
                record_id=row["record_id"]
                if not record_id.replace("_", "").replace("-", "").isalnum(): raise SourceRoutingRegistryError("invalid source-routing record_id")
                try:
                    records.append(SourceRouteRecord(record_id,row["korean_description"],row["existing_retained_source"],row["ls_kb_toss_exact_route_or_noncoverage"],row["pykrx_fdr_yfinance_candidate_or_constraint"],row["preferred_current_display_priority"],row["achievable_interval"],row["latest_validation_outcome"],GuiGate(_head(row["gui_use"])),BacktestGate(_head(row["backtest_use"])),PitGate(_head(row["pit_finality"])),FallbackGate(_head(row["automatic_fallback_eligibility"])),row["exact_blocker"],row["evidence"],row["followup_ur"]))
                except ValueError as error: raise SourceRoutingRegistryError("unknown source-routing decision") from error
        registry = cls(tuple(records))
        if expected_ids is not None and set(registry._records) != set(expected_ids):
            raise SourceRoutingRegistryError("source-routing expected-ID reconciliation failed")
        return registry
