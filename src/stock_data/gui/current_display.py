"""Validated current-display observations kept separate from EOD history."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path


CURRENT_DISPLAY_PATH = Path("data/normalized/gui_current_price_observation/latest.json")
DASHBOARD_CURRENT_PATH = Path("data/normalized/gui_dashboard_current_observation/latest.json")


@dataclass(frozen=True)
class CurrentDisplayObservation:
    symbol: str
    value: float
    unit: str
    source_date: str
    retrieved_at_utc: str
    provider: str
    interval: str
    finality: str
    refresh_status: str = "UPDATED"

    def validate(self) -> None:
        if not re.fullmatch(r"[0-9A-Z.^=_-]{1,24}", self.symbol):
            raise ValueError("invalid current-display symbol")
        if not isinstance(self.value, (int, float)) or self.value <= 0:
            raise ValueError("current-display value must be positive")
        if self.unit not in {"KRW", "USD", "index points"}:
            raise ValueError("unsupported current-display unit")
        if datetime.fromisoformat(self.source_date).date().isoformat() != self.source_date:
            raise ValueError("source_date must be an ISO date")
        captured = datetime.fromisoformat(self.retrieved_at_utc)
        if captured.tzinfo is None:
            raise ValueError("retrieved_at_utc must be timezone-aware")
        if self.refresh_status != "UPDATED":
            raise ValueError("only a validated promotion may be UPDATED")
        if self.finality not in {
            "POLLABLE_DAILY_AS_RETRIEVED", "PROVIDER_STREAM_OBSERVATION",
        }:
            raise ValueError("unsupported current-display finality")


def load_current_display(root: Path) -> CurrentDisplayObservation | None:
    path = Path(root) / CURRENT_DISPLAY_PATH
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if set(payload) != set(CurrentDisplayObservation.__dataclass_fields__):
            raise ValueError("current-display schema mismatch")
        observation = CurrentDisplayObservation(**payload)
        observation.validate()
        return observation
    except FileNotFoundError:
        return None


def promote_current_display(root: Path, observation: CurrentDisplayObservation) -> str:
    observation.validate()
    path = Path(root) / CURRENT_DISPLAY_PATH
    current = load_current_display(root)
    if current is not None:
        if current == observation:
            return "NOOP_CURRENT"
        if datetime.fromisoformat(observation.retrieved_at_utc) <= datetime.fromisoformat(current.retrieved_at_utc):
            raise ValueError("current-display promotion must be newer than retained state")
    payload = json.dumps(asdict(observation), ensure_ascii=False, indent=2).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    with temporary.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    if load_current_display(root) != observation:
        raise RuntimeError("current-display readback mismatch")
    return "UPDATED"


@dataclass(frozen=True)
class DashboardCurrentObservation:
    identity: str
    value: float
    unit: str
    source_date: str
    retrieved_at_utc: str
    provider: str
    route: str
    interval: str = "1d"
    finality: str = "FDR_YAHOO_DAILY_AS_RETRIEVED"
    refresh_status: str = "UPDATED"

    def validate(self) -> None:
        if not re.fullmatch(r"[A-Z0-9_]{2,24}", self.identity):
            raise ValueError("invalid Dashboard identity")
        CurrentDisplayObservation(
            symbol=self.identity, value=self.value, unit=self.unit,
            source_date=self.source_date, retrieved_at_utc=self.retrieved_at_utc,
            provider=self.provider, interval=self.interval,
            finality="POLLABLE_DAILY_AS_RETRIEVED",
            refresh_status=self.refresh_status,
        ).validate()
        if not self.route.startswith("YAHOO:"):
            raise ValueError("Dashboard FDR route must be explicit")


def load_dashboard_current(root: Path) -> dict[str, DashboardCurrentObservation]:
    path = Path(root) / DASHBOARD_CURRENT_PATH
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    if not isinstance(payload, dict) or set(payload) != {"schema_version", "observations"}:
        raise ValueError("Dashboard current-display envelope mismatch")
    if payload["schema_version"] != 1 or not isinstance(payload["observations"], list):
        raise ValueError("Dashboard current-display version mismatch")
    observations: dict[str, DashboardCurrentObservation] = {}
    for item in payload["observations"]:
        if not isinstance(item, dict) or set(item) != set(DashboardCurrentObservation.__dataclass_fields__):
            raise ValueError("Dashboard current-display row mismatch")
        observation = DashboardCurrentObservation(**item)
        observation.validate()
        if observation.identity in observations:
            raise ValueError("duplicate Dashboard current-display identity")
        observations[observation.identity] = observation
    return observations


def promote_dashboard_current(
    root: Path, observations: list[DashboardCurrentObservation],
) -> str:
    if not observations:
        raise ValueError("Dashboard current-display promotion cannot be empty")
    incoming = {item.identity: item for item in observations}
    if len(incoming) != len(observations):
        raise ValueError("duplicate incoming Dashboard identity")
    for observation in observations:
        observation.validate()
    retained = load_dashboard_current(root)
    merged = dict(retained)
    changed = False
    for identity, observation in incoming.items():
        prior = retained.get(identity)
        if prior == observation:
            continue
        if prior is not None and datetime.fromisoformat(observation.retrieved_at_utc) <= datetime.fromisoformat(prior.retrieved_at_utc):
            raise ValueError("Dashboard current-display promotion must be newer")
        merged[identity] = observation
        changed = True
    if not changed:
        return "NOOP_CURRENT"
    payload = json.dumps({
        "schema_version": 1,
        "observations": [asdict(merged[key]) for key in sorted(merged)],
    }, ensure_ascii=False, indent=2).encode("utf-8")
    path = Path(root) / DASHBOARD_CURRENT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    with temporary.open("xb") as stream:
        stream.write(payload); stream.flush(); os.fsync(stream.fileno())
    temporary.replace(path)
    if load_dashboard_current(root) != merged:
        raise RuntimeError("Dashboard current-display readback mismatch")
    return "UPDATED"
