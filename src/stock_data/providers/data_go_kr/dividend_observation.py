"""Build a reproducible observation artifact from retained dividend Landing JSON.

The input is a lossless collection of data.go.kr response envelopes.  The
artifact records the exact retained-file hash and item ordinals, so it can be
reconstructed without a network request.  It intentionally makes no claim
that older event dates were known on those dates.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

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
            dir=path.parent, delete=False,
        ) as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            temporary = Path(handle.name)
        if json.loads(temporary.read_text(encoding="utf-8")) != payload:
            raise RuntimeError("state JSON read-back differs")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def build_dividend_observation(
    *, landing_path: Path, output_root: Path, state_path: Path,
) -> DividendObservationResult:
    """Materialize an isolated observation dataset and checkpoint from retained Landing."""
    frame, metadata = load_dividend_observation(landing_path)
    dataset_root = output_root / KR_EQUITY_DIVIDEND_SOURCE_OBSERVATION.name
    validator = lambda value: validate_data_v1(value, KR_EQUITY_DIVIDEND_SOURCE_OBSERVATION, allow_empty=False)
    write_dataset_atomic(frame, dataset_root, KR_EQUITY_DIVIDEND_SOURCE_OBSERVATION, validator)
    restored = read_dataset(dataset_root, KR_EQUITY_DIVIDEND_SOURCE_OBSERVATION, validator)
    if not restored.equals(frame):
        raise RuntimeError("observation Parquet read-back differs from verified frame")
    state = {
        "dataset": KR_EQUITY_DIVIDEND_SOURCE_OBSERVATION.name,
        "version": KR_EQUITY_DIVIDEND_SOURCE_OBSERVATION.version,
        "status": "ARTIFACT_COMPLETE",
        "semantics": "retained_current_snapshot_observation_not_historical_pit",
        **metadata,
        "row_count": len(frame),
    }
    _write_json_atomic(state_path, state)
    return DividendObservationResult(
        landing_file_sha256=str(metadata["landing_file_sha256"]),
        source_snapshot_date=str(metadata["source_snapshot_date"]),
        response_count=int(metadata["response_count"]),
        row_count=len(frame), output_root=dataset_root, state_path=state_path,
    )
