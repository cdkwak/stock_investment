from pathlib import Path

import pytest

from scripts.manual.ls_derivatives_investor_pilot import (
    OFFICIAL_TOKEN_URL,
    credential_value,
    oauth_request,
    official_base_url,
    safe_json,
    safe_oauth_error,
    secret_scan,
    post_oauth_once,
)


def test_official_base_url_rejects_non_official_host():
    assert official_base_url("https://openapi.ls-sec.co.kr:8080") == "https://openapi.ls-sec.co.kr:8080"
    assert OFFICIAL_TOKEN_URL == "https://openapi.ls-sec.co.kr:8080/oauth2/token"
    for invalid in (
        "https://example.com",
        "https://openapi.ls-sec.co.kr",
        "https://openapi.ls-sec.co.kr/intro",
        "https://openapi.ls-sec.co.kr:8080/intro",
        "https://openapi.ls-sec.co.kr:8080/",
        " https://openapi.ls-sec.co.kr:8080",
        "http://openapi.ls-sec.co.kr:8080",
    ):
        try:
            official_base_url(invalid)
        except ValueError:
            pass
        else:
            raise AssertionError(f"invalid LS base URL was accepted: {invalid}")


def test_safe_json_redacts_sensitive_keys_and_values():
    safe = safe_json(
        {"access_token": "token-value", "message": "contains app-secret", "rows": [{"sv_08": 1}]},
        ("app-secret", "token-value"),
    )
    assert safe["access_token"] == "[REDACTED]"
    assert safe["message"] == "contains [REDACTED]"
    assert safe["rows"] == [{"sv_08": 1}]


def test_secret_scan(tmp_path: Path):
    safe = tmp_path / "safe.json"
    safe.write_text('{"token":"[REDACTED]"}', encoding="utf-8")
    assert secret_scan([safe], ("real-secret",))
    unsafe = tmp_path / "unsafe.json"
    unsafe.write_text("real-secret", encoding="utf-8")
    assert not secret_scan([safe, unsafe], ("real-secret",))


def test_safe_oauth_error_keeps_only_redacted_code_and_message():
    code, message = safe_oauth_error(
        {"rsp_cd": "IGW00121", "rsp_msg": "bad app-secret", "access_token": "token"},
        ("app-secret", "token"),
    )
    assert code == "IGW00121"
    assert message == "bad [REDACTED]"


def test_oauth_request_exactly_matches_official_params_sample():
    headers, params = oauth_request("dummy-key", "dummy-secret")
    assert headers == {"content-type": "application/x-www-form-urlencoded"}
    assert params == {
        "grant_type": "client_credentials",
        "appkey": "dummy-key",
        "appsecretkey": "dummy-secret",
        "scope": "oob",
    }


def test_post_oauth_once_uses_params_not_data_or_json():
    class FakeSession:
        def post(self, url, **kwargs):
            self.url = url
            self.kwargs = kwargs
            return object()

    session = FakeSession()
    post_oauth_once(session, OFFICIAL_TOKEN_URL, "dummy-key", "dummy-secret")
    assert session.url == OFFICIAL_TOKEN_URL
    assert set(session.kwargs) == {"headers", "params", "timeout"}
    assert "data" not in session.kwargs
    assert "json" not in session.kwargs


def test_credential_value_rejects_whitespace(monkeypatch):
    monkeypatch.setenv("LS_APP_KEY", " padded")
    with pytest.raises(ValueError, match="whitespace"):
        credential_value("LS_APP_KEY")
