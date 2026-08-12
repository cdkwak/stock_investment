from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Mapping

import pandas as pd
from pandas.testing import assert_frame_equal

from stock_data.contracts.base import DatasetContract
from stock_data.pipelines.derivatives_backfill import (
    _items_from_pages,
    _read_staged_landing,
)
from stock_data.providers.data_go_kr.derivatives import (
    PRODUCT_SPECS,
    DerivativeProductSpec,
    normalize_derivatives,
)
from stock_data.storage.contract_parquet import read_dataset, write_dataset_atomic
from stock_data.validation.data_v1 import validate_data_v1


REPORT_SCHEMA = "stock_data.retained_derivatives_promotion"
REPORT_VERSION = 1
CLASSIFICATION = "DATA_COMPLETE_WITH_LIMITS"
TARGETS = ("kosdaq150_options", "kosdaq150_futures")
EXPECTED_SOURCE_DATE = "2022-09-19"


@dataclass(frozen=True)
class PreparedPromotion:
    key: str
    spec: DerivativeProductSpec
    input_path: Path
    input_sha256: str
    input_bytes: int
    source_pages: int
    declared_total_count: int
    source_rows: int
    exact_category_rows: int
    excluded_rows: int
    exclusion_reason: str | None
    excluded_contracts: tuple[str, ...]
    dataframe: pd.DataFrame


@dataclass(frozen=True)
class PromotionResult:
    dataset: str
    output_root: str
    rows: int
    source_rows: int
    exact_category_rows: int
    excluded_rows: int
    minimum_date: str
    maximum_date: str
    output_files: tuple[dict[str, object], ...]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _validator(contract: DatasetContract):
    return lambda frame: validate_data_v1(frame, contract, allow_empty=False)


def _atomic_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _exact_category(items: list[Mapping[str, object]], spec: DerivativeProductSpec):
    return [
        item
        for item in items
        if str(item.get("prdCtg", "")).strip() == spec.product_category
    ]


def _declared_total_count(pages: tuple[Mapping[str, object], ...]) -> int:
    totals = set()
    for page in pages:
        try:
            total = page["response"]["body"]["totalCount"]  # type: ignore[index]
            totals.add(int(total))
        except (KeyError, TypeError, ValueError):
            raise RuntimeError("landing response totalCount is invalid") from None
    if len(totals) != 1:
        raise RuntimeError("landing response totalCount differs across pages")
    return totals.pop()


def prepare_promotion(key: str, input_path: Path) -> PreparedPromotion:
    if key not in TARGETS:
        raise ValueError(f"unsupported retained promotion target: {key}")
    spec = PRODUCT_SPECS[key]
    input_path = input_path.resolve()
    pages = _read_staged_landing(input_path)
    items = _items_from_pages(pages)
    declared_total_count = _declared_total_count(pages)
    if declared_total_count != len(items):
        raise RuntimeError(
            f"{spec.contract.name}: retained Landing is incomplete "
            f"({len(items)} of {declared_total_count} source rows)"
        )
    exact = _exact_category(items, spec)
    dataframe = normalize_derivatives(items, spec)
    validate_data_v1(dataframe, spec.contract, allow_empty=False)
    if tuple(dataframe.columns) != spec.contract.column_names:
        raise RuntimeError(f"{spec.contract.name}: normalized schema differs from contract")
    if dataframe.duplicated(list(spec.contract.primary_key)).any():
        raise RuntimeError(f"{spec.contract.name}: duplicate primary key")
    dates = set(pd.to_datetime(dataframe["date"], errors="raise").dt.strftime("%Y-%m-%d"))
    if dates != {EXPECTED_SOURCE_DATE}:
        raise RuntimeError(f"{spec.contract.name}: unexpected retained source coverage: {dates}")

    normalized_contracts = set(dataframe["contract"].astype(str))
    excluded = [
        item for item in exact if str(item.get("srtnCd", "")).strip() not in normalized_contracts
    ]
    exclusion_reason = None
    if excluded:
        names = [str(item.get("itmsNm", "")).strip() for item in excluded]
        if spec.kind != "futures" or not all(" SP " in name for name in names):
            raise RuntimeError(
                f"{spec.contract.name}: exact-category rows had an undocumented exclusion"
            )
        exclusion_reason = "calendar_spread_landing_only"
    if len(dataframe) + len(excluded) != len(exact):
        raise RuntimeError(f"{spec.contract.name}: promotion denominator is inconsistent")

    return PreparedPromotion(
        key=key,
        spec=spec,
        input_path=input_path,
        input_sha256=_sha256(input_path),
        input_bytes=input_path.stat().st_size,
        source_pages=len(pages),
        declared_total_count=declared_total_count,
        source_rows=len(items),
        exact_category_rows=len(exact),
        excluded_rows=len(excluded),
        exclusion_reason=exclusion_reason,
        excluded_contracts=tuple(
            sorted(str(item.get("srtnCd", "")).strip() for item in excluded)
        ),
        dataframe=dataframe,
    )


def _write_and_verify(prepared: PreparedPromotion, project_root: Path) -> PromotionResult:
    contract = prepared.spec.contract
    output_root = project_root / "data/normalized" / contract.name
    validator = _validator(contract)
    expected = prepared.dataframe.reset_index(drop=True)
    if output_root.exists():
        restored = read_dataset(output_root, contract, validator)
        assert_frame_equal(restored, expected, check_dtype=False)
    else:
        write_dataset_atomic(expected, output_root, contract, validator)
        restored = read_dataset(output_root, contract, validator)
        assert_frame_equal(restored, expected, check_dtype=False)

    output_files = tuple(
        {
            "path": path.relative_to(project_root).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
            "rows": len(pd.read_parquet(path)),
        }
        for path in sorted(output_root.rglob("data.parquet"))
    )
    if sum(int(item["rows"]) for item in output_files) != len(expected):
        raise RuntimeError(f"{contract.name}: output manifest row count differs")
    dates = pd.to_datetime(expected["date"], errors="raise").dt.strftime("%Y-%m-%d")
    return PromotionResult(
        dataset=contract.name,
        output_root=output_root.relative_to(project_root).as_posix(),
        rows=len(expected),
        source_rows=prepared.source_rows,
        exact_category_rows=prepared.exact_category_rows,
        excluded_rows=prepared.excluded_rows,
        minimum_date=dates.min(),
        maximum_date=dates.max(),
        output_files=output_files,
    )


def promote_retained_kosdaq150(
    *,
    project_root: Path,
    options_input: Path,
    futures_input: Path,
    state_path: Path | None = None,
) -> dict[str, object]:
    project_root = project_root.resolve()
    prepared = (
        prepare_promotion("kosdaq150_options", options_input),
        prepare_promotion("kosdaq150_futures", futures_input),
    )
    # Both inputs, schemas, primary keys, and exclusion denominators are checked
    # before the first persistent output is touched.
    results = tuple(_write_and_verify(item, project_root) for item in prepared)
    state_path = state_path or (
        project_root / "data/state/d004_kosdaq150_retained_promotion.json"
    )
    state = {
        "report_schema": REPORT_SCHEMA,
        "report_version": REPORT_VERSION,
        "classification": CLASSIFICATION,
        "coverage_limit": f"single retained source snapshot: {EXPECTED_SOURCE_DATE}",
        "inputs": [
            {
                "dataset": item.spec.contract.name,
                "path": str(item.input_path),
                "bytes": item.input_bytes,
                "sha256": item.input_sha256,
                "pages": item.source_pages,
                "declared_total_count": item.declared_total_count,
                "source_rows": item.source_rows,
                "exact_category_rows": item.exact_category_rows,
                "excluded_rows": item.excluded_rows,
                "exclusion_reason": item.exclusion_reason,
                "excluded_contracts": list(item.excluded_contracts),
            }
            for item in prepared
        ],
        "outputs": [asdict(result) for result in results],
        "validation": {
            "contract_schema": "PASS",
            "primary_key_uniqueness": "PASS",
            "required_nullability": "PASS",
            "atomic_parquet_readback": "PASS",
            "input_output_sha_manifests": "PASS",
        },
    }
    # Canonicalize tuples to their JSON representation so the returned and
    # persisted state have exactly the same structure.
    state = json.loads(json.dumps(state, ensure_ascii=False, sort_keys=True))
    _atomic_json(state_path.resolve(), state)
    return state
