from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Callable, Mapping
from urllib.parse import unquote
import xml.etree.ElementTree as ET

from dotenv import load_dotenv
import requests


ERROR_NAMES = {
    "01": "APPLICATION_ERROR",
    "10": "INVALID_REQUEST_PARAMETER_ERROR",
    "12": "NO_OPENAPI_SERVICE_ERROR",
    "20": "SERVICE_ACCESS_DENIED_ERROR",
    "22": "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR",
    "30": "SERVICE_KEY_IS_NOT_REGISTERED_ERROR",
    "31": "DEADLINE_HAS_EXPIRED_ERROR",
    "32": "UNREGISTERED_IP_ERROR",
    "99": "UNKNOWN_ERROR",
}
ERROR_CATEGORIES = {
    "10": "request",
    "12": "service",
    "20": "permission",
    "22": "rate_limit",
    "30": "authentication",
    "31": "authentication",
    "32": "ip_registration",
    "01": "application",
    "99": "unknown",
}


class DataGoKrConfigurationError(RuntimeError):
    pass


class DataGoKrApiError(RuntimeError):
    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        self.error_name = ERROR_NAMES.get(code, "UNDOCUMENTED_ERROR")
        self.category = ERROR_CATEGORIES.get(code, "undocumented")
        safe_message = re.sub(r"(?i)(serviceKey\s*[=:]\s*)\S+", r"\1[REDACTED]", message)
        super().__init__(f"data.go.kr API error {code} {self.error_name}: {safe_message[:160]}")


class DataGoKrHttpError(RuntimeError):
    def __init__(self, status_code: int, diagnosis: str = "unclassified_gateway_response") -> None:
        self.status_code = status_code
        self.diagnosis = diagnosis
        super().__init__(f"data.go.kr HTTP {status_code}: {diagnosis}")


@dataclass(frozen=True)
class DataGoKrResult:
    items: tuple[Mapping[str, object], ...]
    pages: tuple[Mapping[str, object], ...]
    total_count: int


@dataclass(frozen=True)
class DataGoKrPage:
    items: tuple[Mapping[str, object], ...]
    payload: Mapping[str, object]
    page_no: int
    total_count: int


def service_key_from_environment(project_root: Path | None = None) -> str:
    if project_root is not None:
        load_dotenv(project_root / ".env", override=False)
    value = os.getenv("DATA_GO_KR_SERVICE_KEY", "").strip()
    if not value:
        raise DataGoKrConfigurationError("DATA_GO_KR_SERVICE_KEY is not configured")
    return value


def _service_key_for_requests_params(value: str) -> str:
    """requests encodes params; decode a portal-issued encoded key exactly once."""
    return unquote(value) if re.search(r"%[0-9A-Fa-f]{2}", value) else value


def _documented_error_from_response(response) -> tuple[str, str] | None:
    """Extract only documented code/message fields; never retain or return raw bodies."""
    candidates: list[tuple[object, object]] = []
    try:
        payload = response.json()
        if isinstance(payload, dict):
            envelope = payload.get("response")
            if isinstance(envelope, dict) and isinstance(envelope.get("header"), dict):
                header = envelope["header"]
                candidates.append((header.get("resultCode"), header.get("resultMsg")))
            service = payload.get("OpenAPI_ServiceResponse")
            if isinstance(service, dict) and isinstance(service.get("cmmMsgHeader"), dict):
                header = service["cmmMsgHeader"]
                candidates.append((header.get("returnReasonCode"), header.get("returnAuthMsg")))
    except (ValueError, TypeError):
        try:
            root = ET.fromstring(response.content)
            candidates.append((
                root.findtext(".//resultCode") or root.findtext(".//returnReasonCode"),
                root.findtext(".//resultMsg") or root.findtext(".//returnAuthMsg"),
            ))
        except (ET.ParseError, TypeError, AttributeError):
            pass
    for raw_code, raw_message in candidates:
        if raw_code is None:
            continue
        code = str(raw_code).strip().zfill(2)
        if code in ERROR_NAMES:
            return code, str(raw_message or "")
    return None


class DataGoKrClient:
    def __init__(
        self, *, endpoint: str, service_key: str, session=requests,
        timeout_seconds: float = 20.0, max_attempts: int = 2,
        backoff_seconds: float = 1.0, sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        if not service_key.strip():
            raise DataGoKrConfigurationError("DATA_GO_KR_SERVICE_KEY is not configured")
        if max_attempts not in {1, 2}:
            raise ValueError("max_attempts must be 1 or 2")
        self._endpoint = endpoint
        self._original_service_key = service_key.strip()
        self._service_key = _service_key_for_requests_params(self._original_service_key)
        self._session = session
        self._timeout = timeout_seconds
        self._max_attempts = max_attempts
        self._backoff = backoff_seconds
        self._sleep = sleep_fn

    def _request_page(self, parameters: Mapping[str, object]) -> Mapping[str, object]:
        params = {
            "serviceKey": self._service_key,
            "resultType": "json",
            **parameters,
        }
        response = None
        for attempt in range(self._max_attempts):
            try:
                response = self._session.get(
                    self._endpoint, params=params,
                    headers={"User-Agent": "stock-investment-rev1/0.1"},
                    timeout=self._timeout,
                )
                if response.status_code == 429:
                    raise DataGoKrApiError("22", "HTTP 429")
                if 400 <= response.status_code < 500:
                    documented = _documented_error_from_response(response)
                    if documented is not None:
                        code, message = documented
                        safe_message = message.replace(self._original_service_key, "[REDACTED]")
                        safe_message = safe_message.replace(self._service_key, "[REDACTED]")
                        raise DataGoKrApiError(code, safe_message)
                    raise DataGoKrHttpError(response.status_code)
                response.raise_for_status()
                break
            except (DataGoKrApiError, DataGoKrHttpError):
                raise
            except requests.RequestException as error:
                if attempt + 1 >= self._max_attempts:
                    raise RuntimeError(
                        f"data.go.kr request failed: {type(error).__name__}"
                    ) from None
                self._sleep(self._backoff * (2 ** attempt))
        try:
            payload = response.json()
        except (ValueError, TypeError):
            raise RuntimeError("data.go.kr response is not valid JSON") from None
        if not isinstance(payload, dict):
            raise RuntimeError("data.go.kr response root must be an object")
        envelope = payload.get("response")
        if not isinstance(envelope, dict):
            raise RuntimeError("data.go.kr response envelope is missing")
        header = envelope.get("header")
        if not isinstance(header, dict):
            raise RuntimeError("data.go.kr response header is missing")
        code = str(header.get("resultCode", "")).zfill(2)
        if code != "00":
            message = str(header.get("resultMsg", ""))
            message = message.replace(self._original_service_key, "[REDACTED]")
            message = message.replace(self._service_key, "[REDACTED]")
            raise DataGoKrApiError(code, message)
        if not isinstance(envelope.get("body"), dict):
            raise RuntimeError("data.go.kr response body is missing")
        return payload

    def fetch_all(
        self, *, filters: Mapping[str, object] | None = None,
        num_of_rows: int = 100, max_pages: int | None = None,
    ) -> DataGoKrResult:
        if num_of_rows < 1 or num_of_rows > 9999:
            raise ValueError("num_of_rows must be between 1 and 9999")
        requested = dict(filters or {})
        if "serviceKey" in requested:
            raise ValueError("serviceKey must not be supplied as a filter")
        page_no = 1
        pages: list[Mapping[str, object]] = []
        rows: list[Mapping[str, object]] = []
        expected_total: int | None = None
        while True:
            if max_pages is not None and page_no > max_pages:
                raise RuntimeError("data.go.kr session page cap reached")
            page = self.fetch_page(
                filters=requested, num_of_rows=num_of_rows, page_no=page_no,
            )
            total = page.total_count
            returned_page = page.page_no
            if returned_page != page_no or total < 0:
                raise RuntimeError("data.go.kr pagination metadata is inconsistent")
            if expected_total is None:
                expected_total = total
            elif total != expected_total:
                raise RuntimeError("data.go.kr totalCount changed during pagination")
            page_rows = list(page.items)
            pages.append(page.payload)
            rows.extend(page_rows)
            if len(rows) >= total:
                break
            if not page_rows:
                raise RuntimeError("data.go.kr pagination ended before totalCount")
            page_no += 1
        if len(rows) != expected_total:
            raise RuntimeError("data.go.kr rows differ from totalCount")
        return DataGoKrResult(tuple(rows), tuple(pages), expected_total or 0)

    def fetch_page(
        self, *, filters: Mapping[str, object] | None = None,
        num_of_rows: int = 1, page_no: int = 1,
    ) -> DataGoKrPage:
        if num_of_rows < 1 or num_of_rows > 9999 or page_no < 1:
            raise ValueError("invalid page request")
        requested = dict(filters or {})
        if "serviceKey" in requested:
            raise ValueError("serviceKey must not be supplied as a filter")
        payload = self._request_page({
            **requested, "numOfRows": num_of_rows, "pageNo": page_no,
        })
        body = payload["response"]["body"]
        try:
            total = int(body["totalCount"])
            returned_page = int(body["pageNo"])
        except (KeyError, TypeError, ValueError):
            raise RuntimeError("data.go.kr pagination metadata is invalid") from None
        if returned_page != page_no or total < 0:
            raise RuntimeError("data.go.kr pagination metadata is inconsistent")
        container = body.get("items") or {}
        item = container.get("item", []) if isinstance(container, dict) else []
        page_rows = item if isinstance(item, list) else [item]
        if not all(isinstance(row, dict) for row in page_rows):
            raise RuntimeError("data.go.kr item list is invalid")
        return DataGoKrPage(tuple(page_rows), payload, returned_page, total)


def write_landing_pages_atomic(pages: tuple[Mapping[str, object], ...], path: Path) -> None:
    """Persist only lossless response JSON; request metadata and keys are excluded."""
    if not pages:
        raise ValueError("landing pages must not be empty")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".json.tmp",
            prefix=path.stem + "_", dir=path.parent, delete=False,
        ) as temporary:
            json.dump(list(pages), temporary, ensure_ascii=False, separators=(",", ":"))
            temporary_path = Path(temporary.name)
        verified = json.loads(temporary_path.read_text(encoding="utf-8"))
        if verified != list(pages):
            raise RuntimeError("landing JSON read-back differs from source response")
        temporary_path.replace(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
