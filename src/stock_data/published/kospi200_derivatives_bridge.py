from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import tempfile
from uuid import uuid4

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


FUTURES_DATASET = "kr_kospi200_futures_provider_bridge_daily"
OPTIONS_DATASET = "kr_kospi200_options_provider_bridge_daily"
DATASET_VERSION = 1
LAYER = "published"
PRIMARY_KEY = ("date", "bridge_segment", "session", "source_contract_code")
SORT_KEY = PRIMARY_KEY
PARTITION_BY = ("year",)

LEGACY_SEGMENT = "LEGACY_KRX_OPENAPI_2010_2019"
OFFICIAL_SEGMENT = "OFFICIAL_DATA_GO_KR_2020_PRESENT"
REGULAR_SESSION = "REGULAR_DAY"
NIGHT_SESSION = "NIGHT"
UNSPECIFIED_SESSION = "UNSPECIFIED_BY_SOURCE"
UNDERLYING = "KOSPI200"
EXPIRY_STATUS = "NOT_PROVIDED_BY_SOURCE"
UNVERIFIED_UNIT = "UNVERIFIED_SOURCE_UNIT"
CONTRACT_UNIT = "CONTRACTS"
INDEX_POINT_UNIT = "INDEX_POINTS"
PREDICTIVE_USE_STATUS = "CONTRACT_ROWS_ONLY_NO_CONTINUOUS_ROLL"

LEGACY_SOURCE = "legacy_stock_investment"
LEGACY_FUTURES_OPERATION = "krx_fut_bydd_trd"
LEGACY_OPTIONS_OPERATION = "krx_opt_bydd_trd"
OFFICIAL_SOURCE = "data_go_kr"
OFFICIAL_FUTURES_OPERATION = (
    "GetDerivativeProductInfoService/getStockFuturesPriceInfo"
)
OFFICIAL_OPTIONS_OPERATION = "GetDerivativeProductInfoService/getOptionsPriceInfo"
LEGACY_FUTURES_INPUT = "krx_legacy_kospi200_futures_daily"
LEGACY_OPTIONS_INPUT = "krx_legacy_kospi200_options_daily"
OFFICIAL_FUTURES_INPUT = "kr_kospi200_futures_daily"
OFFICIAL_OPTIONS_INPUT = "kr_kospi200_options_daily"


class KOSPI200DerivativesBridgeError(RuntimeError):
    pass


def _field(name: str, dtype: pa.DataType, *, nullable: bool = False) -> pa.Field:
    return pa.field(name, dtype, nullable=nullable)


COMMON_FIELDS = (
    _field("date", pa.date32()),
    _field("bridge_segment", pa.string()),
    _field("session", pa.string()),
    _field("source_session_label", pa.string(), nullable=True),
    _field("underlying", pa.string()),
    _field("instrument_type", pa.string()),
    _field("source_contract_code", pa.string()),
    _field("isin", pa.string(), nullable=True),
    _field("source_name", pa.string()),
    _field("source_product_label", pa.string()),
    _field("maturity_month", pa.string()),
    _field("expiry_date", pa.date32(), nullable=True),
    _field("expiry_status", pa.string()),
)
MARKET_FIELDS = (
    _field("open", pa.float64(), nullable=True),
    _field("high", pa.float64(), nullable=True),
    _field("low", pa.float64(), nullable=True),
    _field("close", pa.float64(), nullable=True),
    _field("volume", pa.int64(), nullable=True),
    _field("open_interest", pa.int64(), nullable=True),
    _field("price_unit_status", pa.string()),
    _field("volume_unit_status", pa.string()),
    _field("open_interest_unit_status", pa.string()),
    _field("source", pa.string()),
    _field("source_operation", pa.string()),
    _field("input_dataset", pa.string()),
    _field("source_row_no", pa.int64(), nullable=True),
    _field("predictive_use_status", pa.string()),
)


def _schema(dataset: str, fields: tuple[pa.Field, ...]) -> pa.Schema:
    return pa.schema(
        fields,
        metadata={
            b"dataset": dataset.encode(),
            b"dataset_version": str(DATASET_VERSION).encode(),
            b"layer": LAYER.encode(),
            b"primary_key": ",".join(PRIMARY_KEY).encode(),
            b"partition_by": ",".join(PARTITION_BY).encode(),
            b"continuous_roll_rule": b"none",
        },
    )


FUTURES_SCHEMA = _schema(FUTURES_DATASET, COMMON_FIELDS + MARKET_FIELDS)
OPTIONS_SCHEMA = _schema(
    OPTIONS_DATASET,
    COMMON_FIELDS
    + (
        _field("call_put", pa.string()),
        _field("strike", pa.float64()),
        _field("strike_unit_status", pa.string()),
    )
    + MARKET_FIELDS,
)


@dataclass(frozen=True)
class BridgeValidation:
    rows: int
    legacy_rows: int
    official_rows: int
    coverage_start: str
    coverage_end: str
    primary_key_duplicates: int
    null_counts: dict[str, int]
    infinity_count: int
    all_null_ohlc_rows: int


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _maturity(values: pd.Series, pattern: str) -> pd.Series:
    raw = values.str.extract(pattern, expand=False)
    if raw.isna().any():
        raise KOSPI200DerivativesBridgeError("source name maturity parsing differs")
    return raw.str[:4] + "-" + raw.str[4:]


def _common(
    frame: pd.DataFrame,
    *,
    segment: str,
    session: pd.Series,
    source_session_label: pd.Series,
    instrument_type: str,
    source_product_label: pd.Series,
    maturity_month: pd.Series,
    isin: pd.Series,
    source_row_no: pd.Series,
    input_dataset: str,
    unit_status: str,
) -> dict[str, pd.Series | str]:
    return {
        "date": frame["date"],
        "bridge_segment": segment,
        "session": session,
        "source_session_label": source_session_label,
        "underlying": UNDERLYING,
        "instrument_type": instrument_type,
        "source_contract_code": frame["contract"].astype("string"),
        "isin": isin,
        "source_name": frame["name"].astype("string"),
        "source_product_label": source_product_label,
        "maturity_month": maturity_month,
        "expiry_date": pd.Series(pd.NaT, index=frame.index),
        "expiry_status": EXPIRY_STATUS,
        "open": frame["open"],
        "high": frame["high"],
        "low": frame["low"],
        "close": frame["close"],
        "volume": frame["volume"],
        "open_interest": frame["open_interest"],
        "price_unit_status": UNVERIFIED_UNIT,
        "volume_unit_status": unit_status,
        "open_interest_unit_status": unit_status,
        "source": frame["source"].astype("string"),
        "source_operation": frame["source_operation"].astype("string"),
        "input_dataset": input_dataset,
        "source_row_no": source_row_no,
        "predictive_use_status": PREDICTIVE_USE_STATUS,
    }


def _legacy_futures(frame: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    if not frame["source"].eq(LEGACY_SOURCE).all() or not frame[
        "source_operation"
    ].eq(LEGACY_FUTURES_OPERATION).all():
        raise KOSPI200DerivativesBridgeError("legacy futures provenance differs")
    outright = frame["name"].str.contains(" F ", regex=False)
    spread = frame["name"].str.contains(" SP ", regex=False)
    if not (outright ^ spread).all():
        raise KOSPI200DerivativesBridgeError("legacy future product identity differs")
    selected = frame.loc[outright].copy()
    if not selected["contract"].str.startswith("101").all():
        raise KOSPI200DerivativesBridgeError("legacy outright contract code differs")
    session_map = {"정규": REGULAR_SESSION, "야간": NIGHT_SESSION}
    session = selected["market_name"].map(session_map)
    if session.isna().any():
        raise KOSPI200DerivativesBridgeError("legacy future session differs")
    data = _common(
        selected,
        segment=LEGACY_SEGMENT,
        session=session,
        source_session_label=selected["market_name"].astype("string"),
        instrument_type="FUTURE",
        source_product_label=selected["product_name"].astype("string"),
        maturity_month=_maturity(selected["name"], r"\sF\s(\d{6})(?:\s|$)"),
        isin=pd.Series(pd.NA, index=selected.index, dtype="string"),
        source_row_no=selected["source_file_row_no"],
        input_dataset=LEGACY_FUTURES_INPUT,
        unit_status=UNVERIFIED_UNIT,
    )
    return pd.DataFrame(data, columns=FUTURES_SCHEMA.names), int(spread.sum())


def _official_futures(frame: pd.DataFrame) -> pd.DataFrame:
    if not frame["source"].eq(OFFICIAL_SOURCE).all() or not frame[
        "source_operation"
    ].eq(OFFICIAL_FUTURES_OPERATION).all():
        raise KOSPI200DerivativesBridgeError("official futures provenance differs")
    if not frame["underlying"].eq(UNDERLYING).all():
        raise KOSPI200DerivativesBridgeError("official futures underlying differs")
    if not frame["product_category"].str.contains("(주간)", regex=False).all():
        raise KOSPI200DerivativesBridgeError("official future session unavailable")
    maturity = _maturity(frame["name"], r"\sF\s(\d{6})$")
    if not maturity.eq(frame["maturity_month"]).all():
        raise KOSPI200DerivativesBridgeError("official future maturity differs")
    data = _common(
        frame,
        segment=OFFICIAL_SEGMENT,
        session=pd.Series(REGULAR_SESSION, index=frame.index),
        source_session_label=frame["product_category"].astype("string"),
        instrument_type="FUTURE",
        source_product_label=frame["product_category"].astype("string"),
        maturity_month=maturity,
        isin=frame["isin"].astype("string"),
        source_row_no=pd.Series(pd.NA, index=frame.index, dtype="Int64"),
        input_dataset=OFFICIAL_FUTURES_INPUT,
        unit_status=CONTRACT_UNIT,
    )
    return pd.DataFrame(data, columns=FUTURES_SCHEMA.names)


OPTION_NAME_PATTERN = (
    r"\s+([CP])\s+(\d{6})\s+([0-9,]+(?:\.[0-9]+)?)(?:\s|$)"
)


def _option_identity(frame: pd.DataFrame) -> pd.DataFrame:
    parsed = frame["name"].str.extract(OPTION_NAME_PATTERN)
    if parsed.isna().any(axis=None):
        raise KOSPI200DerivativesBridgeError("option source name parsing differs")
    parsed.columns = ["side_token", "maturity", "strike"]
    return parsed


def _legacy_options(frame: pd.DataFrame) -> pd.DataFrame:
    if not frame["source"].eq(LEGACY_SOURCE).all() or not frame[
        "source_operation"
    ].eq(LEGACY_OPTIONS_OPERATION).all():
        raise KOSPI200DerivativesBridgeError("legacy options provenance differs")
    parsed = _option_identity(frame)
    side = parsed["side_token"].map({"C": "CALL", "P": "PUT"})
    if not side.eq(frame["right_type"]).all():
        raise KOSPI200DerivativesBridgeError("legacy option call-put differs")
    if not frame["name"].str.endswith("(정규)").all():
        raise KOSPI200DerivativesBridgeError("legacy option session differs")
    maturity = parsed["maturity"].str[:4] + "-" + parsed["maturity"].str[4:]
    data = _common(
        frame,
        segment=LEGACY_SEGMENT,
        session=pd.Series(REGULAR_SESSION, index=frame.index),
        source_session_label=pd.Series("정규", index=frame.index),
        instrument_type="OPTION",
        source_product_label=frame["product_name"].astype("string"),
        maturity_month=maturity,
        isin=pd.Series(pd.NA, index=frame.index, dtype="string"),
        source_row_no=frame["source_row_no"],
        input_dataset=LEGACY_OPTIONS_INPUT,
        unit_status=UNVERIFIED_UNIT,
    )
    data.update(
        {
            "call_put": side,
            "strike": pd.to_numeric(parsed["strike"].str.replace(",", "")),
            "strike_unit_status": UNVERIFIED_UNIT,
        }
    )
    return pd.DataFrame(data, columns=OPTIONS_SCHEMA.names)


def _official_options(frame: pd.DataFrame) -> pd.DataFrame:
    if not frame["source"].eq(OFFICIAL_SOURCE).all() or not frame[
        "source_operation"
    ].eq(OFFICIAL_OPTIONS_OPERATION).all():
        raise KOSPI200DerivativesBridgeError("official options provenance differs")
    if not frame["underlying"].eq(UNDERLYING).all():
        raise KOSPI200DerivativesBridgeError("official options underlying differs")
    parsed = _option_identity(frame)
    side = parsed["side_token"].map({"C": "CALL", "P": "PUT"})
    maturity = parsed["maturity"].str[:4] + "-" + parsed["maturity"].str[4:]
    strike = pd.to_numeric(parsed["strike"].str.replace(",", ""))
    if (
        not side.eq(frame["call_put"]).all()
        or not maturity.eq(frame["maturity_month"]).all()
        or not strike.eq(frame["strike"]).all()
    ):
        raise KOSPI200DerivativesBridgeError("official option identity differs")
    data = _common(
        frame,
        segment=OFFICIAL_SEGMENT,
        session=pd.Series(UNSPECIFIED_SESSION, index=frame.index),
        source_session_label=pd.Series(pd.NA, index=frame.index, dtype="string"),
        instrument_type="OPTION",
        source_product_label=frame["product_category"].astype("string"),
        maturity_month=maturity,
        isin=frame["isin"].astype("string"),
        source_row_no=pd.Series(pd.NA, index=frame.index, dtype="Int64"),
        input_dataset=OFFICIAL_OPTIONS_INPUT,
        unit_status=CONTRACT_UNIT,
    )
    data.update(
        {
            "call_put": side,
            "strike": strike,
            "strike_unit_status": INDEX_POINT_UNIT,
        }
    )
    return pd.DataFrame(data, columns=OPTIONS_SCHEMA.names)


def _table(frame: pd.DataFrame, schema: pa.Schema) -> pa.Table:
    table = pa.Table.from_pandas(
        frame[schema.names], schema=schema, preserve_index=False, safe=True
    )
    return table.replace_schema_metadata(schema.metadata)


def validate_bridge(frame: pd.DataFrame, schema: pa.Schema) -> BridgeValidation:
    if tuple(frame.columns) != tuple(schema.names) or frame.empty:
        raise KOSPI200DerivativesBridgeError("bridge schema or content is empty")
    duplicates = int(frame.duplicated(list(PRIMARY_KEY), keep=False).sum())
    if duplicates or frame[list(PRIMARY_KEY)].isna().any().any():
        raise KOSPI200DerivativesBridgeError(
            f"bridge primary key invalid: duplicates={duplicates}"
        )
    ordered = list(frame[list(SORT_KEY)].itertuples(index=False, name=None))
    if ordered != sorted(ordered):
        raise KOSPI200DerivativesBridgeError("bridge sort key is not monotonic")
    if not frame["underlying"].eq(UNDERLYING).all():
        raise KOSPI200DerivativesBridgeError("bridge underlying differs")
    if not frame["expiry_date"].isna().all() or not frame["expiry_status"].eq(
        EXPIRY_STATUS
    ).all():
        raise KOSPI200DerivativesBridgeError("bridge expiry representation differs")
    if not frame["maturity_month"].str.fullmatch(r"\d{4}-\d{2}").all():
        raise KOSPI200DerivativesBridgeError("bridge maturity format differs")
    if not frame["predictive_use_status"].eq(PREDICTIVE_USE_STATUS).all():
        raise KOSPI200DerivativesBridgeError("bridge predictive-use marker differs")
    legacy = frame["bridge_segment"].eq(LEGACY_SEGMENT)
    official = frame["bridge_segment"].eq(OFFICIAL_SEGMENT)
    if not (legacy | official).all():
        raise KOSPI200DerivativesBridgeError("bridge segment differs")
    if not frame.loc[legacy, "source"].eq(LEGACY_SOURCE).all() or not frame.loc[
        official, "source"
    ].eq(OFFICIAL_SOURCE).all():
        raise KOSPI200DerivativesBridgeError("bridge source differs")
    if not frame.loc[legacy, "isin"].isna().all() or frame.loc[
        legacy, "source_row_no"
    ].isna().any():
        raise KOSPI200DerivativesBridgeError("legacy bridge identity differs")
    if frame.loc[official, "isin"].isna().any() or not frame.loc[
        official, "source_row_no"
    ].isna().all():
        raise KOSPI200DerivativesBridgeError("official bridge identity differs")
    if not frame.loc[legacy, "volume_unit_status"].eq(UNVERIFIED_UNIT).all():
        raise KOSPI200DerivativesBridgeError("legacy bridge unit status differs")
    if not frame.loc[official, "volume_unit_status"].eq(CONTRACT_UNIT).all():
        raise KOSPI200DerivativesBridgeError("official bridge unit status differs")
    if not frame["price_unit_status"].eq(UNVERIFIED_UNIT).all():
        raise KOSPI200DerivativesBridgeError("bridge price unit status differs")
    if not frame["open_interest_unit_status"].eq(
        frame["volume_unit_status"]
    ).all():
        raise KOSPI200DerivativesBridgeError("bridge activity unit statuses differ")

    ohlc = frame[["open", "high", "low", "close"]]
    partial = ohlc.isna().any(axis=1) & ~ohlc.isna().all(axis=1)
    if partial.any():
        raise KOSPI200DerivativesBridgeError("partial-null bridge OHLC")
    available = ~ohlc.isna().all(axis=1)
    bad_ohlc = (
        frame.loc[available, "high"]
        < frame.loc[available, ["open", "low", "close"]].max(axis=1)
    ) | (
        frame.loc[available, "low"]
        > frame.loc[available, ["open", "high", "close"]].min(axis=1)
    )
    if bad_ohlc.any():
        raise KOSPI200DerivativesBridgeError("bridge OHLC coherence differs")
    for column in ("volume", "open_interest"):
        if frame[column].dropna().lt(0).any():
            raise KOSPI200DerivativesBridgeError(f"negative bridge {column}")
    if schema is FUTURES_SCHEMA:
        if not frame["instrument_type"].eq("FUTURE").all():
            raise KOSPI200DerivativesBridgeError("future instrument type differs")
        allowed = legacy & frame["session"].isin((REGULAR_SESSION, NIGHT_SESSION))
        allowed |= official & frame["session"].eq(REGULAR_SESSION)
        if not allowed.all():
            raise KOSPI200DerivativesBridgeError("future bridge session differs")
        expected_operations = frame["bridge_segment"].map(
            {
                LEGACY_SEGMENT: LEGACY_FUTURES_OPERATION,
                OFFICIAL_SEGMENT: OFFICIAL_FUTURES_OPERATION,
            }
        )
        expected_inputs = frame["bridge_segment"].map(
            {
                LEGACY_SEGMENT: LEGACY_FUTURES_INPUT,
                OFFICIAL_SEGMENT: OFFICIAL_FUTURES_INPUT,
            }
        )
    else:
        if not frame["instrument_type"].eq("OPTION").all():
            raise KOSPI200DerivativesBridgeError("option instrument type differs")
        allowed = legacy & frame["session"].eq(REGULAR_SESSION)
        allowed |= official & frame["session"].eq(UNSPECIFIED_SESSION)
        if not allowed.all() or not frame["call_put"].isin(("CALL", "PUT")).all():
            raise KOSPI200DerivativesBridgeError("option bridge identity differs")
        if frame["strike"].isna().any() or frame["strike"].lt(0).any():
            raise KOSPI200DerivativesBridgeError("option bridge strike differs")
        strike_units = frame["bridge_segment"].map(
            {LEGACY_SEGMENT: UNVERIFIED_UNIT, OFFICIAL_SEGMENT: INDEX_POINT_UNIT}
        )
        if not frame["strike_unit_status"].eq(strike_units).all():
            raise KOSPI200DerivativesBridgeError("option strike unit status differs")
        expected_operations = frame["bridge_segment"].map(
            {
                LEGACY_SEGMENT: LEGACY_OPTIONS_OPERATION,
                OFFICIAL_SEGMENT: OFFICIAL_OPTIONS_OPERATION,
            }
        )
        expected_inputs = frame["bridge_segment"].map(
            {
                LEGACY_SEGMENT: LEGACY_OPTIONS_INPUT,
                OFFICIAL_SEGMENT: OFFICIAL_OPTIONS_INPUT,
            }
        )
    if not frame["source_operation"].eq(expected_operations).all() or not frame[
        "input_dataset"
    ].eq(expected_inputs).all():
        raise KOSPI200DerivativesBridgeError("bridge provenance mapping differs")
    numeric = frame.select_dtypes(include="number")
    infinity = sum(
        int(series.dropna().map(lambda value: not math.isfinite(float(value))).sum())
        for _, series in numeric.items()
    )
    if infinity:
        raise KOSPI200DerivativesBridgeError(f"bridge infinity count={infinity}")
    dates = pd.to_datetime(frame["date"])
    return BridgeValidation(
        rows=len(frame),
        legacy_rows=int(legacy.sum()),
        official_rows=int(official.sum()),
        coverage_start=dates.min().strftime("%Y-%m-%d"),
        coverage_end=dates.max().strftime("%Y-%m-%d"),
        primary_key_duplicates=duplicates,
        null_counts={name: int(frame[name].isna().sum()) for name in schema.names},
        infinity_count=infinity,
        all_null_ohlc_rows=int(ohlc.isna().all(axis=1).sum()),
    )


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


def _write_state_atomic(payload: dict, path: Path) -> None:
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
            raise KOSPI200DerivativesBridgeError("bridge state read-back differs")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _input_manifest(paths: list[Path]) -> list[dict[str, object]]:
    return [
        {
            "path": str(path.resolve()),
            "rows": pq.ParquetFile(path).metadata.num_rows,
            "sha256": _sha256(path),
        }
        for path in paths
    ]


def _build_dataset(
    *,
    legacy_root: Path,
    official_root: Path,
    stage_root: Path,
    dataset: str,
    schema: pa.Schema,
    legacy_transform,
    official_transform,
) -> tuple[dict, list[dict[str, object]], list[dict[str, object]], int]:
    legacy_paths = sorted(legacy_root.glob("year=*/data.parquet"))
    official_paths = sorted(official_root.glob("year=*/data.parquet"))
    if not legacy_paths or not official_paths:
        raise FileNotFoundError(f"bridge inputs missing for {dataset}")
    if {path.parent.name for path in legacy_paths}.intersection(
        {path.parent.name for path in official_paths}
    ):
        raise KOSPI200DerivativesBridgeError("bridge provider partition years overlap")
    validations: list[BridgeValidation] = []
    output_manifest: list[dict[str, object]] = []
    excluded_spreads = 0
    for segment, paths, transform in (
        (LEGACY_SEGMENT, legacy_paths, legacy_transform),
        (OFFICIAL_SEGMENT, official_paths, official_transform),
    ):
        for input_path in paths:
            source = pq.ParquetFile(input_path).read().to_pandas()
            transformed = transform(source)
            if isinstance(transformed, tuple):
                transformed, excluded = transformed
                excluded_spreads += excluded
            transformed = transformed.sort_values(list(SORT_KEY), kind="stable").reset_index(drop=True)
            validation = validate_bridge(transformed, schema)
            if (segment == LEGACY_SEGMENT and validation.official_rows) or (
                segment == OFFICIAL_SEGMENT and validation.legacy_rows
            ):
                raise KOSPI200DerivativesBridgeError("bridge segment transform differs")
            validations.append(validation)
            output_path = stage_root / dataset / input_path.parent.name / "data.parquet"
            output_path.parent.mkdir(parents=True)
            pq.write_table(_table(transformed, schema), output_path, compression="zstd")
            restored = pq.ParquetFile(output_path).read()
            if not restored.schema.equals(schema, check_metadata=True):
                raise KOSPI200DerivativesBridgeError("bridge staged schema differs")
            restored_frame = restored.to_pandas()
            validate_bridge(restored_frame, schema)
            if not _table(restored_frame, schema).equals(_table(transformed, schema)):
                raise KOSPI200DerivativesBridgeError("bridge staged values differ")
            output_manifest.append(
                {
                    "relative_path": str(output_path.relative_to(stage_root)),
                    "rows": len(transformed),
                    "sha256": _sha256(output_path),
                }
            )
    rows = sum(value.rows for value in validations)
    legacy_rows = sum(value.legacy_rows for value in validations)
    official_rows = sum(value.official_rows for value in validations)
    null_counts = {
        name: sum(value.null_counts[name] for value in validations)
        for name in schema.names
    }
    validation = {
        "rows": rows,
        "legacy_rows": legacy_rows,
        "official_rows": official_rows,
        "coverage_start": min(value.coverage_start for value in validations),
        "coverage_end": max(value.coverage_end for value in validations),
        "primary_key_duplicates": 0,
        "null_counts": null_counts,
        "infinity_count": sum(value.infinity_count for value in validations),
        "all_null_ohlc_rows": sum(value.all_null_ohlc_rows for value in validations),
    }
    return (
        validation,
        _input_manifest(legacy_paths),
        _input_manifest(official_paths),
        excluded_spreads,
    )


def _boundary(
    legacy_root: Path, official_root: Path, *, futures: bool
) -> dict[str, object]:
    legacy_path = sorted(legacy_root.glob("year=*/data.parquet"))[-1]
    official_path = sorted(official_root.glob("year=*/data.parquet"))[0]
    legacy = pq.ParquetFile(legacy_path).read().to_pandas()
    official = pq.ParquetFile(official_path).read().to_pandas()
    if futures:
        legacy = legacy.loc[legacy["name"].str.contains(" F ", regex=False)]
    legacy_last = pd.to_datetime(legacy["date"]).max()
    official_first = pd.to_datetime(official["date"]).min()
    legacy_codes = set(legacy.loc[pd.to_datetime(legacy["date"]).eq(legacy_last), "contract"])
    official_codes = set(
        official.loc[pd.to_datetime(official["date"]).eq(official_first), "contract"]
    )
    return {
        "legacy_last_date": legacy_last.strftime("%Y-%m-%d"),
        "official_first_date": official_first.strftime("%Y-%m-%d"),
        "calendar_day_gap": int((official_first - legacy_last).days),
        "legacy_contracts": len(legacy_codes),
        "official_contracts": len(official_codes),
        "contract_code_intersection": len(legacy_codes.intersection(official_codes)),
        "legacy_only_contracts": len(legacy_codes - official_codes),
        "official_only_contracts": len(official_codes - legacy_codes),
    }


def build_kospi200_derivatives_bridge(
    *,
    legacy_futures_root: Path,
    official_futures_root: Path,
    legacy_options_root: Path,
    official_options_root: Path,
    output_bundle_root: Path,
    output_state_path: Path,
) -> dict:
    """Build provider-boundary Published unions without a continuous-roll rule."""

    output_bundle_root.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(
        tempfile.mkdtemp(
            prefix=f".{output_bundle_root.name}.stage-",
            dir=output_bundle_root.parent,
        )
    )
    try:
        fv, fli, foi, excluded_spreads = _build_dataset(
            legacy_root=legacy_futures_root,
            official_root=official_futures_root,
            stage_root=stage,
            dataset=FUTURES_DATASET,
            schema=FUTURES_SCHEMA,
            legacy_transform=_legacy_futures,
            official_transform=_official_futures,
        )
        ov, oli, ooi, option_excluded = _build_dataset(
            legacy_root=legacy_options_root,
            official_root=official_options_root,
            stage_root=stage,
            dataset=OPTIONS_DATASET,
            schema=OPTIONS_SCHEMA,
            legacy_transform=_legacy_options,
            official_transform=_official_options,
        )
        if option_excluded:
            raise KOSPI200DerivativesBridgeError("unexpected excluded option rows")
        futures_boundary = _boundary(
            legacy_futures_root, official_futures_root, futures=True
        )
        options_boundary = _boundary(
            legacy_options_root, official_options_root, futures=False
        )
        for boundary in (futures_boundary, options_boundary):
            if (
                boundary["legacy_last_date"] != "2019-12-30"
                or boundary["official_first_date"] != "2020-01-02"
                or boundary["legacy_only_contracts"]
                or boundary["official_only_contracts"]
            ):
                raise KOSPI200DerivativesBridgeError(
                    "exact legacy/official derivative boundary differs"
                )
        _commit_directory_atomic(stage, output_bundle_root)
    except Exception:
        if stage.exists():
            shutil.rmtree(stage)
        raise

    output_files = []
    for path in sorted(output_bundle_root.glob("*/year=*/data.parquet")):
        table = pq.ParquetFile(path)
        output_files.append(
            {
                "path": str(path.resolve()),
                "rows": table.metadata.num_rows,
                "sha256": _sha256(path),
            }
        )
    payload = {
        "task_id": "C007",
        "status": "source_found_with_limits",
        "api_calls": 0,
        "layer": LAYER,
        "datasets": {
            FUTURES_DATASET: {
                "schema": str(FUTURES_SCHEMA),
                "primary_key": list(PRIMARY_KEY),
                "validation": fv,
                "legacy_input_manifest": fli,
                "official_input_manifest": foi,
                "excluded_legacy_spread_rows": excluded_spreads,
                "boundary": futures_boundary,
            },
            OPTIONS_DATASET: {
                "schema": str(OPTIONS_SCHEMA),
                "primary_key": list(PRIMARY_KEY),
                "validation": ov,
                "legacy_input_manifest": oli,
                "official_input_manifest": ooi,
                "boundary": options_boundary,
            },
        },
        "output_files": output_files,
        "predictive_use_limitations": [
            "No continuous-contract or front-month roll rule is defined.",
            "Legacy regular and night futures sessions remain distinct rows.",
            "Official option session is unavailable and remains unspecified.",
            "Legacy volume/open-interest units remain unverified and are not relabeled.",
            "Legacy missing OHLC and official source zero OHLC are preserved distinctly.",
            "Maturity month is not an expiry date; expiry remains null.",
        ],
        "failed": {},
        "staged": [],
    }
    _write_state_atomic(payload, output_state_path)
    return payload
