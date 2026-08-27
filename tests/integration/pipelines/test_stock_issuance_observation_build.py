from pathlib import Path
import sys

import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from scripts.manual.build.build_stock_issuance_observation import (  # noqa: E402
    AVAILABILITY, BuildError, _date_token, validate_frame,
)
from stock_data.contracts.stock_issuance_observation import (  # noqa: E402
    KR_EQUITY_STOCK_ISSUANCE_SOURCE_OBSERVATION as CONTRACT,
)


def row() -> dict[str, object]:
    value = {name: None for name in CONTRACT.column_names}
    value.update({
        "source_snapshot_date": "2026-08-12", "capture_id": "run",
        "captured_at_utc": pd.Timestamp("2026-08-13T00:00:00Z"),
        "landing_response_sha256": "a" * 64, "source_page_no": 1,
        "source_page_item_ordinal": 1, "source_item_ordinal": 1,
        "source_record_sha256": "b" * 64, "corporate_number": "1234567890123",
        "issuer_name": "issuer", "issue_effective_date_source": "00000101",
        "issue_effective_date_status": "INVALID_SOURCE_TOKEN",
        "issued_shares": -1, "listing_date_status": "MISSING",
        "availability_status": AVAILABILITY,
    })
    return value


def test_date_token_preserves_invalid_and_missing_values():
    assert _date_token("00000101") == ("00000101", None, "INVALID_SOURCE_TOKEN")
    assert _date_token("99981230") == ("99981230", None, "OUT_OF_SUPPORTED_RANGE")
    assert _date_token("") == (None, None, "MISSING")
    assert _date_token("20260812") == ("20260812", "2026-08-12", "PARSED")


def test_validator_accepts_signed_source_value():
    frame = pd.DataFrame([row()], columns=CONTRACT.column_names)
    validate_frame(frame, expected_rows=1)


def test_validator_rejects_duplicate_primary_key():
    frame = pd.DataFrame([row(), row()], columns=CONTRACT.column_names)
    with pytest.raises(BuildError, match="primary key"):
        validate_frame(frame, expected_rows=2)
