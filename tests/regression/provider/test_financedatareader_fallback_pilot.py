from __future__ import annotations

import hashlib

import pytest

from scripts.manual.pilot.financedatareader_fallback_routes import (
    GuardedGetTransport,
    PilotStopped,
    TIMEOUT_SECONDS,
)


class _Response:
    status_code = 200
    content = b"bounded-public-body"


class _Backend:
    def __init__(self):
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return _Response()


def _transport():
    transport = GuardedGetTransport(
        route_id="synthetic",
        allowed_host="example.test",
        allowed_path="/public.csv",
        budget=1,
    )
    backend = _Backend()
    transport._session = backend
    return transport, backend


def test_guarded_transport_enforces_timeout_retry0_and_sanitized_evidence():
    transport, backend = _transport()
    response = transport.get("https://example.test/public.csv?symbol=PUBLIC")
    assert response.content == b"bounded-public-body"
    assert backend.calls[0][1]["timeout"] == TIMEOUT_SECONDS
    assert backend.calls[0][1]["allow_redirects"] is False
    evidence = transport.calls[0]
    assert evidence.retry_count == 0
    assert evidence.response_sha256 == hashlib.sha256(response.content).hexdigest()
    assert not hasattr(evidence, "headers") and not hasattr(evidence, "body")


def test_guarded_transport_stops_on_budget_or_upstream_change_without_call():
    transport, backend = _transport()
    with pytest.raises(PilotStopped, match="UPSTREAM_IDENTITY_MISMATCH"):
        transport.get("https://other.test/public.csv")
    assert backend.calls == []

    transport.get("https://example.test/public.csv")
    with pytest.raises(PilotStopped, match="ROUTE_REQUEST_BUDGET_EXCEEDED"):
        transport.get("https://example.test/public.csv")
    assert len(backend.calls) == 1
