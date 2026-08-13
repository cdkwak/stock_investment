from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
from contextlib import contextmanager
from uuid import uuid4

import pandas as pd
import pyarrow.parquet as pq

from stock_data.contracts.kr_equity import (
    KR_EQUITY_CANONICAL_UNIVERSE_DAILY,
    KR_EQUITY_PRICE_DAILY,
)
from stock_data.contracts.kr_market import KR_MARKET_BREADTH_DAILY
from stock_data.derived.market_breadth import calculate_market_breadth
from stock_data.storage.contract_arrow import (
    contract_arrow_schema,
    dataframe_to_contract_table,
    restore_contract_dates,
)
from stock_data.validation.kr_equity import validate_equity_price
from stock_data.validation.kr_market import validate_market_breadth
from stock_data.published.canonical_equity_universe import validate_canonical_universe


class MarketBreadthRebuildError(RuntimeError):
    pass


DATASET = KR_MARKET_BREADTH_DAILY.name
PRICE_ROOT = Path("data/normalized/kr_equity_price_daily")
UNIVERSE_ROOT = Path("data/published/kr_equity_canonical_universe_daily")
OUTPUT_ROOT = Path("data/derived/kr_market_breadth_daily")
STATE_PATH = Path("data/state/kr_market_breadth_daily_rebuild.json")
MARKER_PATH = Path("data/state/.kr_market_breadth_daily.rebuild.transaction.json")
LOCK_PATH = Path("data/state/.kr_market_breadth_daily.rebuild.lock")
_PHASES = {
    "PREPARED", "ROOT_BACKED_UP", "ROOT_PROMOTED", "STATE_BACKED_UP",
    "STATE_PROMOTED", "VERIFIED",
}


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")


def _write_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(_json_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file_digest(path: Path) -> str | None:
    return _sha256_bytes(path.read_bytes()) if path.is_file() else None


def _manifest_digest(value: list[dict[str, object]]) -> str:
    return _sha256_bytes(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    )


@contextmanager
def _single_writer_lock(project_root: Path):
    path = project_root / LOCK_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    token = uuid4().hex
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    except FileExistsError as error:
        raise MarketBreadthRebuildError("market breadth rebuild lock is active") from error
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_json_bytes({"dataset": DATASET, "token": token}))
            stream.flush()
            os.fsync(stream.fileno())
        yield
    finally:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = None
        if payload == {"dataset": DATASET, "token": token}:
            path.unlink(missing_ok=True)


def _manifest(
    project_root: Path, root: Path, *, logical_root: Path | None = None
) -> list[dict[str, object]]:
    result = []
    for path in sorted(root.rglob("data.parquet")):
        result.append({
            "bytes": path.stat().st_size,
            "path": (
                (logical_root / path.relative_to(root)).as_posix()
                if logical_root is not None
                else path.relative_to(project_root).as_posix()
            ),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
    if not result:
        raise MarketBreadthRebuildError(f"no Parquet inputs: {root}")
    return result


def _partitions(root: Path) -> dict[tuple[str, int], Path]:
    unexpected = [
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name != "data.parquet"
    ]
    if unexpected:
        raise MarketBreadthRebuildError(f"unexpected input files: {unexpected[:3]}")
    result: dict[tuple[str, int], Path] = {}
    for path in sorted(root.rglob("data.parquet")):
        relative = path.relative_to(root)
        if len(relative.parts) != 3:
            raise MarketBreadthRebuildError(f"unexpected partition path: {relative.as_posix()}")
        market_part, year_part, filename = relative.parts
        if (
            market_part not in {"market=KOSPI", "market=KOSDAQ"}
            or not year_part.startswith("year=")
            or filename != "data.parquet"
        ):
            raise MarketBreadthRebuildError(f"unexpected partition path: {relative.as_posix()}")
        try:
            year = int(year_part.removeprefix("year="))
        except ValueError as error:
            raise MarketBreadthRebuildError(
                f"invalid partition year: {relative.as_posix()}"
            ) from error
        key = (market_part.removeprefix("market="), year)
        if key in result:
            raise MarketBreadthRebuildError(f"duplicate partition: {key}")
        result[key] = path
    if not result:
        raise MarketBreadthRebuildError(f"no partitions: {root}")
    return result


def _read_partition(
    path: Path, contract, validator, *, expected_market: str, expected_year: int
) -> pd.DataFrame:
    schema = pq.ParquetFile(path).schema_arrow
    if not schema.equals(contract_arrow_schema(contract), check_metadata=False):
        raise MarketBreadthRebuildError(f"physical schema differs from contract: {path}")
    frame = restore_contract_dates(pd.read_parquet(path), contract)
    frame = frame[list(contract.column_names)].sort_values(
        list(contract.sort_key), kind="stable"
    ).reset_index(drop=True)
    validator(frame)
    if not frame["market"].eq(expected_market).all():
        raise MarketBreadthRebuildError(f"row market differs from partition path: {path}")
    years = pd.to_datetime(frame["date"], errors="raise").dt.year
    if not years.eq(expected_year).all():
        raise MarketBreadthRebuildError(f"row year differs from partition path: {path}")
    return frame


def _semantic_fingerprint(frame: pd.DataFrame) -> str:
    ordered = frame.sort_values(
        list(KR_MARKET_BREADTH_DAILY.primary_key), kind="stable"
    ).reset_index(drop=True)
    payload = ordered.to_csv(index=False, lineterminator="\n").encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _write_rebuild(
    *, project_root: Path, stage_root: Path
) -> tuple[pd.DataFrame, list[dict[str, object]], list[dict[str, object]]]:
    price_root = project_root / PRICE_ROOT
    universe_root = project_root / UNIVERSE_ROOT
    prices = _partitions(price_root)
    universes = _partitions(universe_root)
    if set(prices) != set(universes):
        raise MarketBreadthRebuildError("price and canonical-universe partitions differ")

    price_manifest_before = _manifest(project_root, price_root)
    universe_manifest_before = _manifest(project_root, universe_root)
    outputs = []
    for market in ("KOSDAQ", "KOSPI"):
        previous_close: dict[str, int] = {}
        for key in sorted((key for key in prices if key[0] == market), key=lambda item: item[1]):
            _, year = key
            price = _read_partition(
                prices[key], KR_EQUITY_PRICE_DAILY, validate_equity_price,
                expected_market=market, expected_year=year,
            )
            universe = _read_partition(
                universes[key],
                KR_EQUITY_CANONICAL_UNIVERSE_DAILY,
                validate_canonical_universe,
                expected_market=market,
                expected_year=year,
            )
            working = price.copy()
            first_previous = working["symbol"].map(previous_close)
            shifted = working.groupby("symbol", sort=False)["close"].shift(1)
            working["_previous_close"] = shifted.fillna(first_previous)
            synthetic = working[list(KR_EQUITY_PRICE_DAILY.column_names)].copy()
            # The established calculator owns the semantics. A single carry row per
            # symbol makes partitioned execution identical to one full-history call.
            carry = working.loc[
                working.groupby("symbol", sort=False).cumcount().eq(0)
                & working["_previous_close"].notna()
            ].copy()
            if not carry.empty:
                carry["date"] = "1900-01-01"
                carry["source_date"] = "1900-01-01"
                for column in ("open", "high", "low", "close"):
                    carry[column] = carry["_previous_close"].astype("int64")
                carry = carry[list(KR_EQUITY_PRICE_DAILY.column_names)]
                synthetic = pd.concat([carry, synthetic], ignore_index=True).sort_values(
                    list(KR_EQUITY_PRICE_DAILY.sort_key), kind="stable"
                ).reset_index(drop=True)
            last = price.groupby("symbol", sort=False).tail(1)
            previous_close.update(
                {str(row.symbol): int(row.close) for row in last.itertuples(index=False)}
            )
            if not synthetic.duplicated(["market", "symbol"]).any():
                continue
            breadth = calculate_market_breadth(synthetic, universe)
            breadth = breadth[breadth["date"].str[:4].eq(str(year))].reset_index(drop=True)
            if breadth.empty:
                continue
            validate_market_breadth(breadth)
            target = stage_root / f"market={market}/year={year}/data.parquet"
            target.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(dataframe_to_contract_table(breadth, KR_MARKET_BREADTH_DAILY), target)
            verified = _read_partition(
                target, KR_MARKET_BREADTH_DAILY, validate_market_breadth,
                expected_market=market, expected_year=year,
            )
            if not verified.equals(breadth):
                raise MarketBreadthRebuildError(f"staged output differs: {key}")
            outputs.append(breadth)

    if price_manifest_before != _manifest(project_root, price_root):
        raise MarketBreadthRebuildError("price inputs changed during rebuild")
    if universe_manifest_before != _manifest(project_root, universe_root):
        raise MarketBreadthRebuildError("canonical-universe inputs changed during rebuild")
    result = pd.concat(outputs, ignore_index=True).sort_values(
        list(KR_MARKET_BREADTH_DAILY.sort_key), kind="stable"
    ).reset_index(drop=True)
    validate_market_breadth(result)
    staged_partitions = _partitions(stage_root)
    staged = pd.concat(
        [
            _read_partition(
                staged_partitions[key], KR_MARKET_BREADTH_DAILY,
                validate_market_breadth, expected_market=key[0], expected_year=key[1],
            )
            for key in sorted(staged_partitions)
        ],
        ignore_index=True,
    ).sort_values(list(KR_MARKET_BREADTH_DAILY.sort_key), kind="stable").reset_index(
        drop=True
    )
    if not staged.equals(result):
        raise MarketBreadthRebuildError("complete staged dataset differs from rebuild")
    return result, price_manifest_before, universe_manifest_before


def _verify_existing_preserved(project_root: Path, rebuilt: pd.DataFrame) -> dict[str, object]:
    root = project_root / OUTPUT_ROOT
    if not root.is_dir():
        raise MarketBreadthRebuildError("existing output root is required")
    existing_partitions = _partitions(root)
    frames = [
        _read_partition(
            existing_partitions[key], KR_MARKET_BREADTH_DAILY, validate_market_breadth,
            expected_market=key[0], expected_year=key[1],
        )
        for key in sorted(existing_partitions)
    ]
    existing = pd.concat(frames, ignore_index=True).sort_values(
        list(KR_MARKET_BREADTH_DAILY.sort_key), kind="stable"
    ).reset_index(drop=True)
    validate_market_breadth(existing)
    keys = list(KR_MARKET_BREADTH_DAILY.primary_key)
    comparison = existing.merge(rebuilt, on=keys, how="left", suffixes=("_old", "_new"), indicator=True)
    if not comparison["_merge"].eq("both").all():
        raise MarketBreadthRebuildError("rebuilt output drops existing keys")
    for column in KR_MARKET_BREADTH_DAILY.column_names[2:]:
        if not comparison[f"{column}_old"].eq(comparison[f"{column}_new"]).all():
            raise MarketBreadthRebuildError(f"rebuilt output changes existing {column}")
    return {
        "existing_rows": len(existing),
        "existing_semantic_fingerprint_sha256": _semantic_fingerprint(existing),
    }


def _transaction_paths(project_root: Path, transaction_id: str) -> tuple[Path, Path, Path]:
    return (
        project_root / "data" / f".{DATASET}.rebuild.stage.{transaction_id}",
        project_root / "data/derived" / f".{DATASET}.rebuild.backup.{transaction_id}",
        project_root / "data/state" / f".{STATE_PATH.name}.backup.{transaction_id}",
    )


def _require_path_under_project(path: Path, project_root: Path) -> None:
    resolved_project = project_root.resolve()
    resolved = path.resolve()
    if not resolved.is_relative_to(resolved_project):
        raise MarketBreadthRebuildError("rebuild transaction path escapes project root")


def _orphans(project_root: Path) -> set[Path]:
    data = project_root / "data"
    result = set(data.glob(f".{DATASET}.rebuild.stage.*"))
    result.update((data / "derived").glob(f".{DATASET}.rebuild.backup.*"))
    result.update((data / "state").glob(f".{STATE_PATH.name}.backup.*"))
    result.update((data / "state").glob(f"{MARKER_PATH.name}.*.tmp"))
    return result


def _read_marker(project_root: Path) -> tuple[dict[str, object], Path, Path, Path]:
    marker = project_root / MARKER_PATH
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MarketBreadthRebuildError("invalid rebuild transaction marker JSON") from error
    keys = {
        "version", "dataset", "transaction_id", "phase", "state_existed",
        "stage_relative", "backup_relative", "state_backup_relative",
        "original_output_manifest_sha256", "original_state_sha256",
        "expected_output_manifest_sha256", "expected_state_sha256",
    }
    if not isinstance(payload, dict) or set(payload) != keys:
        raise MarketBreadthRebuildError("invalid rebuild transaction marker schema")
    transaction_id = payload.get("transaction_id")
    if (
        payload.get("version") != 1
        or payload.get("dataset") != DATASET
        or not isinstance(transaction_id, str)
        or len(transaction_id) != 32
        or any(c not in "0123456789abcdef" for c in transaction_id)
        or payload.get("phase") not in _PHASES
        or not isinstance(payload.get("state_existed"), bool)
    ):
        raise MarketBreadthRebuildError("invalid rebuild transaction marker identity")
    stage, backup, state_backup = _transaction_paths(project_root, transaction_id)
    expected_relatives = {
        "stage_relative": stage.relative_to(project_root).as_posix(),
        "backup_relative": backup.relative_to(project_root).as_posix(),
        "state_backup_relative": state_backup.relative_to(project_root).as_posix(),
    }
    if any(payload.get(key) != value for key, value in expected_relatives.items()):
        raise MarketBreadthRebuildError("rebuild transaction marker path is unsafe")
    for path in (stage, backup, state_backup):
        _require_path_under_project(path, project_root)
    for key in (
        "original_output_manifest_sha256", "expected_output_manifest_sha256",
        "expected_state_sha256",
    ):
        value = payload.get(key)
        if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise MarketBreadthRebuildError("invalid rebuild transaction marker digest")
    original_state = payload.get("original_state_sha256")
    if original_state is not None and (
        not isinstance(original_state, str)
        or len(original_state) != 64
        or any(c not in "0123456789abcdef" for c in original_state)
    ):
        raise MarketBreadthRebuildError("invalid rebuild transaction marker state digest")
    if bool(payload["state_existed"]) != (original_state is not None):
        raise MarketBreadthRebuildError("marker state existence contradicts state digest")
    unexpected = _orphans(project_root) - {stage, backup, state_backup}
    if unexpected:
        raise MarketBreadthRebuildError("ambiguous rebuild transaction orphans")
    for path in (stage, backup):
        if path.exists() and not path.is_dir():
            raise MarketBreadthRebuildError("rebuild transaction directory is not a directory")
    if state_backup.exists() and not state_backup.is_file():
        raise MarketBreadthRebuildError("rebuild state backup is not a file")
    return payload, stage, backup, state_backup


def _recover(project_root: Path) -> str:
    marker = project_root / MARKER_PATH
    orphans = _orphans(project_root)
    if not marker.exists():
        if orphans:
            raise MarketBreadthRebuildError("orphan rebuild paths exist without marker")
        return "NONE"
    payload, stage, backup, state_backup = _read_marker(project_root)
    phase = str(payload["phase"])
    root = project_root / OUTPUT_ROOT
    state = project_root / STATE_PATH
    root_exists = root.is_dir()
    stage_exists = stage.is_dir()
    backup_exists = backup.is_dir()
    state_exists = state.is_file()
    state_backup_exists = state_backup.is_file()

    if phase == "VERIFIED":
        if (
            not root_exists
            or not state_exists
            or not backup_exists
            or not stage_exists
            or state_backup_exists != bool(payload["state_existed"])
        ):
            raise MarketBreadthRebuildError("verified transaction artifacts are incomplete")
        if _manifest_digest(_manifest(project_root, root)) != payload["expected_output_manifest_sha256"]:
            raise MarketBreadthRebuildError("verified output digest differs; backups retained")
        if _file_digest(state) != payload["expected_state_sha256"]:
            raise MarketBreadthRebuildError("verified state digest differs; backups retained")
        if _manifest_digest(_manifest(project_root, backup, logical_root=OUTPUT_ROOT)) != payload[
            "original_output_manifest_sha256"
        ]:
            raise MarketBreadthRebuildError("verified output backup digest differs")
        if state_backup_exists and _file_digest(state_backup) != payload["original_state_sha256"]:
            raise MarketBreadthRebuildError("verified state backup digest differs")
        shutil.rmtree(backup)
        state_backup.unlink(missing_ok=True)
        shutil.rmtree(stage, ignore_errors=True)
        marker.unlink()
        return "FINALIZED"

    if not stage_exists:
        raise MarketBreadthRebuildError("unverified transaction stage is missing")
    if backup_exists:
        if _manifest_digest(_manifest(project_root, backup, logical_root=OUTPUT_ROOT)) != payload[
            "original_output_manifest_sha256"
        ]:
            raise MarketBreadthRebuildError("output backup digest differs")
        if root_exists:
            shutil.rmtree(root)
    else:
        if not root_exists or _manifest_digest(_manifest(project_root, root)) != payload[
            "original_output_manifest_sha256"
        ]:
            raise MarketBreadthRebuildError("original output is unavailable")
    if backup_exists:
        backup.replace(root)
    if state_backup_exists:
        if _file_digest(state_backup) != payload["original_state_sha256"]:
            raise MarketBreadthRebuildError("state backup digest differs")
        state.unlink(missing_ok=True)
        state_backup.replace(state)
    elif payload["state_existed"]:
        if not state_exists or _file_digest(state) != payload["original_state_sha256"]:
            raise MarketBreadthRebuildError("original state is unavailable")
    else:
        state.unlink(missing_ok=True)
    shutil.rmtree(stage)
    marker.unlink()
    return "ROLLED_BACK"


def _rebuild_market_breadth_locked(
    *, project_root: Path, mode: str, confirmation: str | None = None
) -> dict[str, object]:
    if mode not in {"dry-run", "apply"}:
        raise MarketBreadthRebuildError(f"unsupported mode: {mode}")
    if mode == "apply" and confirmation != DATASET:
        raise MarketBreadthRebuildError("apply requires exact dataset confirmation")
    project_root = project_root.resolve()
    recovery = _recover(project_root)
    original_output_manifest = _manifest(project_root, project_root / OUTPUT_ROOT)
    original_output_digest = _manifest_digest(original_output_manifest)
    state_path = project_root / STATE_PATH
    original_state_digest = _file_digest(state_path)
    transaction_id = uuid4().hex
    stage = project_root / "data" / f".{DATASET}.rebuild.stage.{transaction_id}"
    stage_root = stage / DATASET
    try:
        rebuilt, price_manifest, universe_manifest = _write_rebuild(
            project_root=project_root, stage_root=stage_root
        )
        preservation = _verify_existing_preserved(project_root, rebuilt)
        output_manifest = _manifest(
            project_root, stage_root, logical_root=OUTPUT_ROOT
        )
        state_payload = {
            "algorithm": "consecutive_close_canonical_membership_v1",
            "api_calls": 0,
            "coverage_end": str(rebuilt["date"].max()),
            "coverage_start": str(rebuilt["date"].min()),
            "dataset": DATASET,
            "dataset_contract_version": KR_MARKET_BREADTH_DAILY.version,
            "input_manifests": {
                KR_EQUITY_PRICE_DAILY.name: price_manifest,
                KR_EQUITY_CANONICAL_UNIVERSE_DAILY.name: universe_manifest,
            },
            "input_contract_versions": {
                KR_EQUITY_PRICE_DAILY.name: KR_EQUITY_PRICE_DAILY.version,
                KR_EQUITY_CANONICAL_UNIVERSE_DAILY.name:
                    KR_EQUITY_CANONICAL_UNIVERSE_DAILY.version,
            },
            "output_manifest": output_manifest,
            "rows": len(rebuilt),
            "semantic_fingerprint_sha256": _semantic_fingerprint(rebuilt),
            "existing_values_preserved": preservation,
        }
        (stage / "state.json").write_bytes(_json_bytes(state_payload))
        result = {"mode": mode, "status": "DRY_RUN_PASS", "startup_recovery": recovery,
                  "state": state_payload}
        if mode == "dry-run":
            return result

        if price_manifest != _manifest(project_root, project_root / PRICE_ROOT):
            raise MarketBreadthRebuildError("price inputs changed before promotion")
        if universe_manifest != _manifest(project_root, project_root / UNIVERSE_ROOT):
            raise MarketBreadthRebuildError(
                "canonical-universe inputs changed before promotion"
            )
        if original_output_digest != _manifest_digest(
            _manifest(project_root, project_root / OUTPUT_ROOT)
        ):
            raise MarketBreadthRebuildError("existing output changed before promotion")
        if original_state_digest != _file_digest(state_path):
            raise MarketBreadthRebuildError("existing state changed before promotion")

        root = project_root / OUTPUT_ROOT
        state = project_root / STATE_PATH
        marker = project_root / MARKER_PATH
        backup = root.parent / f".{DATASET}.rebuild.backup.{transaction_id}"
        state_backup = state.parent / f".{state.name}.backup.{transaction_id}"
        marker_payload = {
            "version": 1,
            "dataset": DATASET,
            "transaction_id": transaction_id,
            "phase": "PREPARED",
            "state_existed": state.exists(),
            "stage_relative": stage.relative_to(project_root).as_posix(),
            "backup_relative": backup.relative_to(project_root).as_posix(),
            "state_backup_relative": state_backup.relative_to(project_root).as_posix(),
            "original_output_manifest_sha256": original_output_digest,
            "original_state_sha256": original_state_digest,
            "expected_output_manifest_sha256": _manifest_digest(output_manifest),
            "expected_state_sha256": _sha256_bytes(_json_bytes(state_payload)),
        }
        _write_atomic(marker, marker_payload)
        marker_payload["phase"] = "ROOT_BACKED_UP"
        _write_atomic(marker, marker_payload)
        root.replace(backup)
        marker_payload["phase"] = "ROOT_PROMOTED"
        _write_atomic(marker, marker_payload)
        stage_root.replace(root)
        marker_payload["phase"] = "STATE_BACKED_UP"
        _write_atomic(marker, marker_payload)
        if state.exists():
            state.replace(state_backup)
        marker_payload["phase"] = "STATE_PROMOTED"
        _write_atomic(marker, marker_payload)
        (stage / "state.json").replace(state)
        if _manifest(project_root, root) != output_manifest:
            raise MarketBreadthRebuildError("promoted output differs from staged manifest")
        if json.loads(state.read_text(encoding="utf-8")) != state_payload:
            raise MarketBreadthRebuildError("promoted state differs from staged state")
        marker_payload["phase"] = "VERIFIED"
        _write_atomic(marker, marker_payload)
        _recover(project_root)
        result["status"] = "REBUILT"
        return result
    except Exception:
        if (project_root / MARKER_PATH).exists():
            _recover(project_root)
        raise
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def rebuild_market_breadth(
    *, project_root: Path, mode: str, confirmation: str | None = None
) -> dict[str, object]:
    project_root = project_root.resolve()
    with _single_writer_lock(project_root):
        return _rebuild_market_breadth_locked(
            project_root=project_root, mode=mode, confirmation=confirmation
        )
