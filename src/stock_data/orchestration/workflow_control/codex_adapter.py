"""Disabled-by-default local boundary for a future Codex Python SDK client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Protocol


class CodexAdapterError(RuntimeError):
    pass


class LocalCodexBoundary(Protocol):
    """Minimal injectable boundary; production SDK imports do not live here."""

    def invoke(self, request: Mapping[str, str]) -> Mapping[str, str]: ...


@dataclass(slots=True)
class LocalFakeCodexBoundary:
    """Deterministic test double with no process, network, or SDK behavior."""

    response: Mapping[str, str]
    calls: int = 0

    def invoke(self, request: Mapping[str, str]) -> Mapping[str, str]:
        del request
        self.calls += 1
        return dict(self.response)


class CodexSdkAdapter:
    """Explicitly gated adapter that requires an injected local boundary."""

    def __init__(
        self,
        boundary: LocalCodexBoundary | None = None,
        *,
        enabled: bool = False,
    ) -> None:
        self._boundary = boundary
        self.enabled = enabled

    def invoke(self, request: Mapping[str, str]) -> Mapping[str, str]:
        if not self.enabled:
            raise CodexAdapterError("Codex SDK adapter is disabled")
        if self._boundary is None:
            raise CodexAdapterError("no local Codex boundary was injected")
        if not all(isinstance(key, str) and isinstance(value, str) for key, value in request.items()):
            raise CodexAdapterError("Codex request must contain only text fields")
        return dict(self._boundary.invoke(dict(request)))
