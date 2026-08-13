from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from urllib.parse import unquote

import pytest

from scripts.manual import bok_ecos_treasury_pilot_support as support
from scripts.manual import pilot_bok_ecos_treasury as runner


FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures/bok_ecos_treasury_documented.json").read_text()
)
TENORS = ("2Y", "3Y", "5Y", "10Y", "20Y", "30Y")


def test_manual_entrypoint_help_runs_from_repository_root():
    script = Path(__file__).parents[1] / "scripts/manual/pilot_bok_ecos_treasury.py"
    result = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=script.parents[2],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--phase" in result.stdout


def config_payload():
    return {
        "table_code": "LOCAL001",
        "table_name": "Reviewed daily government bond yields",
        "cycle": "D",
        "tenors": {
            tenor: {
                "item_code": f"LOCAL{tenor}",
                "item_name": f"Government bond {tenor}",
                "unit_name": "percent",
            }
            for tenor in TENORS
        },
        "dates": {
            "recent_normal": "20260807",
            "two_year_introduction_boundary": "20210310",
            "retained_source_gap": "20190315",
            "early_2019": "20190102",
        },
    }


def write_config(tmp_path, payload=None):
    path = tmp_path / "pilot.json"
    path.write_text(json.dumps(payload or config_payload()), encoding="utf-8")
    return path


def response(payload, status=200):
    return type(
        "Response", (),
        {"status_code": status, "content": json.dumps(payload).encode("utf-8")},
    )()


class MetadataSession:
    def __init__(self):
        self.calls = []

    def get(self, url, timeout):
        self.calls.append((url, timeout))
        return response(FIXTURE["metadata"])


class ValueSession:
    def __init__(self):
        self.calls = []

    def get(self, url, timeout):
        self.calls.append((url, timeout))
        path = unquote(url)
        parts = path.rstrip("/").split("/")
        source_date, item_code = parts[-2], parts[-1]
        tenor = item_code.removeprefix("LOCAL")
        payload = {
            "StatisticSearch": {
                "list_total_count": 1,
                "row": [{
                    "STAT_CODE": "LOCAL001",
                    "STAT_NAME": "Reviewed daily government bond yields",
                    "ITEM_CODE1": item_code,
                    "ITEM_NAME1": f"Government bond {tenor}",
                    "UNIT_NAME": "percent",
                    "TIME": source_date,
                    "DATA_VALUE": "3.125",
                }],
            }
        }
        return response(payload)


class NoCallSession:
    def get(self, *_args, **_kwargs):
        raise AssertionError("completed resume must make no HTTP request")


def test_config_requires_explicit_six_tenor_identity_and_four_dates(tmp_path):
    config = support.load_config(write_config(tmp_path))
    assert tuple(config.tenors) == TENORS
    assert len(support.plan_value_scopes(config)) == 8
    broken = config_payload()
    broken["tenors"]["2Y"]["item_code"] = "ITEM_FROM_REVIEW"
    with pytest.raises(support.EcosPilotError, match="placeholder"):
        support.load_config(write_config(tmp_path, broken))


def test_documented_metadata_and_value_fields_are_strict(tmp_path):
    config = support.load_config(write_config(tmp_path))
    metadata = support.parse_item_metadata(json.dumps(FIXTURE["metadata"]).encode(), config)
    assert len(metadata) == 6
    scope = support.plan_value_scopes(config)[0]
    parsed = support.parse_value(json.dumps(FIXTURE["value"]).encode(), config, scope)
    assert parsed.classification == "SUCCESS"
    assert parsed.observations[0]["published_at"] is None
    assert parsed.observations[0]["availability_status"].startswith("blocked_")
    empty = support.parse_value(json.dumps(FIXTURE["valid_empty"]).encode(), config, scope)
    assert empty.classification == "VALID_EMPTY"


def test_metadata_requires_exact_full_official_table_name(tmp_path):
    config = support.load_config(write_config(tmp_path))
    payload = json.loads(json.dumps(FIXTURE["metadata"]))
    for row in payload["StatisticItemList"]["row"]:
        row["STAT_NAME"] = "1.3.2.1. " + row["STAT_NAME"]
    with pytest.raises(support.EcosPilotError, match="table identity"):
        support.parse_item_metadata(json.dumps(payload).encode(), config)


def test_retained_metadata_can_be_finalized_offline_after_exact_label_correction(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv(runner.API_KEY_ENV, "literal-secret-key")
    old_path = write_config(tmp_path)
    old_config = support.load_config(old_path)
    old_hash = support.config_sha256(old_config)
    payload = json.loads(json.dumps(FIXTURE["metadata"]))
    for row in payload["StatisticItemList"]["row"]:
        row["STAT_NAME"] = "1.3.2.1. " + row["STAT_NAME"]

    class CorrectedMetadataSession:
        def get(self, url, timeout):
            return response(payload)

    with pytest.raises(support.EcosPilotError, match="table identity"):
        runner.run_metadata(
            project_root=tmp_path, config_path=old_path,
            session=CorrectedMetadataSession(),
        )
    run_dir = next((tmp_path / runner.LANDING_RELATIVE).glob("metadata_*"))
    corrected = config_payload()
    corrected["table_name"] = "1.3.2.1. Reviewed daily government bond yields"
    corrected_path = write_config(tmp_path, corrected)
    result = runner.finalize_retained_metadata(
        project_root=tmp_path, config_path=corrected_path, run_dir=run_dir,
        original_config_sha256=old_hash,
    )
    assert result["status"] == "METADATA_CAPTURED_REVIEW_REQUIRED"
    assert result["raw_requests"] == 1
    assert result["network_requests_during_finalization"] == 0
    summary = json.loads((run_dir / "metadata_summary.json").read_text())
    assert summary["network_requests_during_finalization"] == 0
    assert summary["original_reviewed_config_sha256"] == old_hash
    assert [row["STAT_NAME"] for row in summary["six_tenor_identity"]] == [
        corrected["table_name"]
    ] * 6


def test_value_parser_rejects_observation_overflow(tmp_path):
    config = support.load_config(write_config(tmp_path))
    scope = support.plan_value_scopes(config)[0]
    payload = json.loads(json.dumps(FIXTURE["value"]))
    payload["StatisticSearch"]["row"] *= 3
    payload["StatisticSearch"]["list_total_count"] = 3
    with pytest.raises(support.EcosPilotError, match="two-observation"):
        support.parse_value(json.dumps(payload).encode(), config, scope)


def test_two_phase_pilot_is_bounded_landing_first_redacted_and_resumable(tmp_path, monkeypatch):
    monkeypatch.setenv(runner.API_KEY_ENV, "literal-secret-key")
    config_path = write_config(tmp_path)
    metadata_session = MetadataSession()
    metadata = runner.run_metadata(
        project_root=tmp_path, config_path=config_path, session=metadata_session
    )
    assert len(metadata_session.calls) == support.MAX_METADATA_REQUESTS == 1
    metadata_dir = Path(metadata["run_dir"])
    assert metadata["status"] == "METADATA_CAPTURED_REVIEW_REQUIRED"
    assert "literal-secret-key" not in (metadata_dir / "call_ledger.jsonl").read_text()
    assert (metadata_dir / "response_01_item_metadata.json").read_bytes()

    value_session = ValueSession()
    values = runner.run_values(
        project_root=tmp_path,
        config_path=config_path,
        metadata_run_dir=metadata_dir,
        approve_metadata_sha256=metadata["metadata_summary_sha256"],
        session=value_session,
    )
    assert len(value_session.calls) == support.MAX_VALUE_REQUESTS == 8
    assert values["observations"] == 8
    assert values["status"] == "VALUE_PILOT_COMPLETE_REVIEW_REQUIRED"
    value_dir = Path(values["run_dir"])
    assert len(list(value_dir.glob("response_*.json"))) == 8
    assert not (tmp_path / "data/normalized").exists()
    assert "literal-secret-key" not in (value_dir / "call_ledger.jsonl").read_text()
    comparison = json.loads((value_dir / "comparison_to_toss.json").read_text())
    assert {row["classification"] for row in comparison} == {"TOSS_MISSING"}
    assert all(row["compatibility_inferred"] is False for row in comparison)

    resumed = runner.run_values(
        project_root=tmp_path,
        config_path=config_path,
        metadata_run_dir=metadata_dir,
        approve_metadata_sha256=metadata["metadata_summary_sha256"],
        resume_run_dir=value_dir,
        session=NoCallSession(),
    )
    assert resumed["raw_requests_this_process"] == 0
    assert resumed["observations"] == 8


def test_wrong_metadata_approval_and_implicit_live_mode_are_rejected(tmp_path, monkeypatch):
    with pytest.raises(SystemExit, match="confirm-live"):
        runner.main([
            "--project-root", str(tmp_path), "--config", str(write_config(tmp_path)),
            "--phase", "metadata",
        ])
    monkeypatch.setenv(runner.API_KEY_ENV, "literal-secret-key")
    metadata = runner.run_metadata(
        project_root=tmp_path, config_path=write_config(tmp_path), session=MetadataSession()
    )
    with pytest.raises(runner.PilotStopped, match="approval hash"):
        runner.run_values(
            project_root=tmp_path,
            config_path=tmp_path / "pilot.json",
            metadata_run_dir=Path(metadata["run_dir"]),
            approve_metadata_sha256="0" * 64,
            session=NoCallSession(),
        )
