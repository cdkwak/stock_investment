from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Callable


class PykrxJobLimitError(RuntimeError):
    pass


class PykrxAutomationDisabledError(RuntimeError):
    pass


def require_manual_live_access(*, manual: bool, requested_days: int) -> None:
    """Reject automated/long live use while allowing a bounded manual smoke test."""
    if not manual:
        raise PykrxAutomationDisabledError(
            "live pykrx automation is disabled; use an approved manual smoke test"
        )
    if requested_days < 1 or requested_days > 10:
        raise PykrxAutomationDisabledError(
            "manual pykrx access is limited to at most 10 calendar days"
        )


@dataclass
class PykrxRequestPolicy:
    min_interval_seconds: float = 2.0
    initial_backoff_seconds: float = 2.0
    max_backoff_seconds: float = 60.0
    max_consecutive_requests: int = 6
    max_consecutive_failures: int = 3
    sleep_fn: Callable[[float], None] = time.sleep
    monotonic_fn: Callable[[], float] = time.monotonic
    request_count: int = 0
    consecutive_failures: int = 0
    _last_request_at: float | None = field(default=None, init=False)

    def before_request(self) -> None:
        if self.request_count >= self.max_consecutive_requests:
            raise PykrxJobLimitError(
                f"pykrx job request limit reached: {self.max_consecutive_requests}"
            )
        if self._last_request_at is not None:
            wait = self.min_interval_seconds - (
                self.monotonic_fn() - self._last_request_at
            )
            if wait > 0:
                self.sleep_fn(wait)
        self.request_count += 1
        self._last_request_at = self.monotonic_fn()

    def record_success(self) -> None:
        self.consecutive_failures = 0

    def record_failure(self) -> None:
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.max_consecutive_failures:
            raise PykrxJobLimitError(
                f"pykrx job stopped after {self.consecutive_failures} consecutive failures"
            )
        delay = min(
            self.initial_backoff_seconds * (2 ** (self.consecutive_failures - 1)),
            self.max_backoff_seconds,
        )
        self.sleep_fn(delay)
