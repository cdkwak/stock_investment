from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from urllib.parse import unquote
from zoneinfo import ZoneInfo

import pytest

from scripts.manual.pilot import bok_ecos_treasury_pilot_support as support
from scripts.manual.pilot import pilot_bok_ecos_treasury as runner


FIXTURE = json.loads(
    (Path(__file__).resolve().parents[3] / "tests/fixtures/bok_ecos_treasury_documented.json").read_text()
)
TENORS = ("2Y", "3Y", "5Y", "10Y", "20Y", "30Y")
FINALITY_CODES = {
    "2Y": "010195000", "3Y": "010200000", "5Y": "010200001",
    "10Y": "010210000", "20Y": "010220000", "30Y": "010230000",
}


def test_manual_entrypoint_help_runs_from_repository_root():
    script = Path(__file__).resolve().parents[3] / "scripts/manual/pilot/pilot_bok_ecos_treasury.py"
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

    post = get


def finality_metadata_summary(tmp_path):
    payload = {
        "six_tenor_identity": [
            {
                "tenor": tenor,
                "STAT_CODE": support.FINALITY_TABLE_CODE,
                "STAT_NAME": support.FINALITY_TABLE_NAME,
                "ITEM_CODE": FINALITY_CODES[tenor],
                "ITEM_NAME": f"국고채({tenor.removesuffix('Y')}년)",
                "CYCLE": "D",
                "UNIT_NAME": support.FINALITY_UNIT_NAME,
            }
            for tenor in TENORS
        ]
    }
    path = tmp_path / "metadata_summary.json"
    path.write_bytes((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


class FinalitySession:
    def __init__(self, dates, *, missing_tenor=None, changed_previous=False):
        self.dates = tuple(dates)
        self.missing_tenor = missing_tenor
        self.changed_previous = changed_previous
        self.get_calls = []
        self.post_calls = []

    def post(self, url, json, timeout):
        self.post_calls.append((url, json, timeout))
        return response({
            "header": {"rspnDvsnCd": "0", "ipAddr": "203.0.113.44"},
            "data": {"dsInfoList": [{
                "dsId": support.FINALITY_TABLE_CODE,
                "dsNm": support.FINALITY_TABLE_NAME,
                "prvsMrkYn": "N",
                "brknwsMrkYn": "N",
            }]},
        })

    def get(self, url, timeout):
        self.get_calls.append((url, timeout))
        parts = unquote(url).rstrip("/").split("/")
        item_code = parts[-1]
        tenor = next(key for key, value in FINALITY_CODES.items() if value == item_code)
        dates = () if tenor == self.missing_tenor else self.dates
        rows = []
        for index, source_date in enumerate(dates):
            value = "3.125"
            if self.changed_previous and index == 0 and tenor == "10Y":
                value = "3.126"
            rows.append({
                "STAT_CODE": support.FINALITY_TABLE_CODE,
                "STAT_NAME": support.FINALITY_TABLE_NAME,
                "ITEM_CODE1": item_code,
                "ITEM_NAME1": f"국고채({tenor.removesuffix('Y')}년)",
                "UNIT_NAME": support.FINALITY_UNIT_NAME,
                "TIME": source_date,
                "DATA_VALUE": value,
            })
        payload = (
            {"RESULT": {"CODE": "INFO-200"}}
            if not rows else
            {"StatisticSearch": {"list_total_count": len(rows), "row": rows}}
        )
        return response(payload)


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


def test_stranded_complete_value_run_is_finalized_without_network(tmp_path, monkeypatch):
    monkeypatch.setenv(runner.API_KEY_ENV, "literal-secret-key")
    config_path = write_config(tmp_path)
    metadata = runner.run_metadata(
        project_root=tmp_path, config_path=config_path, session=MetadataSession()
    )
    original_comparisons = runner._comparisons
    monkeypatch.setattr(
        runner, "_comparisons",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("stranded")),
    )
    with pytest.raises(RuntimeError, match="stranded"):
        runner.run_values(
            project_root=tmp_path, config_path=config_path,
            metadata_run_dir=Path(metadata["run_dir"]),
            approve_metadata_sha256=metadata["metadata_summary_sha256"],
            session=ValueSession(),
        )
    value_dir = next((tmp_path / runner.LANDING_RELATIVE).glob("values_*"))
    assert json.loads((value_dir / "checkpoint.json").read_text())["status"] == "IN_PROGRESS"
    monkeypatch.setattr(runner, "_comparisons", original_comparisons)
    finalized = runner.finalize_retained_values(
        project_root=tmp_path, config_path=config_path,
        metadata_run_dir=Path(metadata["run_dir"]),
        approve_metadata_sha256=metadata["metadata_summary_sha256"],
        run_dir=value_dir,
    )
    assert finalized["status"] == "VALUE_PILOT_COMPLETE_REVIEW_REQUIRED"
    assert finalized["raw_requests_total"] == 8
    assert finalized["raw_requests_during_finalization"] == 0
    assert finalized["observations"] == 8
    assert (value_dir / "comparison_to_toss.json").is_file()


def test_offline_value_finalizer_rejects_landing_hash_mismatch(tmp_path, monkeypatch):
    monkeypatch.setenv(runner.API_KEY_ENV, "literal-secret-key")
    config_path = write_config(tmp_path)
    metadata = runner.run_metadata(
        project_root=tmp_path, config_path=config_path, session=MetadataSession()
    )
    original_comparisons = runner._comparisons
    monkeypatch.setattr(
        runner, "_comparisons",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("stranded")),
    )
    with pytest.raises(RuntimeError, match="stranded"):
        runner.run_values(
            project_root=tmp_path, config_path=config_path,
            metadata_run_dir=Path(metadata["run_dir"]),
            approve_metadata_sha256=metadata["metadata_summary_sha256"],
            session=ValueSession(),
        )
    value_dir = next((tmp_path / runner.LANDING_RELATIVE).glob("values_*"))
    first = value_dir / "response_01_20260807_2Y.json"
    first.write_bytes(first.read_bytes() + b" ")
    monkeypatch.setattr(runner, "_comparisons", original_comparisons)
    with pytest.raises(runner.PilotStopped, match="evidence differs"):
        runner.finalize_retained_values(
            project_root=tmp_path, config_path=config_path,
            metadata_run_dir=Path(metadata["run_dir"]),
            approve_metadata_sha256=metadata["metadata_summary_sha256"],
            run_dir=value_dir,
        )


def test_finality_observation_is_six_call_landing_first_separate_marker_and_api_zero(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv(runner.API_KEY_ENV, "literal-secret-key")
    metadata_path, metadata_hash = finality_metadata_summary(tmp_path)
    session = FinalitySession(("20260825",))
    original_append = runner.Ledger.append

    def assert_landing_first(self, event, **fields):
        if event == "UI_RESPONSE":
            assert (self.path.parent / "response_00_official_ui_table_info.json").is_file()
        elif event == "VALUE_RESPONSE":
            sequence = fields["sequence"]
            assert list(self.path.parent.glob(f"response_{sequence:02d}_*.json"))
        return original_append(self, event, **fields)

    monkeypatch.setattr(runner.Ledger, "append", assert_landing_first)
    now = datetime(2026, 8, 26, 17, 10, tzinfo=ZoneInfo("Asia/Seoul"))
    result = runner.run_finality_observation(
        project_root=tmp_path,
        metadata_summary_path=metadata_path,
        approve_metadata_sha256=metadata_hash,
        range_start_date="20260813",
        observation_kst=now,
        session=session,
    )
    assert result["status"] == "FINALITY_OBSERVATION_COMPLETE"
    assert result["selected_date"] == "20260825"
    assert result["statistic_search_calls"] == len(session.get_calls) == 6
    assert result["official_ui_calls"] == len(session.post_calls) == 1
    assert not (tmp_path / "data/normalized").exists()
    run_dir = Path(result["run_dir"])
    ui_landing = run_dir / "response_00_official_ui_table_info.json"
    assert "203.0.113.44" not in ui_landing.read_text(encoding="utf-8")
    assert "<redacted>" in ui_landing.read_text(encoding="utf-8")
    state = json.loads(
        (tmp_path / runner.FINALITY_STATE_RELATIVE).read_text(encoding="utf-8")
    )
    assert state["status"] == "PUBLICATION_FINALITY_UNKNOWN"
    assert state["batches"][0]["official_ui_marker"]["provisional_marker_flag"] == "N"
    replay = runner.run_finality_observation(
        project_root=tmp_path,
        metadata_summary_path=metadata_path,
        approve_metadata_sha256=metadata_hash,
        range_start_date="20260813",
        observation_kst=now,
        session=NoCallSession(),
    )
    assert replay["status"] == "NOOP_ALREADY_SUCCEEDED"
    assert replay["statistic_search_calls"] == replay["official_ui_calls"] == 0


def test_finality_next_provider_day_compares_previous_fields_and_canonical_bytes(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv(runner.API_KEY_ENV, "literal-secret-key")
    metadata_path, metadata_hash = finality_metadata_summary(tmp_path)
    runner.run_finality_observation(
        project_root=tmp_path,
        metadata_summary_path=metadata_path,
        approve_metadata_sha256=metadata_hash,
        range_start_date="20260813",
        observation_kst=datetime(2026, 8, 26, 17, 5, tzinfo=ZoneInfo("Asia/Seoul")),
        session=FinalitySession(("20260825",)),
    )
    second = runner.run_finality_observation(
        project_root=tmp_path,
        metadata_summary_path=metadata_path,
        approve_metadata_sha256=metadata_hash,
        observation_kst=datetime(2026, 8, 27, 17, 5, tzinfo=ZoneInfo("Asia/Seoul")),
        session=FinalitySession(("20260825", "20260826")),
    )
    assert second["selected_date"] == "20260826"
    assert second["comparison_status"] == "SAME"
    state = json.loads(
        (tmp_path / runner.FINALITY_STATE_RELATIVE).read_text(encoding="utf-8")
    )
    comparison = state["batches"][1]["next_provider_day_comparison"]
    assert {row["status"] for row in comparison["tenors"].values()} == {"SAME"}
    assert all(row["canonical_row_sha256_match"] for row in comparison["tenors"].values())


def test_finality_detects_a_next_day_revision_without_inferring_finality(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv(runner.API_KEY_ENV, "literal-secret-key")
    metadata_path, metadata_hash = finality_metadata_summary(tmp_path)
    runner.run_finality_observation(
        project_root=tmp_path,
        metadata_summary_path=metadata_path,
        approve_metadata_sha256=metadata_hash,
        range_start_date="20260813",
        observation_kst=datetime(2026, 8, 26, 17, 5, tzinfo=ZoneInfo("Asia/Seoul")),
        session=FinalitySession(("20260825",)),
    )
    second = runner.run_finality_observation(
        project_root=tmp_path,
        metadata_summary_path=metadata_path,
        approve_metadata_sha256=metadata_hash,
        observation_kst=datetime(2026, 8, 27, 17, 5, tzinfo=ZoneInfo("Asia/Seoul")),
        session=FinalitySession(("20260825", "20260826"), changed_previous=True),
    )
    assert second["comparison_status"] == "CHANGED"
    state = json.loads(
        (tmp_path / runner.FINALITY_STATE_RELATIVE).read_text(encoding="utf-8")
    )
    assert state["status"] == "PUBLICATION_FINALITY_UNKNOWN"


def test_finality_partial_retry_zero_run_cannot_make_a_second_network_attempt(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv(runner.API_KEY_ENV, "literal-secret-key")
    metadata_path, metadata_hash = finality_metadata_summary(tmp_path)
    now = datetime(2026, 8, 26, 17, 5, tzinfo=ZoneInfo("Asia/Seoul"))
    with pytest.raises(support.EcosPilotError, match="valid-empty"):
        runner.run_finality_observation(
            project_root=tmp_path,
            metadata_summary_path=metadata_path,
            approve_metadata_sha256=metadata_hash,
            range_start_date="20260813",
            observation_kst=now,
            session=FinalitySession(("20260825",), missing_tenor="5Y"),
        )
    assert not (tmp_path / runner.FINALITY_STATE_RELATIVE).exists()
    with pytest.raises(runner.PilotStopped, match="incomplete Landing"):
        runner.run_finality_observation(
            project_root=tmp_path,
            metadata_summary_path=metadata_path,
            approve_metadata_sha256=metadata_hash,
            range_start_date="20260813",
            observation_kst=now,
            session=NoCallSession(),
        )


def test_finality_complete_landing_recovers_atomically_with_api_zero(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv(runner.API_KEY_ENV, "literal-secret-key")
    metadata_path, metadata_hash = finality_metadata_summary(tmp_path)
    now = datetime(2026, 8, 26, 17, 5, tzinfo=ZoneInfo("Asia/Seoul"))
    first = runner.run_finality_observation(
        project_root=tmp_path,
        metadata_summary_path=metadata_path,
        approve_metadata_sha256=metadata_hash,
        range_start_date="20260813",
        observation_kst=now,
        session=FinalitySession(("20260825",)),
    )
    state_path = tmp_path / runner.FINALITY_STATE_RELATIVE
    state_path.unlink()
    checkpoint_path = Path(first["run_dir"]) / "checkpoint.json"
    checkpoint = json.loads(checkpoint_path.read_text())
    checkpoint["status"] = "CAPTURED"
    checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")
    recovered = runner.run_finality_observation(
        project_root=tmp_path,
        metadata_summary_path=metadata_path,
        approve_metadata_sha256=metadata_hash,
        range_start_date="20260813",
        observation_kst=now,
        session=NoCallSession(),
    )
    assert recovered["status"] == "FINALITY_OBSERVATION_COMPLETE"
    assert recovered["statistic_search_calls"] == recovered["official_ui_calls"] == 0
    assert state_path.is_file()


def test_finality_state_first_interruption_reconciles_checkpoint_with_api_zero(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv(runner.API_KEY_ENV, "literal-secret-key")
    metadata_path, metadata_hash = finality_metadata_summary(tmp_path)
    now = datetime(2026, 8, 26, 17, 5, tzinfo=ZoneInfo("Asia/Seoul"))
    first = runner.run_finality_observation(
        project_root=tmp_path,
        metadata_summary_path=metadata_path,
        approve_metadata_sha256=metadata_hash,
        range_start_date="20260813",
        observation_kst=now,
        session=FinalitySession(("20260825",)),
    )
    checkpoint_path = Path(first["run_dir"]) / "checkpoint.json"
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    checkpoint["status"] = "CAPTURED"
    checkpoint.pop("selected_date")
    checkpoint.pop("state_sha256")
    checkpoint.pop("comparison_status")
    checkpoint_path.write_text(json.dumps(checkpoint), encoding="utf-8")

    replayed = runner.run_finality_observation(
        project_root=tmp_path,
        metadata_summary_path=metadata_path,
        approve_metadata_sha256=metadata_hash,
        range_start_date="20260813",
        observation_kst=now,
        session=NoCallSession(),
    )

    assert replayed["status"] == "NOOP_ALREADY_SUCCEEDED"
    assert replayed["statistic_search_calls"] == replayed["official_ui_calls"] == 0
    reconciled = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    assert reconciled["status"] == "COMPLETE"
    assert reconciled["selected_date"] == "20260825"
    assert reconciled["comparison_status"] == "PENDING_FIRST_BATCH"
    assert reconciled["state_sha256"] == hashlib.sha256(
        (tmp_path / runner.FINALITY_STATE_RELATIVE).read_bytes()
    ).hexdigest()


def test_finality_parser_rejects_duplicate_and_wrong_date_rows(tmp_path):
    metadata_path, metadata_hash = finality_metadata_summary(tmp_path)
    config = support.load_finality_config(metadata_path, approve_sha256=metadata_hash)
    scope = support.plan_finality_scopes(
        config, start_date="20260825", end_date="20260826",
    )[0]
    row = {
        "STAT_CODE": support.FINALITY_TABLE_CODE,
        "STAT_NAME": support.FINALITY_TABLE_NAME,
        "ITEM_CODE1": FINALITY_CODES["2Y"],
        "ITEM_NAME1": "국고채(2년)",
        "UNIT_NAME": support.FINALITY_UNIT_NAME,
        "TIME": "20260825",
        "DATA_VALUE": "3.125",
    }
    duplicate = {
        "StatisticSearch": {"list_total_count": 2, "row": [row, dict(row)]},
    }
    with pytest.raises(support.EcosPilotError, match="duplicate source date"):
        support.parse_finality_value(json.dumps(duplicate).encode(), config, scope)
    wrong = json.loads(json.dumps(duplicate))
    wrong["StatisticSearch"]["list_total_count"] = 1
    wrong["StatisticSearch"]["row"] = [{**row, "TIME": "20260827"}]
    with pytest.raises(support.EcosPilotError, match="outside the bounded range"):
        support.parse_finality_value(json.dumps(wrong).encode(), config, scope)
