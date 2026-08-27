import pytest
import hashlib
import stock_data.audit.cboe_vix_retained_audit as audit

def test_hash_gate_and_sanitized_schema_summary(tmp_path) -> None:
    path = tmp_path / "body.json"; path.write_bytes(b'{"data":{"symbol":"_VIX"},"symbol":"_VIX","timestamp":"x"}')
    with pytest.raises(ValueError, match="hash"):
        audit.audit_retained_body(path)


def test_verified_bytes_replay_is_sanitized_and_api_zero(monkeypatch, tmp_path) -> None:
    body = b'{"data":{"symbol":"_VIX","security_type":"index"},"symbol":"_VIX","timestamp":"x"}'
    path = tmp_path / "body.json"; path.write_bytes(body); monkeypatch.setattr(audit, "EXPECTED_SHA256", hashlib.sha256(body).hexdigest())
    result = audit.audit_retained_body(path)
    assert result["accepted"] is False and result["reason"] == "PROVIDER_TIMESTAMP_TIMEZONE_UNBOUND"
