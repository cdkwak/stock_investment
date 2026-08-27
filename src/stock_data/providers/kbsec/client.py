from __future__ import annotations

from dataclasses import dataclass
import os
import re
import time
from typing import Any

import requests

TOKEN_PATH = "/oauth2/token"
MARKET_SUMMARY_PATH = "/api/v1/ivsa0070"
ACCOUNT_SNAPSHOT_PATH = "/api/v1/ssqm2952"
HEADER_KEYS = frozenset({"ipAddr", "macAddr"})
TOKEN_BODY_KEYS = frozenset({"grantType", "appKey", "appSecret"})
ACCOUNT_SNAPSHOT_BODY = {"excg_mktpr_ccd": "A"}


class KBSecError(RuntimeError): pass
class KBSecConfigurationError(KBSecError): pass


@dataclass(frozen=True)
class KBSecHTTPDiagnostics:
    http_status: int
    content_type: str | None
    response_is_json: bool
    error_code: str | None = None
    error_message: str | None = None
    result_code: str | None = None
    result_message: str | None = None
    process_code: str | None = None
    process_message: str | None = None
    text_excerpt: str | None = None


class KBSecHTTPError(KBSecError):
    def __init__(self, message, *, details: KBSecHTTPDiagnostics | None = None):
        super().__init__(message)
        self.details = details


class KBSecResponseError(KBSecError): pass
class KBSecBusinessError(KBSecError):
    def __init__(self, message, *, http_status=None, result_code=None, result_message=None,
                 process_code=None, process_message=None):
        super().__init__(message)
        self.http_status = http_status
        self.result_code = result_code
        self.result_message = result_message
        self.process_code = process_code
        self.process_message = process_message


@dataclass(frozen=True)
class KBSecResponse:
    result_code: str
    process_code: str
    data_body: dict[str, Any]
    raw_payload: dict[str, Any]
    http_status: int = 200
    result_message: str | None = None
    process_message: str | None = None


class KBSecClient:
    """Read-only KB Open API client. Tokens remain in process memory."""
    def __init__(self, *, base_url=None, app_key=None, app_secret=None, session=None, clock=time.time):
        self.base_url = (base_url or os.getenv("KBSEC_BASE_URL", "")).rstrip("/")
        self.app_key = app_key or os.getenv("KBSEC_APP_KEY", "")
        self.app_secret = app_secret or os.getenv("KBSEC_APP_SECRET", "")
        missing = [n for n, v in (("KBSEC_BASE_URL", self.base_url), ("KBSEC_APP_KEY", self.app_key), ("KBSEC_APP_SECRET", self.app_secret)) if not v]
        if missing: raise KBSecConfigurationError("missing environment variables: " + ", ".join(missing))
        if session is None:
            self.session = requests.Session()
            # Exact broker authority must not inherit proxy/netrc credentials
            # or other ambient Requests environment configuration.
            self.session.trust_env = False
        else:
            self.session = session
        self.clock = clock
        self._token = None; self._expires_at = 0.0

    def _send_json(self, path, *, headers=None, payload):
        try:
            response = self.session.post(
                self.base_url + path,
                headers=headers,
                json=payload,
                timeout=(3.05, 10.0),
                allow_redirects=False,
            )
        except requests.RequestException:
            raise KBSecHTTPError("KB API request failed") from None
        if not 200 <= response.status_code < 300:
            raise KBSecHTTPError(
                f"KB API HTTP {response.status_code}",
                details=self._http_diagnostics(response),
            )
        try: result = response.json()
        except (ValueError, TypeError): raise KBSecResponseError("KB API response is not JSON") from None
        if not isinstance(result, dict): raise KBSecResponseError("KB API response is not an object")
        return result, int(response.status_code)

    def _post(self, path, *, headers=None, body):
        payload = {"dataHeader": {"ipAddr": "127.0.0.1", "macAddr": "00:00:00:00:00:00"}, "dataBody": dict(body)}
        if set(payload["dataHeader"]) != HEADER_KEYS:
            raise KBSecConfigurationError("invalid KB dataHeader keys")
        if path == TOKEN_PATH and set(payload["dataBody"]) != TOKEN_BODY_KEYS:
            raise KBSecConfigurationError("invalid KB B2C token payload keys")
        result, http_status = self._send_json(path, headers=headers, payload=payload)
        if not isinstance(result.get("dataHeader"), dict) or not isinstance(result.get("dataBody"), dict):
            raise KBSecResponseError("KB API envelope is invalid")
        return result, http_status

    def access_token(self):
        now = float(self.clock())
        if self._token and now < self._expires_at - 60: return self._token
        payload, http_status = self._post(
            TOKEN_PATH,
            body={"grantType": "client_credentials", "appKey": self.app_key, "appSecret": self.app_secret},
        )
        data_body = payload["dataBody"]
        token = data_body.get("access_token")
        if not isinstance(token, str) or not token:
            header = payload.get("dataHeader") if isinstance(payload.get("dataHeader"), dict) else {}
            raise KBSecBusinessError(
                "KB token request rejected", http_status=http_status,
                result_code=self._safe(header.get("resultCode") or payload.get("error")),
                result_message=self._safe(header.get("resultMessage") or payload.get("error_description")),
                process_code=self._safe(header.get("processCode")),
                process_message=self._safe(header.get("processMessage")),
            )
        try: expires = int(data_body.get("expires_in"))
        except (TypeError, ValueError): raise KBSecResponseError("invalid token expiry") from None
        if expires <= 0: raise KBSecResponseError("invalid token response")
        self._token, self._expires_at = token, now + expires
        return token

    def _safe(self, value):
        if value is None: return None
        text = str(value)
        for sensitive in (self.app_key, self.app_secret, self._token):
            if sensitive: text = text.replace(sensitive, "[REDACTED]")
        text = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", text)
        text = re.sub(
            r"(?i)(access[_-]?token\s*[\"']?\s*[:=]\s*[\"']?)[^\"'\s,}]+",
            r"\1[REDACTED]", text,
        )
        return " ".join(text.split())[:300]

    def _http_diagnostics(self, response) -> KBSecHTTPDiagnostics:
        raw_content_type = response.headers.get("Content-Type") if response.headers else None
        content_type = self._safe(str(raw_content_type).split(";", 1)[0]) if raw_content_type else None
        try:
            payload = response.json()
            response_is_json = True
        except (ValueError, TypeError):
            payload = None
            response_is_json = False
        if isinstance(payload, dict):
            header = payload.get("dataHeader") if isinstance(payload.get("dataHeader"), dict) else {}
            return KBSecHTTPDiagnostics(
                http_status=int(response.status_code), content_type=content_type,
                response_is_json=response_is_json,
                error_code=self._safe(payload.get("error_code") or payload.get("errorCode") or payload.get("error")),
                error_message=self._safe(payload.get("error_description") or payload.get("errorMessage") or payload.get("message")),
                result_code=self._safe(header.get("resultCode") or payload.get("resultCode")),
                result_message=self._safe(header.get("resultMessage") or payload.get("resultMessage")),
                process_code=self._safe(header.get("processCode") or payload.get("processCode")),
                process_message=self._safe(header.get("processMessage") or payload.get("processMessage")),
            )
        excerpt = None
        if not response_is_json:
            try: excerpt = self._safe(response.text)
            except (AttributeError, TypeError): excerpt = None
        return KBSecHTTPDiagnostics(
            http_status=int(response.status_code), content_type=content_type,
            response_is_json=response_is_json, text_excerpt=excerpt,
        )

    def market_summary(self):
        payload, http_status = self._post(MARKET_SUMMARY_PATH, headers={"Authorization": f"Bearer {self.access_token()}", "Content-Type": "application/json", "Accept": "application/json"}, body={})
        header = payload["dataHeader"]
        result_code = self._safe(header.get("resultCode")) or ""
        process_code = self._safe(header.get("processCode")) or ""
        result_message = self._safe(header.get("resultMessage"))
        process_message = self._safe(header.get("processMessage"))
        if result_code != "200":
            raise KBSecBusinessError(
                "KB market summary rejected", http_status=http_status,
                result_code=result_code, result_message=result_message,
                process_code=process_code, process_message=process_message,
            )
        return KBSecResponse(
            result_code, process_code, dict(payload["dataBody"]), payload, http_status,
            result_message=result_message, process_message=process_message,
        )

    def account_snapshot(self):
        """Fetch the authorized read-only SSQM2952 account snapshot."""
        payload, http_status = self._post(
            ACCOUNT_SNAPSHOT_PATH,
            headers={
                "Authorization": f"Bearer {self.access_token()}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            body=ACCOUNT_SNAPSHOT_BODY,
        )
        header = payload["dataHeader"]
        result_code = self._safe(header.get("resultCode")) or ""
        process_code = self._safe(header.get("processCode")) or ""
        result_message = self._safe(header.get("resultMessage"))
        process_message = self._safe(header.get("processMessage"))
        if result_code != "200" or process_code != "0011":
            raise KBSecBusinessError(
                "KB account snapshot rejected",
                http_status=http_status,
                result_code=result_code,
                result_message=result_message,
                process_code=process_code,
                process_message=process_message,
            )
        return KBSecResponse(
            result_code,
            process_code,
            dict(payload["dataBody"]),
            payload,
            http_status,
            result_message=result_message,
            process_message=process_message,
        )
