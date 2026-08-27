from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from stock_data.orchestration.toss_domestic_indices_ur245 import MANIFEST_PATH, run_injected


def _install_manifest(root: Path) -> None:
    destination = root / MANIFEST_PATH; destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes((Path("data/state/toss_domestic_indices_ur245_activation.json")).read_bytes())


def _payload(symbol: str, stamp: str = "2026-08-24T09:30:00+09:00") -> dict:
    return {"result": [{"symbol": symbol, "timestamp": stamp, "lastPrice": "3000.5"}]}


def test_preopen_and_postclose_construct_zero_callbacks(tmp_path: Path) -> None:
    _install_manifest(tmp_path); calls: list[str] = []
    factory = lambda symbol: calls.append(symbol) or _payload(symbol)
    assert run_injected(tmp_path, now=datetime.fromisoformat("2026-08-24T08:30:00+09:00"), response_factory=factory)["calls"] == 0
    assert run_injected(tmp_path, now=datetime.fromisoformat("2026-08-24T15:30:00+09:00"), response_factory=factory)["calls"] == 0
    assert calls == []


def test_eligible_window_is_serial_landing_first_and_no_backfill(tmp_path: Path) -> None:
    _install_manifest(tmp_path); calls: list[str] = []
    result = run_injected(tmp_path, now=datetime.fromisoformat("2026-08-24T09:31:00+09:00"), response_factory=lambda symbol: calls.append(symbol) or _payload(symbol))
    assert result["status"] == "COMPLETE" and result["boundary"] == "2026-08-24T09:30:00+09:00" and calls == ["KOSPI", "KOSDAQ"]
    assert run_injected(tmp_path, now=datetime.fromisoformat("2026-08-24T09:40:00+09:00"), response_factory=lambda _: (_ for _ in ()).throw(AssertionError()))["calls"] == 0
    state = json.loads((tmp_path / "data/state/current_observations/toss_kospi_price_snapshot_ur245.json").read_text(encoding="utf-8"))
    assert state["observations"][0]["unit"] == "index points"
