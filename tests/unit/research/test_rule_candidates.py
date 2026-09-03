from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from stock_data.research.rule_candidates import (
    RuleCandidateError,
    add_candidate,
    edit_candidate,
    load_candidates,
    remove_candidate,
    retire_candidate,
    rules_version,
    validate_candidate,
)


ROOT = Path(__file__).resolve().parents[3]


def _seed(tmp_path: Path) -> dict[str, object]:
    source = ROOT / "config/research/rule_candidates.json"
    target = tmp_path / "config/research/rule_candidates.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(source.read_bytes())
    return load_candidates(tmp_path)


def test_seed_registry_is_versioned_and_valid() -> None:
    payload = load_candidates(ROOT)
    assert payload["schema_version"] == 1
    assert 6 <= len(payload["candidates"]) <= 8
    assert payload["attempt_count"] == len(payload["history"])
    assert len(rules_version(ROOT)) == 64
    assert {item["definition"]["type"] for item in payload["candidates"]} == {
        "ladder", "vol_target", "hybrid",
    }


def test_validation_rejects_unknown_indicator_and_wrong_level_count() -> None:
    candidate = deepcopy(load_candidates(ROOT)["candidates"][0])
    candidate["definition"]["indicators"][0]["key"] = "future_return"
    with pytest.raises(RuleCandidateError, match="unsupported"):
        validate_candidate(candidate)
    candidate = deepcopy(load_candidates(ROOT)["candidates"][0])
    candidate["definition"]["levels"] = 3
    with pytest.raises(RuleCandidateError, match="indicator count"):
        validate_candidate(candidate)


def test_every_mutation_appends_history_and_bumps_attempt(tmp_path: Path) -> None:
    payload = _seed(tmp_path)
    original_attempt = payload["attempt_count"]
    candidate = deepcopy(payload["candidates"][0])
    candidate.update({
        "id": "temporary_candidate",
        "name": "temporary",
        "added_on": "2026-09-05",
        "reason": "unit test",
    })
    added = add_candidate(
        tmp_path, candidate, reason="conversation add", on="2026-09-05"
    )
    assert added["attempt_count"] == original_attempt + 1
    assert added["history"][-1] == {
        "date": "2026-09-05", "action": "add", "id": "temporary_candidate",
        "reason": "conversation add",
    }

    replacement = deepcopy(next(
        item for item in added["candidates"] if item["id"] == "temporary_candidate"
    ))
    replacement["name"] = "edited"
    edited = edit_candidate(
        tmp_path, "temporary_candidate", replacement,
        reason="conversation edit", on="2026-09-06",
    )
    assert edited["attempt_count"] == original_attempt + 2
    assert edited["history"][-1]["action"] == "edit"

    retired = retire_candidate(
        tmp_path, "temporary_candidate", reason="conversation retire", on="2026-09-07"
    )
    assert retired["attempt_count"] == original_attempt + 3
    assert next(
        item for item in retired["candidates"] if item["id"] == "temporary_candidate"
    )["status"] == "retired"

    removed = remove_candidate(
        tmp_path, "temporary_candidate", reason="conversation remove", on="2026-09-08"
    )
    assert removed["attempt_count"] == original_attempt + 4
    assert removed["history"][-1]["action"] == "remove"
    assert all(item["id"] != "temporary_candidate" for item in removed["candidates"])
    json.loads((tmp_path / "config/research/rule_candidates.json").read_text("utf-8"))
