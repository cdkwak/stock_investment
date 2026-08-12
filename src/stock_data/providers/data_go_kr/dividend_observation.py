"""Build a reproducible observation artifact from retained dividend Landing JSON.

The input is a lossless collection of data.go.kr response envelopes.  The
artifact records the exact retained-file hash and item ordinals, so it can be
reconstructed without a network request.  It intentionally makes no claim
that older event dates were known on those dates.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any
from uuid import uuid4

import pandas as pd

from stock_data.contracts.dividend_observation import (
    KR_EQUITY_DIVIDEND_SOURCE_OBSERVATION,
)
from stock_data.providers.data_go_kr.data_v1 import normalize_dividend
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic
from stock_data.validation.data_v1 import validate_data_v1


class DividendObservationError(ValueError):
    """The retained Landing artifact cannot prove a reproducible observation."""


@dataclass(frozen=True)
class DividendObservationResult:
    landing_file_sha256: str
    source_snapshot_date: str
    response_count: int
    row_count: int
    output_root: Path
    state_path: Path


STATE_VERSION = 2


def _canonical_sha256(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _as_items(body: dict[str, Any]) -> list[dict[str, Any]]:
    container = body.get("items")
    if not isinstance(container, dict):
        raise DividendObservationError("response body items is missing")
    item = container.get("item", [])
    rows = item if isinstance(item, list) else [item]
    if not all(isinstance(row, dict) for row in rows):
        raise DividendObservationError("response body item is not an object list")
    return rows


def load_dividend_observation(landing_path: Path) -> tuple[pd.DataFrame, dict[str, object]]:
    """Read and verify one retained paginated landing snapshot, without I/O writes."""
    raw = landing_path.read_bytes()
    landing_file_sha256 = hashlib.sha256(raw).hexdigest()
    try:
        envelopes = json.loads(raw)
    except json.JSONDecodeError as error:
        raise DividendObservationError("landing file is not valid JSON") from error
    if not isinstance(envelopes, list) or not envelopes:
        raise DividendObservationError("landing file must contain response envelopes")

    expected_total: int | None = None
    expected_pages: list[int] = []
    source_dates: set[str] = set()
    rows: list[dict[str, object]] = []
    page_hashes: list[dict[str, object]] = []
    source_item_ordinal = 0
    for envelope_index, envelope in enumerate(envelopes):
        if not isinstance(envelope, dict):
            raise DividendObservationError(f"envelope {envelope_index} is not an object")
        response = envelope.get("response")
        if not isinstance(response, dict):
            raise DividendObservationError(f"envelope {envelope_index} response is missing")
        header = response.get("header")
        body = response.get("body")
        if not isinstance(header, dict) or str(header.get("resultCode", "")).zfill(2) != "00":
            raise DividendObservationError(f"envelope {envelope_index} is not a successful response")
        if not isinstance(body, dict):
            raise DividendObservationError(f"envelope {envelope_index} body is missing")
        try:
            page_no = int(body["pageNo"])
            total_count = int(body["totalCount"])
            page_size = int(body["numOfRows"])
        except (KeyError, TypeError, ValueError) as error:
            raise DividendObservationError(f"envelope {envelope_index} pagination metadata is invalid") from error
        if page_no < 1 or total_count < 0 or page_size < 1:
            raise DividendObservationError(f"envelope {envelope_index} pagination metadata is invalid")
        if expected_total is None:
            expected_total = total_count
        elif total_count != expected_total:
            raise DividendObservationError("totalCount changed within retained snapshot")
        expected_pages.append(page_no)
        items = _as_items(body)
        if len(items) > page_size:
            raise DividendObservationError(f"page {page_no} exceeds numOfRows")
        normalized = normalize_dividend(items)
        body_sha256 = _canonical_sha256(body)
        for page_item_ordinal, (raw_item, normalized_row) in enumerate(
            zip(items, normalized.to_dict("records"), strict=True)
        ):
            source_date = str(normalized_row["date"])
            source_dates.add(source_date)
            rows.append({
                "source_snapshot_date": source_date,
                "landing_file_sha256": landing_file_sha256,
                "source_response_body_canonical_sha256": body_sha256,
                "source_item_ordinal": source_item_ordinal,
                "source_page_no": page_no,
                "source_page_item_ordinal": page_item_ordinal,
                "source_record_canonical_sha256": _canonical_sha256(raw_item),
                **{key: value for key, value in normalized_row.items() if key != "date"},
            })
            source_item_ordinal += 1
        page_hashes.append({
            "page_no": page_no,
            "source_response_body_canonical_sha256": body_sha256,
            "item_count": len(items),
        })
    if expected_total is None or source_item_ordinal != expected_total:
        raise DividendObservationError("retained item count differs from totalCount")
    if sorted(expected_pages) != list(range(1, len(envelopes) + 1)):
        raise DividendObservationError("retained page numbers are not exactly sequential")
    if len(source_dates) != 1:
        raise DividendObservationError("retained snapshot contains multiple source dates")

    frame = pd.DataFrame(rows, columns=KR_EQUITY_DIVIDEND_SOURCE_OBSERVATION.column_names)
    frame = frame.sort_values(
        list(KR_EQUITY_DIVIDEND_SOURCE_OBSERVATION.sort_key), kind="stable"
    ).reset_index(drop=True)
    validate_data_v1(frame, KR_EQUITY_DIVIDEND_SOURCE_OBSERVATION, allow_empty=False)
    metadata: dict[str, object] = {
        "landing_file_sha256": landing_file_sha256,
        "source_snapshot_date": next(iter(source_dates)),
        "response_count": len(envelopes),
        "declared_total_count": expected_total,
        "page_hashes": page_hashes,
    }
    return frame, metadata


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".json.tmp", prefix=path.stem + "_",
            dir=path.parent, delete=False, newline="\n",
        ) as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        if json.loads(temporary.read_text(encoding="utf-8")) != payload:
            raise RuntimeError("state JSON read-back differs")
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _fsync_directory(path: Path) -> None:
    """Best-effort directory durability (unsupported by standard Windows handles)."""
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _json_value(value: object) -> object:
    """Return a stable JSON value for a contract-typed dataframe cell."""
    if pd.isna(value):
        return None
    if isinstance(value, (date, datetime, pd.Timestamp)):
        return value.isoformat()
    item = getattr(value, "item", None)
    return item() if callable(item) else value


def _frame_sha256(frame: pd.DataFrame) -> str:
    rows = [
        [_json_value(value) for value in row]
        for row in frame[list(KR_EQUITY_DIVIDEND_SOURCE_OBSERVATION.column_names)].itertuples(
            index=False, name=None
        )
    ]
    payload = json.dumps(rows, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _manifest_from_frame(
    frame: pd.DataFrame, *, declared_total_count: int | None = None,
) -> dict[str, object]:
    """Derive and validate the immutable manifest for exactly one snapshot."""
    if frame.empty:
        raise DividendObservationError("snapshot observation is empty")
    hashes = frame["landing_file_sha256"].drop_duplicates().tolist()
    dates = frame["source_snapshot_date"].astype(str).drop_duplicates().tolist()
    if len(hashes) != 1 or len(dates) != 1:
        raise DividendObservationError("snapshot identity is not unique")
    landing_hash = str(hashes[0])
    if len(landing_hash) != 64:
        raise DividendObservationError("snapshot landing hash is invalid")
    ordered = frame.sort_values("source_item_ordinal", kind="stable").reset_index(drop=True)
    if ordered["source_item_ordinal"].tolist() != list(range(len(ordered))):
        raise DividendObservationError("snapshot source item ordinals are not contiguous")

    page_hashes: list[dict[str, object]] = []
    expected_pages = sorted(int(value) for value in ordered["source_page_no"].unique())
    if expected_pages != list(range(1, len(expected_pages) + 1)):
        raise DividendObservationError("snapshot page numbers are not contiguous")
    for page_no in expected_pages:
        page = ordered.loc[ordered["source_page_no"] == page_no]
        if page["source_page_item_ordinal"].tolist() != list(range(len(page))):
            raise DividendObservationError("snapshot page item ordinals are not contiguous")
        body_hashes = page["source_response_body_canonical_sha256"].drop_duplicates().tolist()
        if len(body_hashes) != 1 or len(str(body_hashes[0])) != 64:
            raise DividendObservationError("snapshot page response hash is invalid")
        page_hashes.append({
            "page_no": page_no,
            "source_response_body_canonical_sha256": str(body_hashes[0]),
            "item_count": len(page),
        })
    total = len(ordered) if declared_total_count is None else int(declared_total_count)
    if total != len(ordered):
        raise DividendObservationError("snapshot declared count differs from artifact rows")
    return {
        "landing_file_sha256": landing_hash,
        "source_snapshot_date": dates[0],
        "response_count": len(expected_pages),
        "declared_total_count": total,
        "row_count": len(ordered),
        "page_hashes": page_hashes,
        "normalized_rows_canonical_sha256": _frame_sha256(ordered),
    }


def _manifest_from_landing(frame: pd.DataFrame, metadata: dict[str, object]) -> dict[str, object]:
    manifest = _manifest_from_frame(
        frame, declared_total_count=int(metadata["declared_total_count"])
    )
    for field in (
        "landing_file_sha256", "source_snapshot_date", "response_count",
        "declared_total_count", "page_hashes",
    ):
        if manifest[field] != metadata[field]:
            raise DividendObservationError(f"landing metadata differs from rows: {field}")
    return manifest


def _load_existing(
    dataset_root: Path, state_path: Path, validator,
) -> tuple[pd.DataFrame | None, list[dict[str, object]], bool]:
    """Load an existing artifact and prove that its state describes every row."""
    dataset_exists = dataset_root.exists()
    state_exists = state_path.exists()
    if dataset_exists != state_exists:
        raise DividendObservationError("existing dataset/state pair is incomplete")
    if not dataset_exists:
        return None, [], False

    unexpected = [
        path for path in dataset_root.rglob("*")
        if path.is_file()
        and not (
            path.name == "data.parquet"
            and path.parent.parent == dataset_root
            and path.parent.name.startswith("year=")
        )
    ]
    if unexpected:
        raise DividendObservationError("existing dataset contains unexpected files")
    existing = read_dataset(dataset_root, KR_EQUITY_DIVIDEND_SOURCE_OBSERVATION, validator)
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DividendObservationError("existing state is not valid JSON") from error
    if not isinstance(state, dict):
        raise DividendObservationError("existing state is not an object")
    if state.get("dataset") != KR_EQUITY_DIVIDEND_SOURCE_OBSERVATION.name:
        raise DividendObservationError("existing state dataset differs")
    if int(state.get("version", -1)) != KR_EQUITY_DIVIDEND_SOURCE_OBSERVATION.version:
        raise DividendObservationError("existing state contract version differs")
    if int(state.get("row_count", -1)) != len(existing):
        raise DividendObservationError("existing state row count differs")

    is_v2 = state.get("state_version") == STATE_VERSION
    if is_v2:
        raw_manifests = state.get("snapshots")
        if not isinstance(raw_manifests, list) or not raw_manifests:
            raise DividendObservationError("existing state snapshot manifest is missing")
        if int(state.get("snapshot_count", -1)) != len(raw_manifests):
            raise DividendObservationError("existing state snapshot count differs")
    elif "state_version" not in state:
        raw_manifests = [{
            key: state[key] for key in (
                "landing_file_sha256", "source_snapshot_date", "response_count",
                "declared_total_count", "page_hashes", "row_count",
            )
        }]
    else:
        raise DividendObservationError("existing state version is unsupported")

    manifests: list[dict[str, object]] = []
    state_by_hash: dict[str, dict[str, object]] = {}
    for value in raw_manifests:
        if not isinstance(value, dict):
            raise DividendObservationError("existing snapshot manifest is invalid")
        landing_hash = str(value.get("landing_file_sha256", ""))
        if landing_hash in state_by_hash:
            raise DividendObservationError("existing state repeats a snapshot hash")
        state_by_hash[landing_hash] = value
    grouped_hashes = existing["landing_file_sha256"].drop_duplicates().tolist()
    if set(str(value) for value in grouped_hashes) != set(state_by_hash):
        raise DividendObservationError("existing state/artifact snapshot identities differ")
    for landing_hash in sorted(state_by_hash):
        group = existing.loc[existing["landing_file_sha256"] == landing_hash].copy()
        declared = int(state_by_hash[landing_hash].get("declared_total_count", -1))
        actual = _manifest_from_frame(group, declared_total_count=declared)
        expected = state_by_hash[landing_hash]
        compared_fields = (
            "landing_file_sha256", "source_snapshot_date", "response_count",
            "declared_total_count", "row_count", "page_hashes",
        )
        for field in compared_fields:
            if actual[field] != expected.get(field):
                raise DividendObservationError(
                    f"existing state/artifact snapshot differs: {landing_hash} {field}"
                )
        if is_v2 and actual["normalized_rows_canonical_sha256"] != expected.get(
            "normalized_rows_canonical_sha256"
        ):
            raise DividendObservationError(
                f"existing state/artifact snapshot differs: {landing_hash} normalized hash"
            )
        manifests.append(actual)
    return existing, manifests, is_v2


def _state_payload(manifests: list[dict[str, object]], row_count: int) -> dict[str, object]:
    ordered = sorted(
        manifests, key=lambda value: (str(value["source_snapshot_date"]), str(value["landing_file_sha256"]))
    )
    return {
        "dataset": KR_EQUITY_DIVIDEND_SOURCE_OBSERVATION.name,
        "version": KR_EQUITY_DIVIDEND_SOURCE_OBSERVATION.version,
        "state_version": STATE_VERSION,
        "status": "ARTIFACT_COMPLETE",
        "semantics": "append_only_source_observations_not_historical_pit",
        "snapshot_identity": "landing_file_sha256",
        "snapshot_count": len(ordered),
        "row_count": row_count,
        "snapshots": ordered,
    }


_TRANSACTION_PHASES = {
    "PREPARING", "STAGED", "PROMOTION_PENDING", "DATASET_BACKED_UP",
    "DATASET_PROMOTED", "STATE_BACKED_UP", "STATE_PROMOTED", "VERIFIED",
    "DATASET_BACKUP_RETIRED", "BACKUPS_RETIRED", "CLEANUP_FINISHED",
    "RECOVERED_ORIGINAL",
}
_FINAL_TRANSACTION_PHASES = {
    "VERIFIED", "DATASET_BACKUP_RETIRED", "BACKUPS_RETIRED", "CLEANUP_FINISHED",
}


def _transaction_marker(dataset_root: Path) -> Path:
    return dataset_root.parent / (
        f".{KR_EQUITY_DIVIDEND_SOURCE_OBSERVATION.name}.dividend-append.transaction.json"
    )


def _transaction_paths(
    dataset_root: Path, state_path: Path, payload: dict[str, object],
) -> dict[str, Path]:
    transaction_id = str(payload.get("transaction_id", ""))
    dataset = KR_EQUITY_DIVIDEND_SOURCE_OBSERVATION.name
    names = {
        "dataset_stage": f".{dataset}.dividend-append.stage.{transaction_id}",
        "dataset_backup": f".{dataset}.dividend-append.backup.{transaction_id}",
        "dataset_retired": f".{dataset}.dividend-append.retired.{transaction_id}",
        "state_stage": f".{state_path.name}.dividend-append.stage.{transaction_id}",
        "state_backup": f".{state_path.name}.dividend-append.backup.{transaction_id}",
        "state_retired": f".{state_path.name}.dividend-append.retired.{transaction_id}",
    }
    had_dataset = payload.get("had_dataset")
    old_dataset_hash = payload.get("old_dataset_sha256")
    old_state_hash = payload.get("old_state_sha256")
    new_dataset_hash = payload.get("new_dataset_sha256")
    new_state_hash = payload.get("new_state_sha256")

    def valid_hash(value: object) -> bool:
        return (
            isinstance(value, str) and len(value) == 64
            and all(character in "0123456789abcdef" for character in value)
        )

    if (
        len(transaction_id) != 32
        or any(character not in "0123456789abcdef" for character in transaction_id)
        or payload.get("dataset") != dataset
        or payload.get("dataset_root_name") != dataset_root.name
        or payload.get("state_name") != state_path.name
        or payload.get("dataset_parent_resolved") != str(dataset_root.parent.resolve())
        or payload.get("state_parent_resolved") != str(state_path.parent.resolve())
        or payload.get("phase") not in _TRANSACTION_PHASES
        or any(payload.get(key + "_name") != value for key, value in names.items())
        or not isinstance(had_dataset, bool)
        or not isinstance(payload.get("had_state"), bool)
        or had_dataset != payload.get("had_state")
        or (had_dataset and (not valid_hash(old_dataset_hash) or not valid_hash(old_state_hash)))
        or (not had_dataset and (old_dataset_hash is not None or old_state_hash is not None))
        or not valid_hash(new_dataset_hash)
        or not valid_hash(new_state_hash)
    ):
        raise DividendObservationError("dividend append transaction marker is invalid or unsafe")
    return {
        "dataset_stage": dataset_root.parent / names["dataset_stage"],
        "dataset_backup": dataset_root.parent / names["dataset_backup"],
        "dataset_retired": dataset_root.parent / names["dataset_retired"],
        "state_stage": state_path.parent / names["state_stage"],
        "state_backup": state_path.parent / names["state_backup"],
        "state_retired": state_path.parent / names["state_retired"],
    }


def _transaction_orphans(dataset_root: Path, state_path: Path) -> set[Path]:
    dataset = KR_EQUITY_DIVIDEND_SOURCE_OBSERVATION.name
    result = set(dataset_root.parent.glob(f".{dataset}.dividend-append.stage.*"))
    result.update(dataset_root.parent.glob(f".{dataset}.dividend-append.backup.*"))
    result.update(dataset_root.parent.glob(f".{dataset}.dividend-append.retired.*"))
    result.update(state_path.parent.glob(f".{state_path.name}.dividend-append.stage.*"))
    result.update(state_path.parent.glob(f".{state_path.name}.dividend-append.backup.*"))
    result.update(state_path.parent.glob(f".{state_path.name}.dividend-append.retired.*"))
    result.update(dataset_root.parent.glob(f".{dataset}.dividend-append.transaction.json.*.tmp"))
    return result


def _read_marker_payload(path: Path) -> dict[str, object]:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise DividendObservationError("dividend append transaction marker is unreadable") from error
    complete_lines = raw.splitlines(keepends=True)
    payloads: list[dict[str, object]] = []
    try:
        for line in complete_lines:
            if not line.endswith((b"\n", b"\r")):
                break
            value = json.loads(line.decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("journal record is not an object")
            payloads.append(value)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
        raise DividendObservationError("dividend append transaction marker is unreadable") from error
    if not payloads:
        raise DividendObservationError("dividend append transaction marker is invalid")
    return payloads[-1]


def _write_transaction_marker(path: Path, payload: dict[str, object]) -> None:
    with path.open("ab") as stream:
        stream.write(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        stream.write(b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def _create_transaction_marker(path: Path, payload: dict[str, object]) -> None:
    """Create the initial journal exclusively to prevent marker replacement races."""
    try:
        with path.open("xb") as stream:
            stream.write(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
            stream.write(b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        _fsync_directory(path.parent)
    except FileExistsError as error:
        raise DividendObservationError("another dividend append transaction owns the marker") from error


def _set_transaction_phase(
    marker: Path, payload: dict[str, object], phase: str,
) -> None:
    updated = {**payload, "phase": phase}
    _write_transaction_marker(marker, updated)
    payload.clear()
    payload.update(updated)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _dataset_sha256(dataset_root: Path, validator) -> str:
    restored = read_dataset(
        dataset_root, KR_EQUITY_DIVIDEND_SOURCE_OBSERVATION, validator
    )
    return _frame_sha256(restored)


def _remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink(missing_ok=True)


def _rollback_component(
    *, canonical: Path, stage: Path, backup: Path, retired: Path,
    had_original: bool, is_directory: bool,
) -> None:
    if retired.exists():
        raise DividendObservationError("retired backup exists before transaction verification")
    canonical_exists = canonical.is_dir() if is_directory else canonical.is_file()
    stage_exists = stage.is_dir() if is_directory else stage.is_file()
    backup_exists = backup.is_dir() if is_directory else backup.is_file()
    for path in (canonical, stage, backup):
        if path.exists() and (
            (is_directory and not path.is_dir()) or (not is_directory and not path.is_file())
        ):
            raise DividendObservationError("transaction component has an unsafe path type")

    if backup_exists:
        if canonical_exists:
            if stage_exists:
                raise DividendObservationError("transaction rollback paths are ambiguous")
            canonical.replace(stage)
            _fsync_directory(canonical.parent)
            stage_exists = True
        backup.replace(canonical)
        _fsync_directory(canonical.parent)
        canonical_exists = True
    elif stage_exists:
        if had_original and not canonical_exists:
            raise DividendObservationError("original transaction component is missing")
        if not had_original and canonical_exists:
            raise DividendObservationError("new transaction component identity is ambiguous")
    elif not had_original and canonical_exists:
        canonical.replace(stage)
        _fsync_directory(canonical.parent)
        stage_exists = True
        canonical_exists = False
    elif had_original and not canonical_exists:
        raise DividendObservationError("original transaction component is missing")

    if stage_exists:
        _remove_path(stage)
        _fsync_directory(stage.parent)


def _verify_transaction_artifacts(
    *, dataset_root: Path, state_path: Path, payload: dict[str, object],
    validator, expected: str,
) -> None:
    dataset_hash = payload.get(f"{expected}_dataset_sha256")
    state_hash = payload.get(f"{expected}_state_sha256")
    if dataset_hash is None and state_hash is None:
        if dataset_root.exists() or state_path.exists():
            raise DividendObservationError(f"{expected} transaction artifacts should be absent")
        return
    if not dataset_root.is_dir() or not state_path.is_file():
        raise DividendObservationError(f"{expected} transaction artifact pair is incomplete")
    if _dataset_sha256(dataset_root, validator) != dataset_hash:
        raise DividendObservationError(f"{expected} transaction dataset fingerprint differs")
    if _file_sha256(state_path) != state_hash:
        raise DividendObservationError(f"{expected} transaction state fingerprint differs")


def recover_dividend_observation_transaction(
    *, dataset_root: Path, state_path: Path, validator,
) -> str:
    """Recover a journaled append or refuse any state that is not unambiguous."""
    marker = _transaction_marker(dataset_root)
    marker_temporaries = sorted(
        dataset_root.parent.glob(f"{marker.name}.*.tmp")
    )
    if not marker.exists() and marker_temporaries:
        if len(marker_temporaries) != 1:
            raise DividendObservationError("multiple orphan transaction markers exist")
        candidate = marker_temporaries[0]
        other_orphans = _transaction_orphans(dataset_root, state_path) - {candidate}
        if not other_orphans:
            candidate.unlink()
            _fsync_directory(candidate.parent)
            return "DISCARDED_UNINSTALLED_MARKER"
        payload = _read_marker_payload(candidate)
        _transaction_paths(dataset_root, state_path, payload)
        candidate.replace(marker)
        _fsync_directory(marker.parent)
        marker_temporaries = []
    orphans = _transaction_orphans(dataset_root, state_path)
    if not marker.exists():
        if orphans:
            raise DividendObservationError("orphan dividend append paths exist without a marker")
        return "NONE"
    if marker_temporaries:
        if len(marker_temporaries) != 1:
            raise DividendObservationError("ambiguous temporary transaction markers exist")
        marker_temporaries[0].unlink()
        _fsync_directory(marker.parent)
    try:
        payload = _read_marker_payload(marker)
    except DividendObservationError:
        non_marker_orphans = _transaction_orphans(dataset_root, state_path)
        pair_is_intact = (
            (dataset_root.is_dir() and state_path.is_file())
            or (not dataset_root.exists() and not state_path.exists())
        )
        if non_marker_orphans or not pair_is_intact:
            raise
        marker.unlink()
        _fsync_directory(marker.parent)
        return "DISCARDED_INCOMPLETE_INITIAL_MARKER"
    paths = _transaction_paths(dataset_root, state_path, payload)
    unexpected = orphans - set(paths.values())
    if unexpected:
        raise DividendObservationError("ambiguous orphan dividend append paths exist")
    for key in ("dataset_stage", "dataset_backup", "dataset_retired"):
        if paths[key].exists() and not paths[key].is_dir():
            raise DividendObservationError("dataset transaction path is not a directory")
    for key in ("state_stage", "state_backup", "state_retired"):
        if paths[key].exists() and not paths[key].is_file():
            raise DividendObservationError("state transaction path is not a file")

    phase = str(payload["phase"])
    if phase in _FINAL_TRANSACTION_PHASES:
        if paths["dataset_stage"].exists() or paths["state_stage"].exists():
            raise DividendObservationError("verified transaction still has staged artifacts")
        _verify_transaction_artifacts(
            dataset_root=dataset_root, state_path=state_path, payload=payload,
            validator=validator, expected="new",
        )
        for original, retired in (
            (paths["dataset_backup"], paths["dataset_retired"]),
            (paths["state_backup"], paths["state_retired"]),
        ):
            if original.exists() and retired.exists():
                raise DividendObservationError("duplicate backup and retired paths exist")
            if original.exists():
                original.replace(retired)
                _fsync_directory(original.parent)
        _set_transaction_phase(marker, payload, "BACKUPS_RETIRED")
        for retired in (paths["dataset_retired"], paths["state_retired"]):
            _remove_path(retired)
            _fsync_directory(retired.parent)
        _set_transaction_phase(marker, payload, "CLEANUP_FINISHED")
        marker.unlink()
        _fsync_directory(marker.parent)
        return "FINALIZED_NEW_ARTIFACT"

    try:
        _rollback_component(
            canonical=dataset_root, stage=paths["dataset_stage"],
            backup=paths["dataset_backup"], retired=paths["dataset_retired"],
            had_original=bool(payload["had_dataset"]), is_directory=True,
        )
        _rollback_component(
            canonical=state_path, stage=paths["state_stage"],
            backup=paths["state_backup"], retired=paths["state_retired"],
            had_original=bool(payload["had_state"]), is_directory=False,
        )
        _verify_transaction_artifacts(
            dataset_root=dataset_root, state_path=state_path, payload=payload,
            validator=validator, expected="old",
        )
        _set_transaction_phase(marker, payload, "RECOVERED_ORIGINAL")
        marker.unlink()
        _fsync_directory(marker.parent)
        return "RESTORED_ORIGINAL_ARTIFACT"
    except DividendObservationError:
        raise
    except BaseException as error:
        raise DividendObservationError(
            "transaction recovery failed; journal and recoverable paths were retained"
        ) from error


def _commit_dataset_and_state(
    frame: pd.DataFrame, dataset_root: Path, state_path: Path,
    state: dict[str, object], validator,
) -> None:
    """Journal, stage and durably commit the complete dataset/state pair."""
    dataset_root.parent.mkdir(parents=True, exist_ok=True)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    old_dataset = dataset_root.exists()
    old_state = state_path.exists()
    if old_dataset != old_state:
        raise DividendObservationError("existing dataset/state pair is incomplete")
    transaction_id = uuid4().hex
    dataset = KR_EQUITY_DIVIDEND_SOURCE_OBSERVATION.name
    marker = _transaction_marker(dataset_root)
    names = {
        "dataset_stage_name": f".{dataset}.dividend-append.stage.{transaction_id}",
        "dataset_backup_name": f".{dataset}.dividend-append.backup.{transaction_id}",
        "dataset_retired_name": f".{dataset}.dividend-append.retired.{transaction_id}",
        "state_stage_name": f".{state_path.name}.dividend-append.stage.{transaction_id}",
        "state_backup_name": f".{state_path.name}.dividend-append.backup.{transaction_id}",
        "state_retired_name": f".{state_path.name}.dividend-append.retired.{transaction_id}",
    }
    payload: dict[str, object] = {
        "transaction_id": transaction_id,
        "dataset": dataset,
        "dataset_root_name": dataset_root.name,
        "state_name": state_path.name,
        "dataset_parent_resolved": str(dataset_root.parent.resolve()),
        "state_parent_resolved": str(state_path.parent.resolve()),
        "phase": "PREPARING",
        "had_dataset": old_dataset,
        "had_state": old_state,
        "old_dataset_sha256": _dataset_sha256(dataset_root, validator) if old_dataset else None,
        "old_state_sha256": _file_sha256(state_path) if old_state else None,
        "new_dataset_sha256": _frame_sha256(frame),
        "new_state_sha256": hashlib.sha256(
            (json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
        ).hexdigest(),
        **names,
    }
    paths = _transaction_paths(dataset_root, state_path, payload)
    _create_transaction_marker(marker, payload)
    try:
        write_dataset_atomic(
            frame, paths["dataset_stage"],
            KR_EQUITY_DIVIDEND_SOURCE_OBSERVATION, validator,
        )
        restored = read_dataset(
            paths["dataset_stage"], KR_EQUITY_DIVIDEND_SOURCE_OBSERVATION, validator
        )
        if not restored.equals(frame):
            raise RuntimeError("staged observation Parquet differs from combined frame")
        _write_json_atomic(paths["state_stage"], state)
        if json.loads(paths["state_stage"].read_text(encoding="utf-8")) != state:
            raise RuntimeError("staged observation state differs")
        _set_transaction_phase(marker, payload, "STAGED")
        _set_transaction_phase(marker, payload, "PROMOTION_PENDING")
        if old_dataset:
            dataset_root.replace(paths["dataset_backup"])
            _fsync_directory(dataset_root.parent)
        _set_transaction_phase(marker, payload, "DATASET_BACKED_UP")
        paths["dataset_stage"].replace(dataset_root)
        _fsync_directory(dataset_root.parent)
        _set_transaction_phase(marker, payload, "DATASET_PROMOTED")
        if old_state:
            state_path.replace(paths["state_backup"])
            _fsync_directory(state_path.parent)
        _set_transaction_phase(marker, payload, "STATE_BACKED_UP")
        paths["state_stage"].replace(state_path)
        _fsync_directory(state_path.parent)
        _set_transaction_phase(marker, payload, "STATE_PROMOTED")
        _verify_transaction_artifacts(
            dataset_root=dataset_root, state_path=state_path, payload=payload,
            validator=validator, expected="new",
        )
        _set_transaction_phase(marker, payload, "VERIFIED")
        if paths["dataset_backup"].exists():
            paths["dataset_backup"].replace(paths["dataset_retired"])
            _fsync_directory(dataset_root.parent)
        _set_transaction_phase(marker, payload, "DATASET_BACKUP_RETIRED")
        if paths["state_backup"].exists():
            paths["state_backup"].replace(paths["state_retired"])
            _fsync_directory(state_path.parent)
        _set_transaction_phase(marker, payload, "BACKUPS_RETIRED")
        for retired in (paths["dataset_retired"], paths["state_retired"]):
            _remove_path(retired)
            _fsync_directory(retired.parent)
        _set_transaction_phase(marker, payload, "CLEANUP_FINISHED")
        marker.unlink()
        _fsync_directory(marker.parent)
    except BaseException:
        try:
            recover_dividend_observation_transaction(
                dataset_root=dataset_root, state_path=state_path, validator=validator
            )
        except BaseException as recovery_error:
            raise DividendObservationError(
                "append failed and durable transaction recovery did not complete"
            ) from recovery_error
        raise


def build_dividend_observation(
    *, landing_path: Path, output_root: Path, state_path: Path,
) -> DividendObservationResult:
    """Append one immutable Landing snapshot without replacing prior observations."""
    frame, metadata = load_dividend_observation(landing_path)
    dataset_root = output_root / KR_EQUITY_DIVIDEND_SOURCE_OBSERVATION.name
    validator = lambda value: validate_data_v1(value, KR_EQUITY_DIVIDEND_SOURCE_OBSERVATION, allow_empty=False)
    recover_dividend_observation_transaction(
        dataset_root=dataset_root, state_path=state_path, validator=validator
    )
    incoming_manifest = _manifest_from_landing(frame, metadata)
    existing, manifests, is_v2 = _load_existing(dataset_root, state_path, validator)
    if existing is None:
        combined = frame
    else:
        same = existing.loc[
            existing["landing_file_sha256"] == incoming_manifest["landing_file_sha256"]
        ].reset_index(drop=True)
        if not same.empty:
            expected = frame.reset_index(drop=True)
            if not same.equals(expected):
                raise DividendObservationError(
                    "existing snapshot hash resolves to different normalized content"
                )
            prior = next(
                value for value in manifests
                if value["landing_file_sha256"] == incoming_manifest["landing_file_sha256"]
            )
            if prior != incoming_manifest:
                raise DividendObservationError(
                    "existing snapshot hash resolves to different manifest content"
                )
            if is_v2:
                return DividendObservationResult(
                    landing_file_sha256=str(metadata["landing_file_sha256"]),
                    source_snapshot_date=str(metadata["source_snapshot_date"]),
                    response_count=int(metadata["response_count"]),
                    row_count=len(frame), output_root=dataset_root, state_path=state_path,
                )
            combined = existing
        else:
            combined = pd.concat([existing, frame], ignore_index=True)
            combined = combined.sort_values(
                list(KR_EQUITY_DIVIDEND_SOURCE_OBSERVATION.sort_key), kind="stable"
            ).reset_index(drop=True)
            manifests.append(incoming_manifest)
    validate_data_v1(combined, KR_EQUITY_DIVIDEND_SOURCE_OBSERVATION, allow_empty=False)
    if not any(
        value["landing_file_sha256"] == incoming_manifest["landing_file_sha256"]
        for value in manifests
    ):
        manifests.append(incoming_manifest)
    state = _state_payload(manifests, len(combined))
    _commit_dataset_and_state(combined, dataset_root, state_path, state, validator)
    return DividendObservationResult(
        landing_file_sha256=str(metadata["landing_file_sha256"]),
        source_snapshot_date=str(metadata["source_snapshot_date"]),
        response_count=int(metadata["response_count"]),
        row_count=len(frame), output_root=dataset_root, state_path=state_path,
    )
