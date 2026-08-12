import json
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from stock_data.contracts.dividend_observation import KR_EQUITY_DIVIDEND_SOURCE_OBSERVATION
from stock_data.contracts.registry import CONTRACTS
from stock_data.providers.data_go_kr.dividend_observation import (
    DividendObservationError,
    build_dividend_observation,
    load_dividend_observation,
)
import stock_data.providers.data_go_kr.dividend_observation as observation_module
from stock_data.storage.contract_parquet import read_dataset
from stock_data.validation.data_v1 import validate_data_v1


def _item(
    *, record_date: str, amount: str = "10", extra: str = "x",
    snapshot_date: str = "20260808",
) -> dict[str, str]:
    return {
        "basDt": snapshot_date, "isinCd": "KR7000000001", "crno": "1101110000001",
        "stckIssuCmpyNm": "issuer", "scrsItmsKcdNm": "common", "stckDvdnRcdNm": "cash",
        "dvdnBasDt": record_date, "cashDvdnPayDt": "20260901", "stckHndvDt": "",
        "stckGenrDvdnAmt": amount, "stckGenrCashDvdnRt": "1", "stckGenrDvdnRt": "0",
        "stckGrdnDvdnAmt": "0", "cashGrdnDvdnRt": "0", "stckGrdnDvdnRt": "0",
        "stckParPrc": "500", "unmapped_source_field": extra,
    }


def _page(page_no: int, total: int, items: list[dict[str, str]]) -> dict:
    return {"response": {"header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE."}, "body": {
        "items": {"item": items}, "numOfRows": 2, "pageNo": page_no, "totalCount": total,
    }}}


def _landing(path: Path, *, snapshot_date: str = "20260808", amount: str = "10") -> Path:
    payload = [_page(1, 3, [
        _item(record_date="20241231", amount=amount, snapshot_date=snapshot_date),
        _item(record_date="20251231", extra="y", snapshot_date=snapshot_date),
    ]), _page(2, 3, [
        _item(record_date="20261231", amount="0", extra="z", snapshot_date=snapshot_date),
    ])]
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return path


def test_observation_is_provenance_keyed_and_exactly_rebuildable(tmp_path: Path):
    landing = _landing(tmp_path / "landing.json")
    result = build_dividend_observation(
        landing_path=landing, output_root=tmp_path / "normalized", state_path=tmp_path / "state.json",
    )
    assert result.row_count == 3 and result.response_count == 2
    assert result.source_snapshot_date == "2026-08-08"
    validator = lambda frame: validate_data_v1(frame, KR_EQUITY_DIVIDEND_SOURCE_OBSERVATION, allow_empty=False)
    restored = read_dataset(result.output_root, KR_EQUITY_DIVIDEND_SOURCE_OBSERVATION, validator)
    assert len(restored) == 3
    assert restored["source_item_ordinal"].tolist() == [0, 1, 2]
    assert restored["source_page_no"].tolist() == [1, 1, 2]
    assert restored["source_page_item_ordinal"].tolist() == [0, 1, 0]
    assert restored["landing_file_sha256"].nunique() == 1
    assert restored["source_record_canonical_sha256"].nunique() == 3
    assert restored["ordinary_dividend_amount"].tolist() == [10.0, 10.0, 0.0]
    state = json.loads(result.state_path.read_text(encoding="utf-8"))
    assert state["state_version"] == 2
    assert state["row_count"] == 3 and state["snapshot_count"] == 1
    assert state["snapshots"][0]["declared_total_count"] == 3
    assert state["semantics"] == "append_only_source_observations_not_historical_pit"


def test_observation_contract_is_registered():
    assert CONTRACTS[KR_EQUITY_DIVIDEND_SOURCE_OBSERVATION.name] is KR_EQUITY_DIVIDEND_SOURCE_OBSERVATION


@pytest.mark.parametrize("mutator, message", [
    (lambda value: value.__setitem__(1, _page(3, 3, [_item(record_date="20261231")])), "page numbers"),
    (lambda value: value[0]["response"]["body"].__setitem__("totalCount", 4), "totalCount"),
    (lambda value: value[0]["response"]["body"].__setitem__("numOfRows", 1), "exceeds"),
])
def test_observation_rejects_unverifiable_landing(tmp_path: Path, mutator, message: str):
    landing = _landing(tmp_path / "landing.json")
    payload = json.loads(landing.read_text(encoding="utf-8"))
    mutator(payload)
    landing.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(DividendObservationError, match=message):
        load_dividend_observation(landing)


def test_record_hash_covers_retained_unmapped_source_fields(tmp_path: Path):
    first = _landing(tmp_path / "first.json")
    second = _landing(tmp_path / "second.json")
    payload = json.loads(second.read_text(encoding="utf-8"))
    payload[0]["response"]["body"]["items"]["item"][0]["unmapped_source_field"] = "corrected"
    second.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    first_frame, _ = load_dividend_observation(first)
    second_frame, _ = load_dividend_observation(second)
    assert first_frame.loc[0, "landing_file_sha256"] != second_frame.loc[0, "landing_file_sha256"]
    assert first_frame.loc[0, "source_record_canonical_sha256"] != second_frame.loc[0, "source_record_canonical_sha256"]
    assert first_frame.loc[0, "ordinary_dividend_amount"] == second_frame.loc[0, "ordinary_dividend_amount"]


def test_observation_appends_two_snapshots_and_same_snapshot_is_idempotent(tmp_path: Path):
    first = _landing(tmp_path / "first.json")
    second = _landing(tmp_path / "second.json", snapshot_date="20260809", amount="11")
    output_root = tmp_path / "normalized"
    state_path = tmp_path / "state.json"
    first_result = build_dividend_observation(
        landing_path=first, output_root=output_root, state_path=state_path,
    )
    build_dividend_observation(
        landing_path=second, output_root=output_root, state_path=state_path,
    )
    validator = lambda value: validate_data_v1(
        value, KR_EQUITY_DIVIDEND_SOURCE_OBSERVATION, allow_empty=False
    )
    combined = read_dataset(first_result.output_root, KR_EQUITY_DIVIDEND_SOURCE_OBSERVATION, validator)
    assert len(combined) == 6
    assert combined["landing_file_sha256"].nunique() == 2
    assert combined["source_snapshot_date"].astype(str).tolist() == [
        "2026-08-08", "2026-08-08", "2026-08-08",
        "2026-08-09", "2026-08-09", "2026-08-09",
    ]
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["snapshot_count"] == 2 and state["row_count"] == 6
    assert [value["row_count"] for value in state["snapshots"]] == [3, 3]

    parquet_before = {
        path.relative_to(first_result.output_root): path.read_bytes()
        for path in first_result.output_root.rglob("data.parquet")
    }
    state_before = state_path.read_bytes()
    build_dividend_observation(
        landing_path=second, output_root=output_root, state_path=state_path,
    )
    assert state_path.read_bytes() == state_before
    assert {
        path.relative_to(first_result.output_root): path.read_bytes()
        for path in first_result.output_root.rglob("data.parquet")
    } == parquet_before


def test_same_snapshot_hash_with_different_content_fails_closed(
    tmp_path: Path, monkeypatch,
):
    landing = _landing(tmp_path / "landing.json")
    output_root = tmp_path / "normalized"
    state_path = tmp_path / "state.json"
    result = build_dividend_observation(
        landing_path=landing, output_root=output_root, state_path=state_path,
    )
    frame, metadata = load_dividend_observation(landing)
    frame.loc[0, "ordinary_dividend_amount"] = 999.0
    monkeypatch.setattr(observation_module, "load_dividend_observation", lambda unused: (frame, metadata))
    before_state = state_path.read_bytes()
    before_data = {
        path.relative_to(result.output_root): path.read_bytes()
        for path in result.output_root.rglob("data.parquet")
    }
    with pytest.raises(DividendObservationError, match="metadata differs|different normalized content"):
        build_dividend_observation(
            landing_path=landing, output_root=output_root, state_path=state_path,
        )
    assert state_path.read_bytes() == before_state
    assert {
        path.relative_to(result.output_root): path.read_bytes()
        for path in result.output_root.rglob("data.parquet")
    } == before_data


def test_append_staging_failure_preserves_existing_artifact(tmp_path: Path, monkeypatch):
    first = _landing(tmp_path / "first.json")
    second = _landing(tmp_path / "second.json", snapshot_date="20260809")
    output_root = tmp_path / "normalized"
    state_path = tmp_path / "state.json"
    result = build_dividend_observation(
        landing_path=first, output_root=output_root, state_path=state_path,
    )
    before_state = state_path.read_bytes()
    before_data = {
        path.relative_to(result.output_root): path.read_bytes()
        for path in result.output_root.rglob("data.parquet")
    }
    monkeypatch.setattr(
        observation_module, "write_dataset_atomic",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("injected staging failure")),
    )
    with pytest.raises(RuntimeError, match="injected staging failure"):
        build_dividend_observation(
            landing_path=second, output_root=output_root, state_path=state_path,
        )
    assert state_path.read_bytes() == before_state
    assert {
        path.relative_to(result.output_root): path.read_bytes()
        for path in result.output_root.rglob("data.parquet")
    } == before_data


def test_append_commit_failure_rolls_back_dataset_and_state(tmp_path: Path, monkeypatch):
    first = _landing(tmp_path / "first.json")
    second = _landing(tmp_path / "second.json", snapshot_date="20260809")
    output_root = tmp_path / "normalized"
    state_path = tmp_path / "state.json"
    result = build_dividend_observation(
        landing_path=first, output_root=output_root, state_path=state_path,
    )
    before_state = state_path.read_bytes()
    before_data = {
        path.relative_to(result.output_root): path.read_bytes()
        for path in result.output_root.rglob("data.parquet")
    }
    original_replace = Path.replace

    def fail_installing_staged_state(path: Path, target: Path):
        target = Path(target)
        if (
            path.parent == state_path.parent
            and ".dividend-append.stage." in path.name
            and target == state_path
        ):
            raise OSError("injected state commit failure")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_installing_staged_state)
    with pytest.raises(OSError, match="injected state commit failure"):
        build_dividend_observation(
            landing_path=second, output_root=output_root, state_path=state_path,
        )
    assert state_path.read_bytes() == before_state
    assert {
        path.relative_to(result.output_root): path.read_bytes()
        for path in result.output_root.rglob("data.parquet")
    } == before_data


def test_verified_legacy_single_snapshot_state_upgrades_without_row_loss(tmp_path: Path):
    landing = _landing(tmp_path / "landing.json")
    output_root = tmp_path / "normalized"
    state_path = tmp_path / "state.json"
    result = build_dividend_observation(
        landing_path=landing, output_root=output_root, state_path=state_path,
    )
    current = json.loads(state_path.read_text(encoding="utf-8"))
    snapshot = current["snapshots"][0]
    legacy = {
        "dataset": current["dataset"],
        "version": current["version"],
        "status": "ARTIFACT_COMPLETE",
        "semantics": "retained_current_snapshot_observation_not_historical_pit",
        **{key: snapshot[key] for key in (
            "landing_file_sha256", "source_snapshot_date", "response_count",
            "declared_total_count", "page_hashes", "row_count",
        )},
    }
    state_path.write_text(json.dumps(legacy), encoding="utf-8")
    build_dividend_observation(
        landing_path=landing, output_root=output_root, state_path=state_path,
    )
    upgraded = json.loads(state_path.read_text(encoding="utf-8"))
    assert upgraded["state_version"] == 2
    assert upgraded["snapshot_count"] == 1 and upgraded["row_count"] == 3
    validator = lambda value: validate_data_v1(
        value, KR_EQUITY_DIVIDEND_SOURCE_OBSERVATION, allow_empty=False
    )
    restored = read_dataset(result.output_root, KR_EQUITY_DIVIDEND_SOURCE_OBSERVATION, validator)
    assert len(restored) == 3


def test_existing_v2_state_detects_normalized_artifact_tampering(tmp_path: Path):
    landing = _landing(tmp_path / "landing.json")
    output_root = tmp_path / "normalized"
    state_path = tmp_path / "state.json"
    result = build_dividend_observation(
        landing_path=landing, output_root=output_root, state_path=state_path,
    )
    parquet = next(result.output_root.rglob("data.parquet"))
    tampered = pd.read_parquet(parquet)
    tampered.loc[0, "ordinary_dividend_amount"] = 999.0
    tampered.to_parquet(parquet, index=False)
    state_before = state_path.read_bytes()
    data_before = parquet.read_bytes()
    with pytest.raises(DividendObservationError, match="normalized hash"):
        build_dividend_observation(
            landing_path=landing, output_root=output_root, state_path=state_path,
        )
    assert state_path.read_bytes() == state_before
    assert parquet.read_bytes() == data_before


@pytest.mark.parametrize("rename_boundary", range(1, 7))
def test_startup_recovery_models_hard_crash_after_each_rename_boundary(
    tmp_path: Path, monkeypatch, rename_boundary: int,
):
    first = _landing(tmp_path / "first.json")
    second = _landing(tmp_path / "second.json", snapshot_date="20260809")
    output_root = tmp_path / "normalized"
    state_path = tmp_path / "state.json"
    result = build_dividend_observation(
        landing_path=first, output_root=output_root, state_path=state_path,
    )
    old_hash = observation_module._dataset_sha256(
        result.output_root,
        lambda value: validate_data_v1(
            value, KR_EQUITY_DIVIDEND_SOURCE_OBSERVATION, allow_empty=False
        ),
    )
    original_replace = Path.replace
    original_recover = observation_module.recover_dividend_observation_transaction
    counter = {"value": 0}

    class HardCrash(BaseException):
        pass

    def crash_after_boundary(path: Path, target: Path):
        target = Path(target)
        result_path = original_replace(path, target)
        if ".dividend-append." in path.name or ".dividend-append." in target.name:
            counter["value"] += 1
            if counter["value"] == rename_boundary:
                raise HardCrash(f"hard crash boundary {rename_boundary}")
        return result_path

    monkeypatch.setattr(Path, "replace", crash_after_boundary)
    recovery_calls = {"value": 0}

    def terminate_in_exception_recovery(**kwargs):
        recovery_calls["value"] += 1
        if recovery_calls["value"] == 1:
            return original_recover(**kwargs)
        raise HardCrash("process terminated")

    monkeypatch.setattr(
        observation_module, "recover_dividend_observation_transaction",
        terminate_in_exception_recovery,
    )
    with pytest.raises(DividendObservationError, match="durable transaction recovery"):
        build_dividend_observation(
            landing_path=second, output_root=output_root, state_path=state_path,
        )
    assert counter["value"] == rename_boundary

    monkeypatch.setattr(Path, "replace", original_replace)
    monkeypatch.setattr(
        observation_module, "recover_dividend_observation_transaction", original_recover
    )
    validator = lambda value: validate_data_v1(
        value, KR_EQUITY_DIVIDEND_SOURCE_OBSERVATION, allow_empty=False
    )
    action = original_recover(
        dataset_root=result.output_root, state_path=state_path, validator=validator
    )
    restored = read_dataset(result.output_root, KR_EQUITY_DIVIDEND_SOURCE_OBSERVATION, validator)
    if rename_boundary <= 4:
        assert action == "RESTORED_ORIGINAL_ARTIFACT"
        assert len(restored) == 3
        assert observation_module._frame_sha256(restored) == old_hash
    else:
        assert action == "FINALIZED_NEW_ARTIFACT"
        assert len(restored) == 6
    assert not observation_module._transaction_marker(result.output_root).exists()
    assert observation_module._transaction_orphans(result.output_root, state_path) == set()


@pytest.mark.parametrize("interrupt", [KeyboardInterrupt, SystemExit])
def test_base_exception_during_promotion_recovers_before_propagating(
    tmp_path: Path, monkeypatch, interrupt,
):
    first = _landing(tmp_path / "first.json")
    second = _landing(tmp_path / "second.json", snapshot_date="20260809")
    output_root = tmp_path / "normalized"
    state_path = tmp_path / "state.json"
    result = build_dividend_observation(
        landing_path=first, output_root=output_root, state_path=state_path,
    )
    before_state = state_path.read_bytes()
    before_data = {
        path.relative_to(result.output_root): path.read_bytes()
        for path in result.output_root.rglob("data.parquet")
    }
    original_replace = Path.replace

    def interrupt_state_promotion(path: Path, target: Path):
        if ".dividend-append.stage." in path.name and Path(target) == state_path:
            raise interrupt("injected interruption")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", interrupt_state_promotion)
    with pytest.raises(interrupt):
        build_dividend_observation(
            landing_path=second, output_root=output_root, state_path=state_path,
        )
    assert state_path.read_bytes() == before_state
    assert {
        path.relative_to(result.output_root): path.read_bytes()
        for path in result.output_root.rglob("data.parquet")
    } == before_data
    assert observation_module._transaction_orphans(result.output_root, state_path) == set()


def test_cleanup_interruption_is_finalized_from_journal(tmp_path: Path, monkeypatch):
    first = _landing(tmp_path / "first.json")
    second = _landing(tmp_path / "second.json", snapshot_date="20260809")
    output_root = tmp_path / "normalized"
    state_path = tmp_path / "state.json"
    result = build_dividend_observation(
        landing_path=first, output_root=output_root, state_path=state_path,
    )
    original_remove = observation_module._remove_path
    original_recover = observation_module.recover_dividend_observation_transaction
    calls = {"value": 0}

    class HardCrash(BaseException):
        pass

    def crash_after_first_cleanup(path: Path):
        original_remove(path)
        calls["value"] += 1
        if calls["value"] == 1:
            raise HardCrash("cleanup power loss")

    monkeypatch.setattr(observation_module, "_remove_path", crash_after_first_cleanup)
    recovery_calls = {"value": 0}

    def terminate_in_exception_recovery(**kwargs):
        recovery_calls["value"] += 1
        if recovery_calls["value"] == 1:
            return original_recover(**kwargs)
        raise HardCrash("process terminated")

    monkeypatch.setattr(
        observation_module, "recover_dividend_observation_transaction",
        terminate_in_exception_recovery,
    )
    with pytest.raises(DividendObservationError, match="durable transaction recovery"):
        build_dividend_observation(
            landing_path=second, output_root=output_root, state_path=state_path,
        )
    monkeypatch.setattr(observation_module, "_remove_path", original_remove)
    monkeypatch.setattr(
        observation_module, "recover_dividend_observation_transaction", original_recover
    )
    validator = lambda value: validate_data_v1(
        value, KR_EQUITY_DIVIDEND_SOURCE_OBSERVATION, allow_empty=False
    )
    assert original_recover(
        dataset_root=result.output_root, state_path=state_path, validator=validator
    ) == "FINALIZED_NEW_ARTIFACT"
    assert len(read_dataset(
        result.output_root, KR_EQUITY_DIVIDEND_SOURCE_OBSERVATION, validator
    )) == 6
    assert observation_module._transaction_orphans(result.output_root, state_path) == set()


def test_rollback_failure_retains_journal_for_later_recovery(tmp_path: Path, monkeypatch):
    first = _landing(tmp_path / "first.json")
    second = _landing(tmp_path / "second.json", snapshot_date="20260809")
    output_root = tmp_path / "normalized"
    state_path = tmp_path / "state.json"
    result = build_dividend_observation(
        landing_path=first, output_root=output_root, state_path=state_path,
    )
    original_replace = Path.replace

    def fail_promotion_and_rollback(path: Path, target: Path):
        target = Path(target)
        if ".dividend-append.stage." in path.name and target == state_path:
            raise OSError("promotion failure")
        if ".dividend-append.backup." in path.name and target == result.output_root:
            raise OSError("rollback failure")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_promotion_and_rollback)
    with pytest.raises(DividendObservationError, match="recovery did not complete"):
        build_dividend_observation(
            landing_path=second, output_root=output_root, state_path=state_path,
        )
    assert observation_module._transaction_marker(result.output_root).is_file()
    monkeypatch.setattr(Path, "replace", original_replace)
    validator = lambda value: validate_data_v1(
        value, KR_EQUITY_DIVIDEND_SOURCE_OBSERVATION, allow_empty=False
    )
    assert observation_module.recover_dividend_observation_transaction(
        dataset_root=result.output_root, state_path=state_path, validator=validator
    ) == "RESTORED_ORIGINAL_ARTIFACT"
    assert len(read_dataset(
        result.output_root, KR_EQUITY_DIVIDEND_SOURCE_OBSERVATION, validator
    )) == 3


def test_orphan_transaction_path_without_marker_is_refused(tmp_path: Path):
    output_root = tmp_path / "normalized"
    dataset_root = output_root / KR_EQUITY_DIVIDEND_SOURCE_OBSERVATION.name
    state_path = tmp_path / "state.json"
    output_root.mkdir()
    orphan = output_root / (
        f".{KR_EQUITY_DIVIDEND_SOURCE_OBSERVATION.name}.dividend-append.stage."
        + "a" * 32
    )
    orphan.mkdir()
    validator = lambda value: validate_data_v1(
        value, KR_EQUITY_DIVIDEND_SOURCE_OBSERVATION, allow_empty=False
    )
    with pytest.raises(DividendObservationError, match="orphan"):
        observation_module.recover_dividend_observation_transaction(
            dataset_root=dataset_root, state_path=state_path, validator=validator
        )


def test_multiple_temporary_transaction_markers_are_refused(tmp_path: Path):
    output_root = tmp_path / "normalized"
    dataset_root = output_root / KR_EQUITY_DIVIDEND_SOURCE_OBSERVATION.name
    state_path = tmp_path / "state.json"
    output_root.mkdir()
    marker = observation_module._transaction_marker(dataset_root)
    for suffix in ("a", "b"):
        marker.with_name(f"{marker.name}.{suffix * 32}.tmp").write_text("{}", encoding="utf-8")
    validator = lambda value: validate_data_v1(
        value, KR_EQUITY_DIVIDEND_SOURCE_OBSERVATION, allow_empty=False
    )
    with pytest.raises(DividendObservationError, match="multiple orphan"):
        observation_module.recover_dividend_observation_transaction(
            dataset_root=dataset_root, state_path=state_path, validator=validator
        )


def test_manual_entrypoint_import_is_side_effect_free_and_explicit_call_builds(
    tmp_path: Path, monkeypatch, capsys,
):
    script_path = Path(__file__).parents[1] / "scripts" / "manual" / "build_dividend_observation.py"
    calls = []

    def fake_builder(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            landing_file_sha256="a" * 64,
            source_snapshot_date="2026-08-08",
            response_count=2,
            row_count=3,
            output_root=kwargs["output_root"],
            state_path=kwargs["state_path"],
        )

    monkeypatch.setattr(observation_module, "build_dividend_observation", fake_builder)
    spec = importlib.util.spec_from_file_location("build_dividend_observation_test", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert calls == []

    landing = _landing(tmp_path / "landing.json")
    output_root = tmp_path / "normalized"
    state_path = tmp_path / "state.json"
    assert module.main([
        "--landing-path", str(landing), "--output-root", str(output_root),
        "--state-path", str(state_path),
    ]) == 0
    assert calls == [{
        "landing_path": landing, "output_root": output_root, "state_path": state_path,
    }]
    assert json.loads(capsys.readouterr().out)["row_count"] == 3

    module.build_dividend_observation = build_dividend_observation
    assert module.main([
        "--landing-path", str(landing), "--output-root", str(output_root),
        "--state-path", str(state_path),
    ]) == 0
    assert (output_root / KR_EQUITY_DIVIDEND_SOURCE_OBSERVATION.name / "year=2026" / "data.parquet").is_file()
    assert state_path.is_file()
