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


def _item(*, record_date: str, amount: str = "10", extra: str = "x") -> dict[str, str]:
    return {
        "basDt": "20260808", "isinCd": "KR7000000001", "crno": "1101110000001",
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


def _landing(path: Path) -> Path:
    payload = [_page(1, 3, [_item(record_date="20241231"), _item(record_date="20251231", extra="y")]),
               _page(2, 3, [_item(record_date="20261231", amount="0", extra="z")])]
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
    assert state["row_count"] == 3 and state["declared_total_count"] == 3
    assert state["semantics"] == "retained_current_snapshot_observation_not_historical_pit"


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
