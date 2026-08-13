from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
from uuid import uuid4

import pandas as pd
import pyarrow.parquet as pq

from stock_data.contracts.data_v1 import KR_EQUITY_RIGHTS_SCHEDULE
from stock_data.providers.data_go_kr.data_v1 import normalize_rights
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic
from stock_data.storage.contract_arrow import contract_arrow_schema
from stock_data.validation.data_v1 import validate_data_v1


DATASET = KR_EQUITY_RIGHTS_SCHEDULE.name
STATUS = "PARTIAL_DIAGNOSTIC_SOURCE_OBSERVATION"
STATE_VERSION = 1
DIAGNOSTIC_SPECS = {
    "B002-P1": {
        "source_classification": "SOURCE_USABLE",
        "requested_rows": 1,
        "returned_rows": 1,
        "declared_total": 12,
    },
    "B002-P3": {
        "source_classification": "SOURCE_SNAPSHOT_COMPLETE",
        "requested_rows": 12,
        "returned_rows": 12,
        "declared_total": 12,
    },
}


class RightsObservationError(RuntimeError):
    pass


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise RightsObservationError(f"invalid JSON: {path}") from error
    if not isinstance(value, dict):
        raise RightsObservationError(f"JSON root must be an object: {path}")
    return value


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(
                json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
            )
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _under(root: Path, path: Path) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as error:
        raise RightsObservationError(f"path escapes project root: {path}") from error
    return resolved


def _canonical_frame_sha256(frame: pd.DataFrame) -> str:
    ordered = frame.sort_values(
        list(KR_EQUITY_RIGHTS_SCHEDULE.sort_key), kind="stable"
    ).reset_index(drop=True)
    values = ordered.astype(object).where(ordered.notna(), None)
    payload = values.to_json(
        orient="records", date_format="iso", force_ascii=False
    ).encode("utf-8")
    return _sha256_bytes(payload)


def _tree_manifest(root: Path, logical_root: Path) -> list[dict[str, object]]:
    files = sorted(root.glob("year=*/data.parquet"))
    if not files or files != sorted(root.rglob("*.parquet")):
        raise RightsObservationError("dataset must contain only year=<YYYY>/data.parquet")
    return [
        {
            "path": (logical_root / path.relative_to(root)).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in files
    ]


def _verify_physical_dataset(root: Path) -> None:
    expected = contract_arrow_schema(KR_EQUITY_RIGHTS_SCHEDULE)
    for path in sorted(root.glob("year=*/data.parquet")):
        try:
            year = int(path.parent.name.removeprefix("year="))
        except ValueError as error:
            raise RightsObservationError("invalid dataset year partition") from error
        if not pq.ParquetFile(path).schema_arrow.equals(expected, check_metadata=False):
            raise RightsObservationError("dataset physical schema differs from contract")
        dates = pd.to_datetime(pq.read_table(path, columns=["source_snapshot_date"]).to_pandas()[
            "source_snapshot_date"
        ], errors="raise")
        if not dates.dt.year.eq(year).all():
            raise RightsObservationError("dataset row differs from year partition")


def _manifest_sha256(manifest: list[dict[str, object]]) -> str:
    return _sha256_bytes(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    )


def _extract_diagnostic(
    *, project_root: Path, diagnostic_root: Path
) -> tuple[pd.DataFrame, dict[str, object]]:
    diagnostic_root = _under(project_root, diagnostic_root)
    envelope_path = diagnostic_root / "response_envelope.json"
    ledger_path = diagnostic_root / "call_ledger.redacted.json"
    handoff_path = diagnostic_root / "handoff_manifest.json"
    if set(path.name for path in diagnostic_root.iterdir() if path.is_file()) != {
        envelope_path.name, ledger_path.name, handoff_path.name,
    }:
        raise RightsObservationError("diagnostic directory file set differs")

    handoff = _json(handoff_path)
    envelope = _json(envelope_path)
    ledger = _json(ledger_path)
    run_id = handoff.get("run_id")
    task_id = handoff.get("task_id")
    spec = DIAGNOSTIC_SPECS.get(str(task_id))
    if (
        not isinstance(run_id, str)
        or not run_id
        or spec is None
        or handoff.get("classification") != spec["source_classification"]
        or handoff.get("request_count") != 1
        or handoff.get("retries") != 0
        or handoff.get("envelope_sha256") != _sha256(envelope_path)
        or handoff.get("ledger_sha256") != _sha256(ledger_path)
    ):
        raise RightsObservationError("handoff manifest authenticity gate failed")
    for field, expected in (
        ("envelope_path", envelope_path), ("ledger_path", ledger_path),
    ):
        value = handoff.get(field)
        if not isinstance(value, str) or _under(project_root, project_root / value) != expected:
            raise RightsObservationError(f"handoff {field} differs")
    if task_id == "B002-P3":
        checkpoint_value = handoff.get("checkpoint_path")
        if not isinstance(checkpoint_value, str):
            raise RightsObservationError("completion checkpoint path is missing")
        checkpoint_path = _under(project_root, project_root / checkpoint_value)
        checkpoint = _json(checkpoint_path)
        if (
            handoff.get("checkpoint_sha256") != _sha256(checkpoint_path)
            or checkpoint.get("task_id") != task_id
            or checkpoint.get("run_id") != run_id
            or checkpoint.get("status") != spec["source_classification"]
            or checkpoint.get("request_count") != 1
            or checkpoint.get("retry_count") != 0
        ):
            raise RightsObservationError("completion checkpoint authenticity gate failed")

    encoded = envelope.get("response_body_base64")
    if envelope.get("response_body_encoding") != "base64" or not isinstance(encoded, str):
        raise RightsObservationError("response envelope encoding differs")
    try:
        body_bytes = base64.b64decode(encoded, validate=True)
    except ValueError as error:
        raise RightsObservationError("response body base64 is invalid") from error
    body_sha = _sha256_bytes(body_bytes)
    if (
        envelope.get("task_id") != task_id
        or envelope.get("run_id") != run_id
        or envelope.get("http_status") != 200
        or envelope.get("response_body_bytes") != len(body_bytes)
        or envelope.get("response_body_sha256") != body_sha
        or handoff.get("response_body_sha256") != body_sha
    ):
        raise RightsObservationError("response envelope authenticity gate failed")

    request = ledger.get("request")
    if (
        ledger.get("task_id") != task_id
        or ledger.get("run_id") != run_id
        or ledger.get("classification") != spec["source_classification"]
        or ledger.get("authorized_operation")
        != "GetStocRighScheService_V2/getRighExerReasSche_V2"
        or ledger.get("http_status") != 200
        or ledger.get("request_count") != 1
        or ledger.get("retries") != 0
        or ledger.get("response_body_bytes") != len(body_bytes)
        or ledger.get("response_body_sha256") != body_sha
        or ledger.get("service_key_or_prepared_query_stored") is not False
        or ledger.get("json_parseable") is not True
        or ledger.get("result_code") != "00"
        or ledger.get("transport_error_type") is not None
        or not isinstance(request, dict)
        or set(request) != {
            "basDt", "issuCmpyKsdCustNo", "numOfRows", "pageNo", "resultType"
        }
        or request.get("pageNo") != 1
        or request.get("numOfRows") != spec["requested_rows"]
        or request.get("resultType") != "json"
    ):
        raise RightsObservationError("call ledger authenticity gate failed")
    endpoint = ledger.get("endpoint")
    if not isinstance(endpoint, str) or "?" in endpoint or "#" in endpoint:
        raise RightsObservationError("call ledger endpoint is not safely redacted")

    try:
        payload = json.loads(body_bytes)
        response = payload["response"]
        body = response["body"]
        items = body["items"]["item"]
    except (UnicodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise RightsObservationError("response body shape differs") from error
    if (
        response.get("header", {}).get("resultCode") != "00"
        or body.get("pageNo") != 1
        or body.get("numOfRows") != spec["requested_rows"]
        or body.get("totalCount") != spec["declared_total"]
        or not isinstance(items, list)
        or len(items) != spec["returned_rows"]
        or ledger.get("returned_item_count") != spec["returned_rows"]
        or ledger.get("total_count") != spec["declared_total"]
        or any(str(request.get("basDt")) != str(item.get("basDt")) for item in items)
        or any(
            str(request.get("issuCmpyKsdCustNo"))
            != str(item.get("issuCmpyKsdCustNo")) for item in items
        )
    ):
        raise RightsObservationError("diagnostic response/request semantics differ")

    frame = normalize_rights(
        items, landing_response_body_sha256=body_sha, source_page_no=1
    )
    metadata = {
        "run_id": run_id,
        "task_id": task_id,
        "classification": STATUS,
        "historical_completeness": False,
        "canonical_economic_event_identity": False,
        "api_calls_for_promotion": 0,
        "source_snapshot_date": str(frame["source_snapshot_date"].iloc[0]),
        "declared_total_count": spec["declared_total"],
        "returned_item_count": spec["returned_rows"],
        "source_page_no": 1,
        "response_body_sha256": body_sha,
        "response_envelope_sha256": _sha256(envelope_path),
        "call_ledger_sha256": _sha256(ledger_path),
        "handoff_manifest_sha256": _sha256(handoff_path),
        "normalized_rows_canonical_sha256": _canonical_frame_sha256(frame),
    }
    return frame, metadata


def _validator(frame: pd.DataFrame) -> None:
    validate_data_v1(frame, KR_EQUITY_RIGHTS_SCHEDULE, allow_empty=False)


def _load_existing(
    dataset_root: Path, state_path: Path, logical_root: Path
) -> tuple[pd.DataFrame | None, list[dict[str, object]]]:
    if dataset_root.exists() != state_path.exists():
        raise RightsObservationError("existing dataset/state pair is incomplete")
    if not dataset_root.exists():
        return None, []
    _tree_manifest(dataset_root, logical_root)
    _verify_physical_dataset(dataset_root)
    existing = read_dataset(dataset_root, KR_EQUITY_RIGHTS_SCHEDULE, _validator)
    state = _json(state_path)
    snapshots = state.get("snapshots")
    manifest = _tree_manifest(dataset_root, logical_root)
    if (
        set(state) != {
            "dataset", "contract_version", "state_version", "status", "semantics",
            "historical_completeness", "canonical_economic_event_identity", "api_calls",
            "snapshot_count", "row_count", "snapshots", "output_manifest",
            "output_manifest_sha256",
        }
        or
        state.get("dataset") != DATASET
        or state.get("contract_version") != KR_EQUITY_RIGHTS_SCHEDULE.version
        or state.get("state_version") != STATE_VERSION
        or state.get("status") != STATUS
        or state.get("semantics")
        != "append_only_source_observations_not_canonical_events"
        or state.get("historical_completeness") is not False
        or state.get("canonical_economic_event_identity") is not False
        or state.get("api_calls") != 0
        or state.get("row_count") != len(existing)
        or state.get("output_manifest") != manifest
        or state.get("output_manifest_sha256") != _manifest_sha256(manifest)
        or not isinstance(snapshots, list)
        or len(snapshots) != state.get("snapshot_count")
    ):
        raise RightsObservationError("existing state does not describe current artifact")
    snapshot_fields = {
        "run_id", "task_id", "classification", "historical_completeness",
        "canonical_economic_event_identity", "api_calls_for_promotion",
        "source_snapshot_date", "declared_total_count", "returned_item_count",
        "source_page_no", "response_body_sha256", "response_envelope_sha256",
        "call_ledger_sha256", "handoff_manifest_sha256",
        "normalized_rows_canonical_sha256",
    }
    for snapshot in snapshots:
        hashes = (
            snapshot.get("response_body_sha256"),
            snapshot.get("response_envelope_sha256"),
            snapshot.get("call_ledger_sha256"),
            snapshot.get("handoff_manifest_sha256"),
            snapshot.get("normalized_rows_canonical_sha256"),
        ) if isinstance(snapshot, dict) else ()
        if (
            not isinstance(snapshot, dict)
            or set(snapshot) != snapshot_fields
            or not isinstance(snapshot.get("task_id"), str)
            or snapshot.get("task_id") not in DIAGNOSTIC_SPECS
            or snapshot.get("classification") != STATUS
            or snapshot.get("historical_completeness") is not False
            or snapshot.get("canonical_economic_event_identity") is not False
            or snapshot.get("api_calls_for_promotion") != 0
            or snapshot.get("declared_total_count")
            != DIAGNOSTIC_SPECS[str(snapshot.get("task_id"))]["declared_total"]
            or snapshot.get("returned_item_count")
            != DIAGNOSTIC_SPECS[str(snapshot.get("task_id"))]["returned_rows"]
            or snapshot.get("source_page_no") != 1
            or any(not isinstance(value, str) or len(value) != 64
                   or any(character not in "0123456789abcdef" for character in value)
                   for value in hashes)
        ):
            raise RightsObservationError("existing snapshot state is invalid")
    identities = {str(value) for value in existing["landing_response_body_sha256"].unique()}
    state_identities = {
        str(value.get("response_body_sha256")) for value in snapshots if isinstance(value, dict)
    }
    if identities != state_identities or len(state_identities) != len(snapshots):
        raise RightsObservationError("existing state snapshot identities differ")
    for snapshot in snapshots:
        identity = str(snapshot["response_body_sha256"])
        group = existing.loc[existing["landing_response_body_sha256"].eq(identity)]
        if (
            snapshot.get("returned_item_count") != len(group)
            or snapshot.get("normalized_rows_canonical_sha256")
            != _canonical_frame_sha256(group)
        ):
            raise RightsObservationError("existing snapshot state differs from rows")
    return existing, list(snapshots)


def _tree_hash(root: Path, logical_root: Path) -> str:
    return _manifest_sha256(_tree_manifest(root, logical_root))


def _recover(
    *, project_root: Path, dataset_root: Path, state_path: Path, logical_root: Path
) -> str:
    marker = dataset_root.parent / f".{DATASET}.rights-observation.transaction.json"
    if not marker.exists():
        orphans = list(dataset_root.parent.glob(f".{DATASET}.rights-observation.*.*"))
        orphans += list(state_path.parent.glob(f".{state_path.name}.*.*"))
        if orphans:
            raise RightsObservationError("orphan rights-observation transaction paths exist")
        return "NONE"
    payload = _json(marker)
    transaction_id = str(payload.get("transaction_id", ""))
    phase = payload.get("phase")
    if (
        len(transaction_id) != 32
        or any(value not in "0123456789abcdef" for value in transaction_id)
        or phase not in {
            "PREPARED", "DATASET_BACKED_UP", "DATASET_PROMOTED",
            "STATE_BACKED_UP", "STATE_PROMOTED", "VERIFIED",
        }
        or payload.get("dataset") != DATASET
        or payload.get("dataset_parent") != str(dataset_root.parent.resolve())
        or payload.get("state_parent") != str(state_path.parent.resolve())
        or not isinstance(payload.get("had_pair"), bool)
        or set(payload) != {
            "dataset", "transaction_id", "phase", "had_pair", "dataset_parent",
            "state_parent", "old_dataset_sha256", "old_state_sha256",
            "new_dataset_sha256", "new_state_sha256",
        }
        or any(
            not isinstance(payload.get(field), str)
            or len(str(payload.get(field))) != 64
            or any(character not in "0123456789abcdef" for character in str(payload.get(field)))
            for field in ("new_dataset_sha256", "new_state_sha256")
        )
        or (
            bool(payload.get("had_pair"))
            and any(
                not isinstance(payload.get(field), str)
                or len(str(payload.get(field))) != 64
                or any(character not in "0123456789abcdef"
                       for character in str(payload.get(field)))
                for field in ("old_dataset_sha256", "old_state_sha256")
            )
        )
        or (
            not bool(payload.get("had_pair"))
            and (payload.get("old_dataset_sha256") is not None
                 or payload.get("old_state_sha256") is not None)
        )
    ):
        raise RightsObservationError("transaction marker is invalid or unsafe")
    stage = dataset_root.parent / f".{DATASET}.rights-observation.stage.{transaction_id}"
    backup = dataset_root.parent / f".{DATASET}.rights-observation.backup.{transaction_id}"
    state_stage = state_path.parent / f".{state_path.name}.stage.{transaction_id}"
    state_backup = state_path.parent / f".{state_path.name}.backup.{transaction_id}"
    paths = (dataset_root, stage, backup, state_path, state_stage, state_backup)
    old_dataset = payload.get("old_dataset_sha256")
    old_state = payload.get("old_state_sha256")
    new_dataset = payload["new_dataset_sha256"]
    new_state = payload["new_state_sha256"]

    def classify_dataset(path: Path) -> str | None:
        if not path.exists():
            return None
        if not path.is_dir():
            raise RightsObservationError("dataset transaction path has unsafe type")
        try:
            _verify_physical_dataset(path)
            fingerprint = _tree_hash(path, logical_root)
        except Exception as error:
            raise RightsObservationError(
                "dataset transaction artifact cannot be fingerprinted"
            ) from error
        if fingerprint == old_dataset:
            return "old"
        if fingerprint == new_dataset:
            return "new"
        raise RightsObservationError("dataset transaction fingerprint is unknown")

    def classify_state(path: Path) -> str | None:
        if not path.exists():
            return None
        if not path.is_file():
            raise RightsObservationError("state transaction path has unsafe type")
        fingerprint = _sha256(path)
        if fingerprint == old_state:
            return "old"
        if fingerprint == new_state:
            return "new"
        raise RightsObservationError("state transaction fingerprint is unknown")

    layout = (
        classify_dataset(paths[0]), classify_dataset(paths[1]),
        classify_dataset(paths[2]), classify_state(paths[3]),
        classify_state(paths[4]), classify_state(paths[5]),
    )
    old = "old" if payload["had_pair"] else None
    layouts = {
        "L0": (old, None, None, old, None, None),
        "L1": (old, "new", None, old, "new", None),
        "L2": (None, "new", old, old, "new", None),
        "L3": ("new", None, old, old, "new", None),
        "L4": ("new", None, old, None, "new", old),
        "L5": ("new", None, old, "new", None, old),
        # Interrupted rollback layouts.
        "R1": (None, "new", old, None, "new", old),
        "R2": (old, "new", None, None, "new", old),
        "R3": (old, None, None, None, "new", old),
        "R4": (old, None, None, old, "new", None),
        # Verified cleanup may have retired either backup first.
        "F1": ("new", None, None, "new", None, old),
        "F2": ("new", None, old, "new", None, None),
        "F3": ("new", None, None, "new", None, None),
    }
    allowed_by_phase = {
        "PREPARED": {"L1", "L2", "R1", "R2", "R3", "R4", "L0"},
        "DATASET_BACKED_UP": {"L2", "L3", "R1", "R2", "R3", "R4", "L0"},
        "DATASET_PROMOTED": {"L3", "L4", "R1", "R2", "R3", "R4", "L0"},
        "STATE_BACKED_UP": {"L4", "L5", "R1", "R2", "R3", "R4", "L0"},
        "STATE_PROMOTED": {"L5", "R1", "R2", "R3", "R4", "L0"},
        "VERIFIED": {"L5", "F1", "F2", "F3"},
    }
    if layout not in {layouts[name] for name in allowed_by_phase[str(phase)]}:
        raise RightsObservationError(
            f"transaction layout contradicts journal phase {phase}"
        )

    if phase == "VERIFIED":
        shutil.rmtree(backup, ignore_errors=True)
        state_backup.unlink(missing_ok=True)
        shutil.rmtree(stage, ignore_errors=True)
        state_stage.unlink(missing_ok=True)
        marker.unlink()
        return "FINALIZED"
    if backup.exists():
        if dataset_root.exists():
            if stage.exists():
                raise RightsObservationError("dataset rollback topology is ambiguous")
            dataset_root.replace(stage)
        backup.replace(dataset_root)
    elif not payload["had_pair"] and dataset_root.exists():
        if stage.exists():
            raise RightsObservationError("new dataset rollback topology is ambiguous")
        dataset_root.replace(stage)
    if state_backup.exists():
        if state_path.exists():
            if state_stage.exists():
                raise RightsObservationError("state rollback topology is ambiguous")
            state_path.replace(state_stage)
        state_backup.replace(state_path)
    elif not payload["had_pair"] and state_path.exists():
        if state_stage.exists():
            raise RightsObservationError("new state rollback topology is ambiguous")
        state_path.replace(state_stage)
    shutil.rmtree(stage, ignore_errors=True)
    state_stage.unlink(missing_ok=True)
    if payload["had_pair"]:
        if (
            not dataset_root.is_dir() or not state_path.is_file()
            or _tree_hash(dataset_root, logical_root) != old_dataset
            or _sha256(state_path) != old_state
        ):
            raise RightsObservationError("rolled-back canonical pair differs")
    elif dataset_root.exists() or state_path.exists():
        raise RightsObservationError("new canonical pair remained after rollback")
    marker.unlink()
    return "ROLLED_BACK"


def _promote_rights_diagnostic_locked(
    *, project_root: Path, diagnostic_root: Path
) -> dict[str, object]:
    project_root = project_root.resolve()
    diagnostic_root = _under(project_root, diagnostic_root)
    logical_root = Path("data/normalized") / DATASET
    dataset_root = _under(project_root, project_root / logical_root)
    state_path = _under(
        project_root, project_root / "data/state/kr_equity_rights_schedule_observation.json"
    )
    startup_recovery = _recover(
        project_root=project_root, dataset_root=dataset_root,
        state_path=state_path, logical_root=logical_root,
    )
    incoming, snapshot = _extract_diagnostic(
        project_root=project_root, diagnostic_root=diagnostic_root
    )
    existing, snapshots = _load_existing(dataset_root, state_path, logical_root)
    identity = str(snapshot["response_body_sha256"])
    if existing is not None and identity in {
        str(value["response_body_sha256"]) for value in snapshots
    }:
        group = existing.loc[existing["landing_response_body_sha256"].eq(identity)]
        if not group.reset_index(drop=True).equals(incoming.reset_index(drop=True)):
            raise RightsObservationError("existing diagnostic identity has different rows")
        return {"status": "ALREADY_RECORDED", "startup_recovery": startup_recovery,
                "dataset": DATASET, "row_count": len(existing), "snapshot": snapshot}

    combined = incoming if existing is None else pd.concat([existing, incoming], ignore_index=True)
    combined = combined.sort_values(
        list(KR_EQUITY_RIGHTS_SCHEDULE.sort_key), kind="stable"
    ).reset_index(drop=True)
    _validator(combined)
    updated_snapshots = sorted(
        [*snapshots, snapshot],
        key=lambda value: (str(value["source_snapshot_date"]), str(value["response_body_sha256"])),
    )

    transaction_id = uuid4().hex
    stage = dataset_root.parent / f".{DATASET}.rights-observation.stage.{transaction_id}"
    state_stage = state_path.parent / f".{state_path.name}.stage.{transaction_id}"
    backup = dataset_root.parent / f".{DATASET}.rights-observation.backup.{transaction_id}"
    state_backup = state_path.parent / f".{state_path.name}.backup.{transaction_id}"
    marker = dataset_root.parent / f".{DATASET}.rights-observation.transaction.json"
    had_pair = dataset_root.exists()
    try:
        write_dataset_atomic(
            combined, stage, KR_EQUITY_RIGHTS_SCHEDULE, _validator
        )
        _verify_physical_dataset(stage)
        restored = read_dataset(stage, KR_EQUITY_RIGHTS_SCHEDULE, _validator)
        if not restored.equals(combined):
            raise RightsObservationError("staged dataset differs")
        output_manifest = _tree_manifest(stage, logical_root)
        state_payload = {
            "dataset": DATASET,
            "contract_version": KR_EQUITY_RIGHTS_SCHEDULE.version,
            "state_version": STATE_VERSION,
            "status": STATUS,
            "semantics": "append_only_source_observations_not_canonical_events",
            "historical_completeness": False,
            "canonical_economic_event_identity": False,
            "api_calls": 0,
            "snapshot_count": len(updated_snapshots),
            "row_count": len(combined),
            "snapshots": updated_snapshots,
            "output_manifest": output_manifest,
            "output_manifest_sha256": _manifest_sha256(output_manifest),
        }
        state_stage.parent.mkdir(parents=True, exist_ok=True)
        with state_stage.open("xb") as stream:
            stream.write(
                json.dumps(state_payload, ensure_ascii=False, indent=2, sort_keys=True).encode(
                    "utf-8"
                )
            )
            stream.flush()
            os.fsync(stream.fileno())
        old_dataset_sha256 = _tree_hash(dataset_root, logical_root) if had_pair else None
        old_state_sha256 = _sha256(state_path) if had_pair else None
        marker_payload = {
            "dataset": DATASET, "transaction_id": transaction_id, "phase": "PREPARED",
            "had_pair": had_pair, "dataset_parent": str(dataset_root.parent.resolve()),
            "state_parent": str(state_path.parent.resolve()),
            "old_dataset_sha256": old_dataset_sha256,
            "old_state_sha256": old_state_sha256,
            "new_dataset_sha256": _tree_hash(stage, logical_root),
            "new_state_sha256": _sha256(state_stage),
        }
        _atomic_json(marker, marker_payload)
        # Compare-and-swap gate immediately before the first canonical rename.
        if had_pair:
            if (
                _tree_hash(dataset_root, logical_root) != old_dataset_sha256
                or _sha256(state_path) != old_state_sha256
            ):
                raise RightsObservationError("existing dataset/state changed before promotion")
        elif dataset_root.exists() or state_path.exists():
            raise RightsObservationError("dataset/state appeared before promotion")
        if had_pair:
            dataset_root.replace(backup)
        marker_payload["phase"] = "DATASET_BACKED_UP"; _atomic_json(marker, marker_payload)
        stage.replace(dataset_root)
        marker_payload["phase"] = "DATASET_PROMOTED"; _atomic_json(marker, marker_payload)
        if had_pair:
            state_path.replace(state_backup)
        marker_payload["phase"] = "STATE_BACKED_UP"; _atomic_json(marker, marker_payload)
        state_stage.replace(state_path)
        marker_payload["phase"] = "STATE_PROMOTED"; _atomic_json(marker, marker_payload)
        if (
            _tree_hash(dataset_root, logical_root) != marker_payload["new_dataset_sha256"]
            or _sha256(state_path) != marker_payload["new_state_sha256"]
        ):
            raise RightsObservationError("promoted dataset/state differs")
        marker_payload["phase"] = "VERIFIED"; _atomic_json(marker, marker_payload)
        _recover(
            project_root=project_root, dataset_root=dataset_root,
            state_path=state_path, logical_root=logical_root,
        )
        return {"status": STATUS, "startup_recovery": startup_recovery,
                "dataset": DATASET, "row_count": len(combined), "snapshot": snapshot}
    except Exception:
        if marker.exists():
            _recover(
                project_root=project_root, dataset_root=dataset_root,
                state_path=state_path, logical_root=logical_root,
            )
        raise
    finally:
        shutil.rmtree(stage, ignore_errors=True)
        state_stage.unlink(missing_ok=True)


@contextmanager
def _exclusive_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    token = uuid4().hex
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise RightsObservationError("another Rights observation writer holds the lock") from error
    try:
        os.write(descriptor, token.encode("ascii"))
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        yield
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            current = path.read_text(encoding="ascii")
        except OSError as error:
            raise RightsObservationError("cannot verify owned Rights observation lock") from error
        if current != token:
            raise RightsObservationError("Rights observation lock ownership changed")
        path.unlink()


def promote_rights_diagnostic(
    *, project_root: Path, diagnostic_root: Path
) -> dict[str, object]:
    project_root = project_root.resolve()
    lock_path = _under(
        project_root, project_root / "data/state/.kr_equity_rights_schedule_observation.lock"
    )
    with _exclusive_lock(lock_path):
        return _promote_rights_diagnostic_locked(
            project_root=project_root, diagnostic_root=diagnostic_root
        )
