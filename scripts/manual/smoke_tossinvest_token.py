"""One-shot Toss Securities OAuth smoke. Never prints credentials or tokens."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from stock_data.providers.tossinvest import (  # noqa: E402
    DEFAULT_BASE_URL,
    TossInvestAuthenticationError,
    TossInvestClient,
    TossInvestConfigurationError,
    TossInvestError,
    TossInvestHTTPError,
    TossInvestRateLimitError,
    TossInvestResponseError,
    TossInvestTimeoutError,
)


REQUIRED = (
    "TOSSINVEST_BASE_URL",
    "TOSSINVEST_CLIENT_ID",
    "TOSSINVEST_CLIENT_SECRET",
)


def _rate_limit_payload(rate_limit) -> dict[str, int | str | None] | None:
    if rate_limit is None:
        return None
    return {
        "group": rate_limit.group,
        "limit": rate_limit.limit,
        "remaining": rate_limit.remaining,
        "reset_seconds": rate_limit.reset_seconds,
        "retry_after_seconds": rate_limit.retry_after_seconds,
    }


def _error_status(error: TossInvestError) -> str:
    if isinstance(error, TossInvestRateLimitError):
        return "TOKEN_RATE_LIMITED"
    if isinstance(error, TossInvestAuthenticationError):
        return "TOKEN_REJECTED"
    if isinstance(error, TossInvestTimeoutError):
        return "TOKEN_TIMEOUT"
    if isinstance(error, TossInvestResponseError):
        return "TOKEN_RESPONSE_ERROR"
    if isinstance(error, TossInvestHTTPError):
        return "TOKEN_HTTP_ERROR"
    if isinstance(error, TossInvestConfigurationError):
        return "LIVE_NOT_READY"
    return "TOKEN_ERROR"


def main() -> int:
    load_dotenv(ROOT / ".env", override=False)
    base_url = os.getenv("TOSSINVEST_BASE_URL", "").strip() or DEFAULT_BASE_URL
    readiness = {
        "TOSSINVEST_BASE_URL": bool(base_url),
        "TOSSINVEST_CLIENT_ID": bool(
            os.getenv("TOSSINVEST_CLIENT_ID", "").strip()
        ),
        "TOSSINVEST_CLIENT_SECRET": bool(
            os.getenv("TOSSINVEST_CLIENT_SECRET", "").strip()
        ),
    }
    if not all(readiness.values()):
        print(
            json.dumps(
                {"status": "LIVE_NOT_READY", "credentials": readiness},
                ensure_ascii=False,
            )
        )
        return 2

    try:
        client = TossInvestClient.from_environment(project_root=ROOT)
        client.access_token()  # exactly one network call; client performs no retry
        metadata = client.token_metadata
        if metadata is None:
            raise TossInvestResponseError("missing token metadata")
        print(
            json.dumps(
                {
                    "status": "TOKEN_OK",
                    "http_status": metadata.http_status,
                    "expires_in": metadata.expires_in,
                    "rate_limit": _rate_limit_payload(metadata.rate_limit),
                },
                ensure_ascii=False,
            )
        )
        return 0
    except TossInvestError as error:
        details = error.details
        print(
            json.dumps(
                {
                    "status": _error_status(error),
                    "http_status": details.http_status if details else None,
                    "content_type": details.content_type if details else None,
                    "error_code": details.error_code if details else None,
                    "error_message": details.error_message if details else None,
                    "request_id": details.request_id if details else None,
                    "rate_limit": _rate_limit_payload(
                        details.rate_limit if details else None
                    ),
                },
                ensure_ascii=False,
            )
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
