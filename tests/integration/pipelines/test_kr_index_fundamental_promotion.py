from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

from stock_data.pipelines.kr_index_fundamental_promotion import (
    dry_run_retained_index_fundamentals,
    merge_index_fundamental_frames,
    normalize_index_fundamental_response,
    prepare_retained_index_fundamentals,
    stage_retained_index_fundamentals,
)


def _row(date: str, *, per: str = "15.0", pbr: str = "1.2") -> dict[str, str]:
    return {
        "TRD_DD": date,
        "CLSPRC_IDX": "3,200.50",
        "WT_PER": per,
        "WT_STKPRC_NETASST_RTO": pbr,
        "DIV_YD": "-",
    }


def _retained_run(root: Path, parts: dict[str, list[dict[str, str]]]) -> Path:
    run = root / "retained"
    run.mkdir()
    completed = {}
    sequence = 0
    for name, rows in parts.items():
        sequence += 1
        body_file = f"response_{sequence:02d}_{name}.json"
        body = json.dumps({"output": rows, "CURRENT_DATETIME": "ignored"}).encode()
        (run / body_file).write_bytes(body)
        completed[name] = {
            "body_file": body_file,
            "body_sha256": hashlib.sha256(body).hexdigest(),
            "classification": "SUCCESS",
            "rows": len(rows),
        }
    (run / "checkpoint.json").write_text(
        json.dumps({
            "status": "COMPLETE", "run_id": "synthetic", "completed": completed
        }),
        encoding="utf-8",
    )
    return run


def _parts() -> dict[str, list[dict[str, str]]]:
    return {
        "index_kospi_history_01": [_row("2025/12/31", pbr="-")],
        "index_kospi_history_02": [_row("2026/01/02")],
        "index_kosdaq_history_01": [_row("2025/12/31", per="-")],
        "index_kosdaq_history_02": [_row("2026/01/02")],
    }


def test_offline_stage_is_partitioned_hash_bound_and_network_free(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "requests.sessions.Session.request",
        lambda *args, **kwargs: pytest.fail("offline staging attempted a network call"),
    )
    run = _retained_run(tmp_path, _parts())
    before = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in run.iterdir()}

    dry_run = dry_run_retained_index_fundamentals(run)
    assert dry_run["rows"] == 4
    assert dry_run["selected_response_files"] == 4
    assert dry_run["coverage"]["KOSPI"]["maximum_date"] == "2026-01-02"
    assert not (tmp_path / "stage").exists()

    staged = stage_retained_index_fundamentals(
        retained_run_root=run, staging_root=tmp_path / "stage"
    )
    assert staged["semantic_sha256"] == dry_run["semantic_sha256"]
    assert len(staged["output_files"]) == 4
    frames = [pd.read_parquet(tmp_path / "stage" / item["path"])
              for item in staged["output_files"]]
    output = pd.concat(frames, ignore_index=True)
    assert output["source_response_sha256"].str.fullmatch(r"[0-9a-f]{64}").all()
    assert output["weighted_per"].isna().sum() == 1
    assert output["weighted_pbr"].isna().sum() == 1
    after = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in run.iterdir()}
    assert after == before


@pytest.mark.parametrize("conflicting", [False, True])
def test_multi_part_duplicate_or_conflicting_date_fails_before_write(
    tmp_path, conflicting
):
    parts = _parts()
    parts["index_kospi_history_02"] = [
        _row("2025/12/31", per="16.0" if conflicting else "15.0", pbr="-")
    ]
    run = _retained_run(tmp_path, parts)
    with pytest.raises(ValueError, match="duplicate or conflicting"):
        stage_retained_index_fundamentals(
            retained_run_root=run, staging_root=tmp_path / "stage"
        )
    assert not (tmp_path / "stage").exists()


def test_response_hash_mismatch_fails_closed(tmp_path):
    run = _retained_run(tmp_path, _parts())
    target = next(run.glob("response_*.json"))
    target.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="SHA-256 differs"):
        dry_run_retained_index_fundamentals(run)


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_provider_ratio_fails_before_write(tmp_path, token):
    parts = _parts()
    parts["index_kospi_history_01"] = [
        _row("2025/12/31", per=token, pbr="-")
    ]
    run = _retained_run(tmp_path, parts)

    with pytest.raises(ValueError, match="non-finite provider number"):
        stage_retained_index_fundamentals(
            retained_run_root=run, staging_root=tmp_path / "stage"
        )

    assert not (tmp_path / "stage").exists()


def test_incremental_overlap_is_idempotent_but_conflict_fails_closed(tmp_path):
    run = _retained_run(tmp_path, _parts())
    existing = prepare_retained_index_fundamentals(run).dataframe
    body = (run / "response_02_index_kospi_history_02.json").read_bytes()
    identical = normalize_index_fundamental_response(
        body, index_code="1001", market="KOSPI",
    )
    assert len(merge_index_fundamental_frames(existing, (identical,))) == len(existing)

    conflicting_body = json.dumps({
        "output": [_row("2026/01/02", per="16.0")]
    }).encode()
    conflict = normalize_index_fundamental_response(
        conflicting_body, index_code="1001", market="KOSPI",
    )
    with pytest.raises(ValueError, match="conflicts with retained key"):
        merge_index_fundamental_frames(existing, (conflict,))
