"""Identifier-free contract for a sanitized KB domestic account snapshot."""

KB_ACCOUNT_SNAPSHOT_SCHEMA_VERSION = 1
KB_ACCOUNT_SOURCE = "kbsec_open_api"
KB_ACCOUNT_SOURCE_OPERATION = "SSQM2952"
KB_ACCOUNT_SOURCE_EVIDENCE_VERSION = "official-sample-2026-06-22"

KB_ACCOUNT_REGISTERED_HOLDER_SCOPE = "SELF"
KB_ACCOUNT_ECONOMIC_ATTRIBUTION_SCOPE = "SELF"
KB_ACCOUNT_SOURCE_MODE = "SANITIZED_READ_ONLY"

KB_ACCOUNT_UNSUPPORTED_FIELDS = frozenset({
    "cash_balance",
    "buying_power",
    "realized_pnl",
    "overseas_positions",
})
