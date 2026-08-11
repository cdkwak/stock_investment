from stock_data.contracts.legacy_kospi200 import LEGACY_KOSPI200_CONTRACTS
from stock_data.contracts.registry import CONTRACTS
from stock_data.derived.kospi200_option_pcr import PCR_SCHEMA
from stock_data.pipelines.legacy_derivatives_migration import (
    FUTURES_NORMALIZED_SCHEMA,
    FUTURES_SPEC,
    OPTIONS_NORMALIZED_SCHEMA,
    OPTIONS_SPEC,
)


def _schema_signature(schema):
    dtype_names = {
        "date32[day]": "date32",
        "double": "float64",
        "int64": "int64",
        "string": "string",
    }
    return tuple(
        (field.name, dtype_names[str(field.type)], field.nullable)
        for field in schema
    )


def _contract_signature(contract):
    return tuple(
        (column.name, column.dtype, column.nullable)
        for column in contract.columns
    )


def test_legacy_kospi200_contracts_match_implemented_schemas_and_keys() -> None:
    futures, options, pcr = LEGACY_KOSPI200_CONTRACTS

    assert _contract_signature(futures) == _schema_signature(FUTURES_NORMALIZED_SCHEMA)
    assert futures.primary_key == FUTURES_SPEC.primary_key
    assert futures.sort_key == FUTURES_SPEC.sort_key

    assert _contract_signature(options) == _schema_signature(OPTIONS_NORMALIZED_SCHEMA)
    assert options.primary_key == OPTIONS_SPEC.primary_key
    assert options.sort_key == OPTIONS_SPEC.sort_key

    assert _contract_signature(pcr) == _schema_signature(PCR_SCHEMA)
    assert pcr.layer == "derived"
    assert pcr.primary_key == ("date", "scope", "market_scope")
    assert pcr.sort_key == pcr.primary_key


def test_legacy_kospi200_contracts_are_active_and_registered() -> None:
    for contract in LEGACY_KOSPI200_CONTRACTS:
        assert contract.status == "active"
        assert contract.partition_by == ("year",)
        assert CONTRACTS[contract.name] is contract

    assert LEGACY_KOSPI200_CONTRACTS[2].columns[6].unit == "ratio"
    assert LEGACY_KOSPI200_CONTRACTS[2].columns[9].unit == "ratio"
    assert all(
        column.unit is None
        for contract in LEGACY_KOSPI200_CONTRACTS[:2]
        for column in contract.columns
    )
