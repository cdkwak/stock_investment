from __future__ import annotations

from copy import deepcopy

import pytest

from stock_data.providers.kbsec.client import (
    KBSecBusinessError,
    KBSecClient,
)


class _Response:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code
        self.headers = {"Content-Type": "application/json"}
        self.text = ""

    def json(self) -> dict:
        return deepcopy(self.payload)


class _Session:
    def __init__(self, responses: list[_Response]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def post(
        self, url, *, headers=None, json=None, timeout=None,
        allow_redirects=None,
    ):
        self.calls.append({
            "url": url,
            "headers": headers,
            "json": deepcopy(json),
            "timeout": timeout,
            "allow_redirects": allow_redirects,
        })
        return self.responses.pop(0)


def _token() -> dict:
    return {
        "dataHeader": {"resultCode": "200", "processCode": "0000"},
        "dataBody": {"access_token": "runtime-token", "expires_in": "3600"},
    }


def _account(*, process_code: str = "0011") -> dict:
    return {
        "dataHeader": {
            "resultCode": "200",
            "processCode": process_code,
            "processTime": "20260826144000000",
        },
        "dataBody": {
            "grid_cnt1": "0", "tl_data_cnt": "0",
            "nt_asts_val_amt": "0", "scrts_nt_val_amt": "0",
            "byng_amt_sum": "0", "val_amt_sum": "0", "val_pl_sum": "0",
            "Record1": [],
        },
    }


def test_account_snapshot_uses_exact_authorized_read_only_route() -> None:
    session = _Session([_Response(_token()), _Response(_account())])
    client = KBSecClient(
        base_url="https://kb.example",
        app_key="fixture-key",
        app_secret="fixture-secret",
        session=session,
        clock=lambda: 0,
    )

    response = client.account_snapshot()

    assert response.process_code == "0011"
    assert [call["url"] for call in session.calls] == [
        "https://kb.example/oauth2/token",
        "https://kb.example/api/v1/ssqm2952",
    ]
    assert session.calls[0]["json"]["dataBody"] == {
        "grantType": "client_credentials",
        "appKey": "fixture-key",
        "appSecret": "fixture-secret",
    }
    assert session.calls[1]["json"]["dataBody"] == {"excg_mktpr_ccd": "A"}
    assert session.calls[1]["headers"]["Authorization"] == "Bearer runtime-token"
    assert all(call["timeout"] == (3.05, 10.0) for call in session.calls)
    assert all(call["allow_redirects"] is False for call in session.calls)
    assert len(session.calls) == 2


def test_production_session_disables_ambient_requests_environment() -> None:
    client = KBSecClient(
        base_url="https://kb.example",
        app_key="fixture-key",
        app_secret="fixture-secret",
        clock=lambda: 0,
    )

    assert client.session.trust_env is False
    client.session.close()


def test_account_snapshot_rejection_does_not_render_configured_secret_text() -> None:
    session = _Session([_Response(_token()), _Response(_account(process_code="9999"))])
    client = KBSecClient(
        base_url="https://kb.example",
        app_key="fixture-key",
        app_secret="fixture-secret",
        session=session,
        clock=lambda: 0,
    )

    with pytest.raises(KBSecBusinessError) as captured:
        client.account_snapshot()

    rendered = str(captured.value)
    assert "fixture-key" not in rendered
    assert "fixture-secret" not in rendered
    assert "runtime-token" not in rendered
    assert len(session.calls) == 2
