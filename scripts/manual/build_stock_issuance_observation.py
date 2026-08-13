"""Build the retained DATA.GO.KR stock-issuance history without source calls."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
from uuid import uuid4

import pandas as pd
import pyarrow.parquet as pq


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from scripts.manual.collect_data_go_kr_stock_issuance_snapshot import (  # noqa: E402
    verify_complete_snapshot,
)
from stock_data.contracts.stock_issuance_observation import (  # noqa: E402
    KR_EQUITY_STOCK_ISSUANCE_SOURCE_OBSERVATION as CONTRACT,
)
from stock_data.storage.contract_arrow import contract_arrow_schema  # noqa: E402
from stock_data.storage.contract_parquet import write_dataset_atomic  # noqa: E402


DATASET = CONTRACT.name
EXPECTED_RUN_ID = "20260813T173606Z_28afa7bd957b42aab02604f79cd47588"
EXPECTED_SOURCE_MANIFEST_SHA256 = "7d189ec7d7cbac53aad5801a171b0656114225ba8d92d979ba716ae26bb54776"
STATUS = "ARTIFACT_COMPLETE_SOURCE_OBSERVATION"
AVAILABILITY = "SOURCE_REFERENCE_DATE_ONLY_PUBLICATION_TIME_UNKNOWN"
DATE_STATUSES = {"PARSED", "MISSING", "INVALID_SOURCE_TOKEN", "OUT_OF_SUPPORTED_RANGE"}


class BuildError(RuntimeError):
    pass


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha(path: Path) -> str:
    return _sha_bytes(path.read_bytes())


def _canonical_sha(value: object) -> str:
    return _sha_bytes(json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8"))


def _atomic_json(path: Path, value: object) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with temporary.open("xb") as stream:
            stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2).encode())
            stream.write(b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _plain_under(project_root: Path, path: Path) -> Path:
    lexical = Path(os.path.abspath(path))
    if lexical.is_symlink():
        raise BuildError(f"symlink path rejected: {path}")
    resolved = lexical.resolve()
    try:
        resolved.relative_to(project_root.resolve())
    except ValueError as error:
        raise BuildError(f"path escapes project root: {path}") from error
    return resolved


def _manifest(root: Path, logical_root: Path) -> list[dict[str, object]]:
    paths = sorted(root.glob("year=*/data.parquet"))
    if not paths or paths != sorted(root.rglob("*.parquet")):
        raise BuildError("dataset topology differs")
    return [{
        "path": (logical_root / path.relative_to(root)).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": _sha(path),
        "rows": pq.ParquetFile(path).metadata.num_rows,
    } for path in paths]


def _source_tree(run_root: Path) -> list[dict[str, object]]:
    return [{
        "path": path.relative_to(run_root).as_posix(),
        "bytes": path.stat().st_size,
        "sha256": _sha(path),
    } for path in sorted(run_root.rglob("*")) if path.is_file()]


def _optional(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _date_token(value: object) -> tuple[str | None, str | None, str]:
    source = _optional(value)
    if source is None:
        return None, None, "MISSING"
    try:
        parsed = datetime.strptime(source, "%Y%m%d").date()
    except ValueError:
        return source, None, "INVALID_SOURCE_TOKEN"
    if pd.isna(pd.to_datetime(pd.Series([parsed.isoformat()]), errors="coerce").iloc[0]):
        return source, None, "OUT_OF_SUPPORTED_RANGE"
    return source, parsed.isoformat(), "PARSED"


def normalize_retained_run(project_root: Path, run_root: Path) -> tuple[pd.DataFrame, dict[str, object]]:
    project_root = project_root.resolve()
    run_root = _plain_under(project_root, run_root)
    audit = verify_complete_snapshot(project_root, run_root)
    if (
        run_root.name != EXPECTED_RUN_ID
        or audit.get("status") != "OFFLINE_AUDIT_PASS"
        or audit.get("manifest_sha256") != EXPECTED_SOURCE_MANIFEST_SHA256
        or audit.get("rows") != 152_676
        or audit.get("network_requests") != 0
    ):
        raise BuildError("retained source audit differs from the frozen evidence")
    before = _source_tree(run_root)
    records: list[dict[str, object]] = []
    global_ordinal = 0
    invalid_issue = 0
    invalid_listing = 0
    out_of_range_issue = 0
    out_of_range_listing = 0
    negative_shares = 0
    for page_no in range(1, 17):
        page = run_root / f"page={page_no:05d}"
        raw_path = page / "raw_response.body"
        raw_sha256 = _sha(raw_path)
        call = json.loads((page / "raw_call.json").read_text(encoding="utf-8"))
        payload = json.loads(raw_path.read_bytes())
        items = payload["response"]["body"]["items"]["item"]
        items = items if isinstance(items, list) else [items]
        for page_ordinal, item in enumerate(items, 1):
            global_ordinal += 1
            issue_source, issue_date, issue_status = _date_token(item.get("stckIssuDt"))
            list_source, listing_date, listing_status = _date_token(item.get("lstgDt"))
            invalid_issue += issue_status == "INVALID_SOURCE_TOKEN"
            invalid_listing += listing_status == "INVALID_SOURCE_TOKEN"
            out_of_range_issue += issue_status == "OUT_OF_SUPPORTED_RANGE"
            out_of_range_listing += listing_status == "OUT_OF_SUPPORTED_RANGE"
            shares_text = _optional(item.get("issuStckCnt"))
            shares = None if shares_text is None else int(shares_text)
            negative_shares += shares is not None and shares < 0
            canonical = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            records.append({
                "source_snapshot_date": datetime.strptime(str(item["basDt"]), "%Y%m%d").date().isoformat(),
                "capture_id": run_root.name,
                "captured_at_utc": pd.Timestamp(call["captured_at_utc"]),
                "landing_response_sha256": raw_sha256,
                "source_page_no": page_no,
                "source_page_item_ordinal": page_ordinal,
                "source_item_ordinal": global_ordinal,
                "source_record_sha256": _sha_bytes(canonical.encode("utf-8")),
                "corporate_number": str(item["crno"]).strip(),
                "isin": _optional(item.get("isinCd")),
                "security_name": _optional(item.get("isinCdNm")),
                "issuer_name": str(item["stckIssuCmpyNm"]).strip(),
                "securities_classification_code": _optional(item.get("scrsDcd")),
                "issuance_sequence_no": _optional(item.get("stckIssuSqno")),
                "issue_effective_date_source": issue_source,
                "issue_effective_date": issue_date,
                "issue_effective_date_status": issue_status,
                "issuance_round_no": _optional(item.get("stckIssuDcnt")),
                "security_type_code": _optional(item.get("scrsItmsKcd")),
                "security_type_name": _optional(item.get("scrsItmsKcdNm")),
                "issuance_reason_code": _optional(item.get("stckIssuRcd")),
                "issuance_reason_name": _optional(item.get("stckIssuRcdNm")),
                "issued_shares": shares,
                "listing_date_source": list_source,
                "listing_date": listing_date,
                "listing_date_status": listing_status,
                "availability_status": AVAILABILITY,
            })
    frame = pd.DataFrame(records, columns=CONTRACT.column_names)
    validate_frame(frame)
    if _source_tree(run_root) != before:
        raise BuildError("retained source changed during normalization")
    return frame, {
        "negative_issued_share_rows": int(negative_shares),
        "invalid_issue_date_rows": int(invalid_issue),
        "invalid_listing_date_rows": int(invalid_listing),
        "out_of_range_issue_date_rows": int(out_of_range_issue),
        "out_of_range_listing_date_rows": int(out_of_range_listing),
    }


def validate_frame(
    frame: pd.DataFrame, *, expected_rows: int = 152_676,
    require_global_sequence: bool = True,
) -> None:
    if list(frame.columns) != list(CONTRACT.column_names) or len(frame) != expected_rows:
        raise BuildError("normalized columns or row count differs")
    if frame[list(CONTRACT.primary_key)].isna().any().any() or frame.duplicated(list(CONTRACT.primary_key)).any():
        raise BuildError("normalized primary key differs")
    if require_global_sequence and frame["source_item_ordinal"].tolist() != list(range(1, len(frame) + 1)):
        raise BuildError("global source ordinal differs")
    required = [column.name for column in CONTRACT.columns if not column.nullable]
    if frame[required].isna().any().any():
        raise BuildError("required normalized value is null")
    if not frame["issue_effective_date_status"].isin(DATE_STATUSES).all():
        raise BuildError("issue date status differs")
    if not frame["listing_date_status"].isin(DATE_STATUSES).all():
        raise BuildError("listing date status differs")
    if not frame["availability_status"].eq(AVAILABILITY).all():
        raise BuildError("availability status differs")


@contextmanager
def _lock(path: Path):
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.write(descriptor, uuid4().hex.encode())
        os.fsync(descriptor)
        yield
    finally:
        os.close(descriptor)
        path.unlink(missing_ok=True)


def build_and_publish(project_root: Path, run_root: Path) -> dict[str, object]:
    project_root = project_root.resolve()
    target = project_root / "data" / "normalized" / DATASET
    state_path = project_root / "data" / "state" / f"{DATASET}.json"
    lock_path = project_root / "data" / "state" / f"{DATASET}.lock"
    marker = project_root / "data" / "state" / f"{DATASET}.transaction.json"
    if marker.exists():
        raise BuildError("unfinished publication marker requires review")
    with _lock(lock_path):
        frame, statistics = normalize_retained_run(project_root, run_root)
        source_before = _source_tree(run_root)
        if target.exists() or state_path.exists():
            if not (target.is_dir() and state_path.is_file()):
                raise BuildError("existing output/state pair is incomplete")
            state = json.loads(state_path.read_text(encoding="utf-8"))
            manifest = _manifest(target, Path("data/normalized") / DATASET)
            if (
                state.get("status") != STATUS or state.get("row_count") != len(frame)
                or state.get("output_manifest") != manifest
                or state.get("source_run_id") != EXPECTED_RUN_ID
            ):
                raise BuildError("existing output/state differs")
            return {"status": "ALREADY_PUBLISHED", "rows": len(frame), "api_calls": 0}
        stage = target.with_name(f".{DATASET}.{uuid4().hex}.stage")
        state_candidate = state_path.with_name(f".{state_path.name}.{uuid4().hex}.stage")
        try:
            write_dataset_atomic(
                frame, stage, CONTRACT,
                lambda value: validate_frame(
                    value, expected_rows=len(value), require_global_sequence=False
                ),
            )
            manifest = _manifest(stage, Path("data/normalized") / DATASET)
            for path in sorted(stage.glob("year=*/data.parquet")):
                if not pq.ParquetFile(path).schema_arrow.equals(contract_arrow_schema(CONTRACT), check_metadata=False):
                    raise BuildError("physical Arrow schema differs")
            state = {
                "dataset": DATASET, "contract_version": CONTRACT.version,
                "status": STATUS, "row_count": len(frame),
                "coverage_start": frame["source_snapshot_date"].min(),
                "coverage_end": frame["source_snapshot_date"].max(),
                "source_run_id": EXPECTED_RUN_ID,
                "source_manifest_sha256": EXPECTED_SOURCE_MANIFEST_SHA256,
                "source_tree_sha256": _canonical_sha(source_before),
                "output_manifest": manifest,
                "output_manifest_sha256": _canonical_sha(manifest),
                "statistics": statistics,
                "historical_publication_timing_known": False,
                "predictive_use": "BLOCKED",
                "api_calls_for_publication": 0,
                "collection_network_calls": 16,
            }
            _atomic_json(state_candidate, state)
            if _source_tree(run_root) != source_before:
                raise BuildError("retained source changed before publication")
            _atomic_json(marker, {
                "phase": "PREPARED", "target": target.relative_to(project_root).as_posix(),
                "state": state_path.relative_to(project_root).as_posix(),
                "output_manifest_sha256": state["output_manifest_sha256"],
                "state_sha256": _sha(state_candidate),
            })
            os.replace(stage, target)
            _atomic_json(marker, {**json.loads(marker.read_text(encoding="utf-8")), "phase": "ROOT_INSTALLED"})
            os.replace(state_candidate, state_path)
            _atomic_json(marker, {**json.loads(marker.read_text(encoding="utf-8")), "phase": "COMMITTED"})
            marker.unlink()
            return {
                "status": "PUBLISHED", "rows": len(frame), "api_calls": 0,
                "coverage_start": state["coverage_start"], "coverage_end": state["coverage_end"],
                "output_manifest_sha256": state["output_manifest_sha256"], **statistics,
            }
        except Exception:
            if target.exists() and not state_path.exists():
                shutil.rmtree(target)
            marker.unlink(missing_ok=True)
            raise
        finally:
            shutil.rmtree(stage, ignore_errors=True)
            state_candidate.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_and_publish(args.project_root, args.run_root), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
