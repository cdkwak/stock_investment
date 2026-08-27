from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from stock_data.contracts.vkospi_daily import KR_VKOSPI_DAILY, KR_VKOSPI_RAW_DAILY
from stock_data.orchestration.vkospi_daily_incremental import (
    VKOSPIDailyOperationError,
    run_offline_daily_append,
)
from stock_data.storage.contract_parquet import read_dataset
from stock_data.validation.vkospi_daily import validate_vkospi_daily, validate_vkospi_raw_daily


def _body(day: str, close: str = "55.31") -> bytes:
    return json.dumps(
        {
            "CURRENT_DATETIME": "2026-08-18 09:00:00",
            "output": [
                {
                    "TRD_DD": day.replace("-", "/"),
                    "CLSPRC_IDX": close,
                    "PRV_DD_CMPR": "0.03",
                    "UPDN_RATE": "+0.05",
                    "OPNPRC_IDX": "55.38",
                    "HGPRC_IDX": "56.27",
                    "LWPRC_IDX": "55.00",
                    "FLUC_TP_CD": "1",
                }
            ],
        }
    ).encode()


def _landing(tmp_path: Path, day: str, close: str = "55.31", name: str = "response") -> Path:
    path = tmp_path / "data/landing/krx/vkospi_daily_raw/run" / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_body(day, close))
    return path


def _roots(tmp_path: Path) -> tuple[Path, Path, Path]:
    return (
        tmp_path / "data/raw/kr_vkospi_daily",
        tmp_path / "data/normalized/kr_vkospi_daily",
        tmp_path / "data/state",
    )


def _run(tmp_path: Path, landing: Path, day: str, run_id: str = "vk-1", **kwargs):
    raw, normalized, state = _roots(tmp_path)
    return run_offline_daily_append(
        landing,
        finalized_market_date=day,
        finality_confirmed=True,
        run_id=run_id,
        raw_root=raw,
        normalized_root=normalized,
        state_root=state,
        **kwargs,
    )


def test_exact_date_append_is_landing_first_atomic_and_idempotent(tmp_path: Path) -> None:
    landing = _landing(tmp_path, "2026-08-17")
    landing_before = landing.read_bytes()
    result = _run(tmp_path, landing, "2026-08-17")
    raw_root, normalized_root, state_root = _roots(tmp_path)

    assert result.status == "SUCCEEDED" and result.inserted_rows == 1
    assert landing.read_bytes() == landing_before
    assert read_dataset(raw_root, KR_VKOSPI_RAW_DAILY, validate_vkospi_raw_daily)[
        "market_date"
    ].tolist() == ["2026-08-17"]
    normalized = read_dataset(normalized_root, KR_VKOSPI_DAILY, validate_vkospi_daily)
    assert normalized["market_date"].tolist() == ["2026-08-17"]
    assert normalized["pit_status"].unique().tolist() == [
        "PIT_LIMITED_PUBLICATION_REVISION_UNRESOLVED"
    ]
    assert json.loads((state_root / "kr_vkospi_daily.json").read_text())["last_accepted_market_date"] == "2026-08-17"

    replay = _run(tmp_path, landing, "2026-08-17", run_id="vk-2")
    assert replay.status == "NOOP_IDEMPOTENT" and replay.inserted_rows == 0
    assert landing.read_bytes() == landing_before


def test_finality_and_exact_date_fail_closed_without_outputs(tmp_path: Path) -> None:
    landing = _landing(tmp_path, "2026-08-17")
    raw, normalized, state = _roots(tmp_path)
    with pytest.raises(VKOSPIDailyOperationError, match="finality"):
        run_offline_daily_append(
            landing,
            finalized_market_date="2026-08-17",
            finality_confirmed=False,
            run_id="vk-finality",
            raw_root=raw,
            normalized_root=normalized,
            state_root=state,
        )
    with pytest.raises(VKOSPIDailyOperationError, match="exactly"):
        _run(tmp_path, landing, "2026-08-18", run_id="vk-date")
    assert not raw.exists() and not normalized.exists()


def test_conflict_and_historical_gap_do_not_overwrite(tmp_path: Path) -> None:
    accepted = _landing(tmp_path, "2026-08-17", name="accepted")
    _run(tmp_path, accepted, "2026-08-17")
    raw, normalized, _ = _roots(tmp_path)
    before = {path: path.read_bytes() for root in (raw, normalized) for path in root.rglob("data.parquet")}

    conflict = _landing(tmp_path, "2026-08-17", close="55.99", name="conflict")
    with pytest.raises(VKOSPIDailyOperationError, match="conflict"):
        _run(tmp_path, conflict, "2026-08-17", run_id="vk-conflict")
    historical = _landing(tmp_path, "2026-08-14", name="historical")
    with pytest.raises(VKOSPIDailyOperationError, match="historical target"):
        _run(tmp_path, historical, "2026-08-14", run_id="vk-history")
    assert {path: path.read_bytes() for root in (raw, normalized) for path in root.rglob("data.parquet")} == before


def test_checkpoint_failure_rolls_back_both_layers_and_prior_state(tmp_path: Path) -> None:
    first = _landing(tmp_path, "2026-08-14", name="first")
    _run(tmp_path, first, "2026-08-14", run_id="vk-first")
    raw, normalized, state = _roots(tmp_path)
    before = {path: path.read_bytes() for root in (raw, normalized) for path in root.rglob("data.parquet")}
    checkpoint_before = (state / "kr_vkospi_daily.json").read_bytes()
    second = _landing(tmp_path, "2026-08-17", name="second")

    def fail_checkpoint(path: Path, payload: object) -> None:
        raise RuntimeError("injected checkpoint failure")

    with pytest.raises(RuntimeError, match="checkpoint failure"):
        _run(
            tmp_path,
            second,
            "2026-08-17",
            run_id="vk-failure",
            checkpoint_writer=fail_checkpoint,
        )
    assert {path: path.read_bytes() for root in (raw, normalized) for path in root.rglob("data.parquet")} == before
    assert (state / "kr_vkospi_daily.json").read_bytes() == checkpoint_before
    assert json.loads((state / "journal/kr_vkospi_daily--vk-failure.json").read_text())["status"] == "FAILED"
