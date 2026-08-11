from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import shutil
import tempfile
from uuid import uuid4

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from stock_data.contracts.kospi200_futures_basis import (
    KR_KOSPI200_FUTURES_NEAREST_LISTED_DAILY,
)
from stock_data.published.kospi200_derivatives_bridge import (
    FUTURES_DATASET as INPUT_BRIDGE_DATASET,
    LEGACY_SEGMENT,
    NIGHT_SESSION,
    OFFICIAL_SEGMENT,
    REGULAR_SESSION,
)


DATASET = "kr_kospi200_futures_nearest_listed_daily"
DATASET_VERSION = 1
LAYER = "derived"
PRIMARY_KEY = ("date", "bridge_segment", "session")
SORT_KEY = PRIMARY_KEY
PARTITION_BY = ("year",)
SELECTION_RULE = "NEAREST_SOURCE_LISTED_MATURITY_MONTH"
EXPIRY_STATUS = "NOT_PROVIDED_BY_SOURCE"
PRICE_UNIT_STATUS = "UNVERIFIED_SOURCE_NATIVE_PRICE_UNIT"
BASIS_AVAILABLE = "SAME_ROW_REGULAR_SESSION_SOURCE_NATIVE_DIFFERENCE"
BASIS_BLOCKED = "SESSION_ALIGNMENT_UNVERIFIED"
PREDICTIVE_USE_STATUS = "END_OF_DAY_T_PLUS_1_ONLY_NO_BACK_ADJUSTMENT_UNITS_UNVERIFIED"
LEGACY_INPUT = "krx_legacy_kospi200_futures_daily"
OFFICIAL_INPUT = "kr_kospi200_futures_daily"
LEGACY_SOURCE = "legacy_stock_investment"
LEGACY_OPERATION = "krx_fut_bydd_trd"
OFFICIAL_SOURCE = "data_go_kr"
OFFICIAL_OPERATION = "GetDerivativeProductInfoService/getStockFuturesPriceInfo"


class KOSPI200FuturesBasisError(RuntimeError):
    pass


def _field(name: str, dtype: pa.DataType, *, nullable: bool = False) -> pa.Field:
    return pa.field(name, dtype, nullable=nullable)


SCHEMA = pa.schema(
    [
        _field("date", pa.date32()),
        _field("bridge_segment", pa.string()),
        _field("session", pa.string()),
        _field("source_session_label", pa.string(), nullable=True),
        _field("source_contract_code", pa.string()),
        _field("source_name", pa.string()),
        _field("maturity_month", pa.string()),
        _field("expiry_date", pa.date32(), nullable=True),
        _field("expiry_status", pa.string()),
        _field("selection_rule", pa.string()),
        _field("contract_transition", pa.bool_()),
        _field("close", pa.float64()),
        _field("settlement_price", pa.float64(), nullable=True),
        _field("spot_value", pa.float64()),
        _field("settlement_basis", pa.float64(), nullable=True),
        _field("basis_status", pa.string()),
        _field("price_unit_status", pa.string()),
        _field("volume", pa.int64(), nullable=True),
        _field("open_interest", pa.int64(), nullable=True),
        _field("source", pa.string()),
        _field("source_operation", pa.string()),
        _field("input_bridge_dataset", pa.string()),
        _field("input_normalized_dataset", pa.string()),
        _field("predictive_use_status", pa.string()),
    ],
    metadata={
        b"dataset": DATASET.encode(),
        b"dataset_version": str(DATASET_VERSION).encode(),
        b"layer": LAYER.encode(),
        b"primary_key": ",".join(PRIMARY_KEY).encode(),
        b"partition_by": ",".join(PARTITION_BY).encode(),
        b"selection_rule": SELECTION_RULE.encode(),
        b"continuous_adjustment": b"none",
        b"expiry_inference": b"none",
    },
)


@dataclass(frozen=True)
class Validation:
    rows: int
    coverage_start: str
    coverage_end: str
    legacy_regular_rows: int
    legacy_night_rows: int
    official_regular_rows: int
    basis_rows: int
    transition_rows: int
    primary_key_duplicates: int
    null_counts: dict[str, int]
    infinity_count: int


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_partitioned(root: Path, columns: list[str]) -> tuple[pd.DataFrame, list[dict]]:
    paths = sorted(root.glob("year=*/data.parquet"))
    if not paths:
        raise FileNotFoundError(f"input partitions missing: {root}")
    frames = []
    manifest = []
    for path in paths:
        table = pq.ParquetFile(path).read(columns=columns)
        frames.append(table.to_pandas())
        manifest.append(
            {"path": str(path.resolve()), "rows": table.num_rows, "sha256": _sha256(path)}
        )
    return pd.concat(frames, ignore_index=True), manifest


def _assert_contract() -> None:
    contract = KR_KOSPI200_FUTURES_NEAREST_LISTED_DAILY
    dtype_names = {
        pa.date32(): "date32",
        pa.string(): "string",
        pa.bool_(): "bool",
        pa.float64(): "float64",
        pa.int64(): "int64",
    }
    actual = tuple((field.name, dtype_names[field.type], field.nullable) for field in SCHEMA)
    expected = tuple((c.name, c.dtype, c.nullable) for c in contract.columns)
    if (
        contract.name != DATASET
        or contract.version != DATASET_VERSION
        or contract.layer != LAYER
        or contract.primary_key != PRIMARY_KEY
        or contract.sort_key != SORT_KEY
        or contract.partition_by != PARTITION_BY
        or actual != expected
    ):
        raise KOSPI200FuturesBasisError("dataset contract differs from Arrow schema")


def _join_source_values(
    bridge: pd.DataFrame, legacy: pd.DataFrame, official: pd.DataFrame
) -> pd.DataFrame:
    if bridge.duplicated(["date", "bridge_segment", "session", "source_contract_code"]).any():
        raise KOSPI200FuturesBasisError("bridge primary key duplicates")
    legacy_bridge = bridge.loc[bridge["bridge_segment"].eq(LEGACY_SEGMENT)].copy()
    official_bridge = bridge.loc[bridge["bridge_segment"].eq(OFFICIAL_SEGMENT)].copy()
    if len(legacy_bridge) + len(official_bridge) != len(bridge):
        raise KOSPI200FuturesBasisError("unknown bridge segment")

    legacy_values = legacy.rename(
        columns={"spot_price": "spot_value", "settlement_price": "source_settlement_price"}
    )
    legacy_joined = legacy_bridge.merge(
        legacy_values,
        left_on=["date", "source_row_no"],
        right_on=["date", "source_file_row_no"],
        how="left",
        validate="one_to_one",
    )
    official_values = official.rename(
        columns={
            "contract": "normalized_contract",
            "underlying_value": "spot_value",
            "settlement_price": "source_settlement_price",
        }
    )
    official_joined = official_bridge.merge(
        official_values,
        left_on=["date", "source_contract_code"],
        right_on=["date", "normalized_contract"],
        how="left",
        validate="one_to_one",
    )
    for label, frame in (("legacy", legacy_joined), ("official", official_joined)):
        if frame[["spot_value", "source_settlement_price"]].isna().all(axis=1).any():
            raise KOSPI200FuturesBasisError(f"{label} normalized source join is incomplete")
    legacy_joined["input_normalized_dataset"] = LEGACY_INPUT
    official_joined["input_normalized_dataset"] = OFFICIAL_INPUT
    return pd.concat([legacy_joined, official_joined], ignore_index=True, sort=False)


def _alternative_rule_audit(frame: pd.DataFrame, nearest: pd.DataFrame) -> dict[str, object]:
    keys = list(PRIMARY_KEY)
    volume = (
        frame.sort_values(
            keys + ["volume", "open_interest", "maturity_month", "source_contract_code"],
            ascending=[True, True, True, False, False, True, True],
            kind="stable",
        )
        .groupby(keys, as_index=False)
        .head(1)
    )
    open_interest = (
        frame.sort_values(
            keys + ["open_interest", "volume", "maturity_month", "source_contract_code"],
            ascending=[True, True, True, False, False, True, True],
            kind="stable",
        )
        .groupby(keys, as_index=False)
        .head(1)
    )
    comparison = nearest[keys + ["source_contract_code"]].rename(
        columns={"source_contract_code": "nearest"}
    )
    comparison = comparison.merge(
        volume[keys + ["source_contract_code"]].rename(
            columns={"source_contract_code": "volume"}
        ),
        on=keys,
        validate="one_to_one",
    ).merge(
        open_interest[keys + ["source_contract_code"]].rename(
            columns={"source_contract_code": "open_interest"}
        ),
        on=keys,
        validate="one_to_one",
    )
    groups = []
    for (segment, session), group in comparison.groupby(
        ["bridge_segment", "session"], sort=True
    ):
        ordered = group.sort_values("date", kind="stable")
        groups.append(
            {
                "bridge_segment": segment,
                "session": session,
                "rows": len(group),
                "volume_disagrees_with_nearest": int(group["volume"].ne(group["nearest"]).sum()),
                "open_interest_disagrees_with_nearest": int(
                    group["open_interest"].ne(group["nearest"]).sum()
                ),
                "nearest_contract_sequences": int(
                    ordered["nearest"].ne(ordered["nearest"].shift()).sum()
                ),
                "volume_contract_sequences": int(
                    ordered["volume"].ne(ordered["volume"].shift()).sum()
                ),
                "open_interest_contract_sequences": int(
                    ordered["open_interest"].ne(ordered["open_interest"].shift()).sum()
                ),
            }
        )
    return {
        "selected_rule": SELECTION_RULE,
        "alternatives": [
            "MAX_DAILY_VOLUME_WITH_DETERMINISTIC_TIES",
            "MAX_DAILY_OPEN_INTEREST_WITH_DETERMINISTIC_TIES",
        ],
        "group_comparison": groups,
        "rejected_calendar_rule": (
            "No exact expiry dates or verified calendar convention are retained; "
            "maturity_month is not converted into an expiry date."
        ),
    }


def _validate_source_rows(frame: pd.DataFrame) -> None:
    required = [
        "date",
        "bridge_segment",
        "session",
        "source_contract_code",
        "source_name",
        "maturity_month",
        "expiry_status",
        "spot_value",
        "source",
        "source_operation",
        "input_normalized_dataset",
    ]
    if frame[required].isna().any().any():
        raise KOSPI200FuturesBasisError("source rows violate non-nullability")

    legacy = frame["bridge_segment"].eq(LEGACY_SEGMENT)
    official = frame["bridge_segment"].eq(OFFICIAL_SEGMENT)
    regular = frame["session"].eq(REGULAR_SESSION)
    night = frame["session"].eq(NIGHT_SESSION)
    allowed = (legacy & (regular | night)) | (official & regular)
    if not allowed.all():
        raise KOSPI200FuturesBasisError("source provider/session matrix differs")

    mappings = (
        (legacy, LEGACY_SOURCE, LEGACY_OPERATION, LEGACY_INPUT),
        (official, OFFICIAL_SOURCE, OFFICIAL_OPERATION, OFFICIAL_INPUT),
    )
    for mask, source, operation, input_dataset in mappings:
        if (
            not frame.loc[mask, "source"].eq(source).all()
            or not frame.loc[mask, "source_operation"].eq(operation).all()
            or not frame.loc[mask, "input_normalized_dataset"].eq(input_dataset).all()
        ):
            raise KOSPI200FuturesBasisError("source provider provenance mapping differs")

    maturity = frame["maturity_month"].astype("string")
    if not maturity.str.fullmatch(r"\d{4}-(?:0[1-9]|1[0-2])").fillna(False).all():
        raise KOSPI200FuturesBasisError("source maturity_month format differs")
    observation_month = pd.to_datetime(frame["date"], errors="raise").dt.strftime("%Y-%m")
    if maturity.lt(observation_month).any():
        raise KOSPI200FuturesBasisError("source maturity precedes observation month")

    if frame.loc[regular, "source_settlement_price"].isna().any():
        raise KOSPI200FuturesBasisError("regular settlement price is null")
    if frame.loc[night, "source_settlement_price"].notna().any():
        raise KOSPI200FuturesBasisError("night settlement price must remain null")


def _derive(joined: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, object]]:
    _validate_source_rows(joined)
    keys = list(PRIMARY_KEY)
    maturity_counts = joined.groupby(keys + ["maturity_month"]).size()
    if (maturity_counts > 1).any():
        raise KOSPI200FuturesBasisError("multiple contracts share a maturity within a source scope")
    nearest = (
        joined.sort_values(keys + ["maturity_month", "source_contract_code"], kind="stable")
        .groupby(keys, as_index=False)
        .head(1)
        .copy()
    )
    if nearest.duplicated(keys).any():
        raise KOSPI200FuturesBasisError("nearest-listed selection is not unique")
    nearest = nearest.sort_values(keys, kind="stable").reset_index(drop=True)
    nearest["contract_transition"] = nearest.groupby(
        ["bridge_segment", "session"], sort=False
    )["source_contract_code"].transform(
        lambda values: values.ne(values.shift()) & values.shift().notna()
    )
    regular = nearest["session"].eq(REGULAR_SESSION)
    night = nearest["session"].eq(NIGHT_SESSION)
    if not (regular | night).all():
        raise KOSPI200FuturesBasisError("unverified source session")
    if nearest.loc[regular, ["source_settlement_price", "spot_value"]].isna().any().any():
        raise KOSPI200FuturesBasisError("regular source basis inputs contain null")
    if nearest.loc[night, "source_settlement_price"].notna().any():
        raise KOSPI200FuturesBasisError("night settlement availability changed")
    result = pd.DataFrame(
        {
            "date": nearest["date"],
            "bridge_segment": nearest["bridge_segment"],
            "session": nearest["session"],
            "source_session_label": nearest["source_session_label"],
            "source_contract_code": nearest["source_contract_code"],
            "source_name": nearest["source_name"],
            "maturity_month": nearest["maturity_month"],
            "expiry_date": pd.Series(pd.NaT, index=nearest.index),
            "expiry_status": EXPIRY_STATUS,
            "selection_rule": SELECTION_RULE,
            "contract_transition": nearest["contract_transition"],
            "close": nearest["close"],
            "settlement_price": nearest["source_settlement_price"],
            "spot_value": nearest["spot_value"],
            "settlement_basis": nearest["source_settlement_price"].sub(nearest["spot_value"]).where(regular),
            "basis_status": regular.map({True: BASIS_AVAILABLE, False: BASIS_BLOCKED}),
            "price_unit_status": PRICE_UNIT_STATUS,
            "volume": nearest["volume"],
            "open_interest": nearest["open_interest"],
            "source": nearest["source"],
            "source_operation": nearest["source_operation"],
            "input_bridge_dataset": INPUT_BRIDGE_DATASET,
            "input_normalized_dataset": nearest["input_normalized_dataset"],
            "predictive_use_status": PREDICTIVE_USE_STATUS,
        },
        columns=SCHEMA.names,
    )
    return result, _alternative_rule_audit(joined, nearest)


def validate(frame: pd.DataFrame) -> Validation:
    if tuple(frame.columns) != tuple(SCHEMA.names) or frame.empty:
        raise KOSPI200FuturesBasisError("output schema or content is empty")
    required = [field.name for field in SCHEMA if not field.nullable]
    if frame[required].isna().any().any():
        raise KOSPI200FuturesBasisError("output violates contract non-nullability")
    duplicates = int(frame.duplicated(list(PRIMARY_KEY), keep=False).sum())
    if duplicates or frame[list(PRIMARY_KEY)].isna().any().any():
        raise KOSPI200FuturesBasisError(f"output primary key invalid: duplicates={duplicates}")
    if list(frame[list(SORT_KEY)].itertuples(index=False, name=None)) != sorted(
        frame[list(SORT_KEY)].itertuples(index=False, name=None)
    ):
        raise KOSPI200FuturesBasisError("output sort key is not monotonic")
    exact = {
        "expiry_status": EXPIRY_STATUS,
        "selection_rule": SELECTION_RULE,
        "price_unit_status": PRICE_UNIT_STATUS,
        "input_bridge_dataset": INPUT_BRIDGE_DATASET,
        "predictive_use_status": PREDICTIVE_USE_STATUS,
    }
    for column, value in exact.items():
        if not frame[column].eq(value).all():
            raise KOSPI200FuturesBasisError(f"output {column} differs")
    if frame["expiry_date"].notna().any():
        raise KOSPI200FuturesBasisError("expiry date was inferred")
    legacy = frame["bridge_segment"].eq(LEGACY_SEGMENT)
    official = frame["bridge_segment"].eq(OFFICIAL_SEGMENT)
    regular = frame["session"].eq(REGULAR_SESSION)
    night = frame["session"].eq(NIGHT_SESSION)
    allowed = (legacy & (regular | night)) | (official & regular)
    if not allowed.all():
        raise KOSPI200FuturesBasisError("output provider/session matrix differs")
    mappings = (
        (legacy, LEGACY_SOURCE, LEGACY_OPERATION, LEGACY_INPUT),
        (official, OFFICIAL_SOURCE, OFFICIAL_OPERATION, OFFICIAL_INPUT),
    )
    for mask, source, operation, input_dataset in mappings:
        if (
            not frame.loc[mask, "source"].eq(source).all()
            or not frame.loc[mask, "source_operation"].eq(operation).all()
            or not frame.loc[mask, "input_normalized_dataset"].eq(input_dataset).all()
        ):
            raise KOSPI200FuturesBasisError("output provider provenance mapping differs")
    maturity = frame["maturity_month"].astype("string")
    if not maturity.str.fullmatch(r"\d{4}-(?:0[1-9]|1[0-2])").fillna(False).all():
        raise KOSPI200FuturesBasisError("output maturity_month format differs")
    observation_month = pd.to_datetime(frame["date"], errors="raise").dt.strftime("%Y-%m")
    if maturity.lt(observation_month).any():
        raise KOSPI200FuturesBasisError("output maturity precedes observation month")
    if frame.loc[regular, "settlement_price"].isna().any():
        raise KOSPI200FuturesBasisError("regular settlement price is null")
    if frame.loc[night, "settlement_price"].notna().any():
        raise KOSPI200FuturesBasisError("night settlement price must remain null")
    if frame.loc[regular, "settlement_basis"].isna().any() or frame.loc[
        night, "settlement_basis"
    ].notna().any():
        raise KOSPI200FuturesBasisError("basis availability differs from session rule")
    expected = frame["settlement_price"].sub(frame["spot_value"])
    if not frame.loc[regular, "settlement_basis"].eq(expected.loc[regular]).all():
        raise KOSPI200FuturesBasisError("settlement basis arithmetic differs")
    if not frame.loc[regular, "basis_status"].eq(BASIS_AVAILABLE).all() or not frame.loc[
        night, "basis_status"
    ].eq(BASIS_BLOCKED).all():
        raise KOSPI200FuturesBasisError("basis status differs")
    numeric = frame[
        ["close", "settlement_price", "spot_value", "settlement_basis", "volume", "open_interest"]
    ]
    infinity = sum(
        int(values.dropna().map(lambda value: not math.isfinite(float(value))).sum())
        for _, values in numeric.items()
    )
    if infinity:
        raise KOSPI200FuturesBasisError(f"output infinity count={infinity}")
    dates = pd.to_datetime(frame["date"], errors="raise")
    return Validation(
        rows=len(frame),
        coverage_start=dates.min().strftime("%Y-%m-%d"),
        coverage_end=dates.max().strftime("%Y-%m-%d"),
        legacy_regular_rows=int(
            (frame["bridge_segment"].eq(LEGACY_SEGMENT) & regular).sum()
        ),
        legacy_night_rows=int((frame["bridge_segment"].eq(LEGACY_SEGMENT) & night).sum()),
        official_regular_rows=int(
            (frame["bridge_segment"].eq(OFFICIAL_SEGMENT) & regular).sum()
        ),
        basis_rows=int(frame["settlement_basis"].notna().sum()),
        transition_rows=int(frame["contract_transition"].sum()),
        primary_key_duplicates=duplicates,
        null_counts={name: int(frame[name].isna().sum()) for name in SCHEMA.names},
        infinity_count=infinity,
    )


def _table(frame: pd.DataFrame) -> pa.Table:
    table = pa.Table.from_pandas(frame[SCHEMA.names], schema=SCHEMA, preserve_index=False, safe=True)
    return table.replace_schema_metadata(SCHEMA.metadata)


def _commit_directory_atomic(stage: Path, target: Path) -> None:
    backup: Path | None = None
    try:
        if target.exists():
            backup = target.parent / f".{target.name}.backup-{uuid4().hex}"
            target.replace(backup)
        stage.replace(target)
    except Exception:
        if target.exists() and backup is not None:
            shutil.rmtree(target)
        if backup is not None and backup.exists():
            backup.replace(target)
        raise
    finally:
        if stage.exists():
            shutil.rmtree(stage)
        if backup is not None and backup.exists():
            shutil.rmtree(backup)


def _write_dataset(frame: pd.DataFrame, output_root: Path) -> list[dict[str, object]]:
    output_root.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output_root.name}.stage-", dir=output_root.parent))
    try:
        years = pd.to_datetime(frame["date"]).dt.year
        for year in sorted(years.unique()):
            partition = frame.loc[years.eq(year)].reset_index(drop=True)
            path = stage / f"year={year}" / "data.parquet"
            path.parent.mkdir(parents=True)
            pq.write_table(_table(partition), path, compression="zstd")
            restored = pq.ParquetFile(path).read()
            if not restored.schema.equals(SCHEMA, check_metadata=True):
                raise KOSPI200FuturesBasisError("staged schema differs")
            validate(restored.to_pandas())
        _commit_directory_atomic(stage, output_root)
    except Exception:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    return [
        {
            "path": str(path.resolve()),
            "rows": pq.ParquetFile(path).metadata.num_rows,
            "sha256": _sha256(path),
        }
        for path in sorted(output_root.glob("year=*/data.parquet"))
    ]


def _write_json_atomic(payload: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", suffix=".json.tmp", prefix=path.stem + "_",
            dir=path.parent, delete=False
        ) as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            temporary = Path(handle.name)
        if json.loads(temporary.read_text(encoding="utf-8")) != payload:
            raise KOSPI200FuturesBasisError("state JSON read-back differs")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def build_kospi200_futures_nearest_listed(
    *,
    bridge_root: Path,
    legacy_root: Path,
    official_root: Path,
    output_root: Path,
    output_state_path: Path,
) -> dict:
    """Build an offline, source-observed nearest-listed series without expiry inference."""

    _assert_contract()
    bridge_columns = [
        "date", "bridge_segment", "session", "source_session_label",
        "source_contract_code", "source_name", "maturity_month", "expiry_date",
        "expiry_status", "close", "volume", "open_interest", "source",
        "source_operation", "source_row_no",
    ]
    bridge, bridge_manifest = _read_partitioned(bridge_root, bridge_columns)
    legacy, legacy_manifest = _read_partitioned(
        legacy_root,
        ["date", "source_file_row_no", "spot_price", "settlement_price"],
    )
    official, official_manifest = _read_partitioned(
        official_root,
        ["date", "contract", "underlying_value", "settlement_price"],
    )
    joined = _join_source_values(bridge, legacy, official)
    result, alternatives = _derive(joined)
    validation = validate(result)
    output_files = _write_dataset(result, output_root)
    payload = {
        "task_id": "C009",
        "status": "complete_with_limits",
        "dataset": DATASET,
        "dataset_version": DATASET_VERSION,
        "layer": LAYER,
        "api_calls": 0,
        "schema": str(SCHEMA),
        "primary_key": list(PRIMARY_KEY),
        "sort_key": list(SORT_KEY),
        "partition_by": list(PARTITION_BY),
        "validation": asdict(validation),
        "roll_rule_audit": alternatives,
        "input_manifests": {
            INPUT_BRIDGE_DATASET: bridge_manifest,
            LEGACY_INPUT: legacy_manifest,
            OFFICIAL_INPUT: official_manifest,
        },
        "output_files": output_files,
        "limitations": [
            "Nearest-listed means minimum retained maturity_month within each date/provider/session scope.",
            "No expiry date, expiry calendar, or pre-expiry roll date is inferred.",
            "Provider and regular/night session boundaries remain separate.",
            "Night settlement basis is null because session alignment is unverified.",
            "Price units remain source-native and unverified; settlement_basis is not labeled as index points.",
            "No back-adjustment or return-continuity transformation is applied.",
        ],
        "failed": {},
        "staged": [],
    }
    _write_json_atomic(payload, output_state_path)
    return payload
