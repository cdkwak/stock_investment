from __future__ import annotations

from datetime import date, datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import time
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

import pandas as pd
import requests

from stock_data.validation.ls_t1633 import normalize_ls_t1633_market_pair


OFFICIAL_BASE_URL = "https://openapi.ls-sec.co.kr:8080"
TOKEN_ENDPOINT = "/oauth2/token"
PROGRAM_ENDPOINT = "/stock/program"
TR_CODE = "t1633"
MAX_DATA_CALLS = 4
MAX_RAW_CALLS = 8
MAX_RETRIES_PER_SCOPE = 1
TRANSIENT_HTTP_STATUS = frozenset({429, 500, 502, 503, 504})


class LST1633ProviderError(RuntimeError):
    pass


def _official_base_url(value: str) -> str:
    parsed = urlparse(value)
    if (
        value != OFFICIAL_BASE_URL
        or parsed.scheme != "https"
        or parsed.hostname != "openapi.ls-sec.co.kr"
        or parsed.port != 8080
        or parsed.path
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise LST1633ProviderError("LS base URL is not the exact official endpoint")
    return value


def _atomic_bytes(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_json(path: Path, payload: object) -> None:
    _atomic_bytes(
        path,
        (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )


def _contains_secret(body: bytes, secrets: tuple[str, ...]) -> bool:
    return any(value and value.encode("utf-8") in body for value in secrets)


def _request_block(market_code: str, selector: str, market_date: date) -> dict[str, object]:
    target = market_date.strftime("%Y%m%d")
    return {
        "gubun": market_code,
        "gubun1": selector,
        "gubun2": "0",
        "gubun3": "1",
        "fdate": target,
        "tdate": target,
        "gubun4": "0",
        "date": " ",
        "exchgubun": "K",
    }


def _exact_row(payload: object, target: str) -> dict[str, object]:
    if not isinstance(payload, dict) or payload.get("rsp_cd") != "00000":
        raise LST1633ProviderError("LS t1633 provider response failed")
    rows = payload.get("t1633OutBlock1")
    if not isinstance(rows, list):
        raise LST1633ProviderError("LS t1633 response rows are missing")
    exact = [row for row in rows if isinstance(row, dict) and str(row.get("date")) == target]
    if len(exact) != 1:
        raise LST1633ProviderError("LS t1633 exact-date row count differs")
    return exact[0]


class LST1633DailyCandidateBuilder:
    """One OAuth plus four logical t1633 calls and one bounded transient retry each."""

    def __init__(
        self,
        *,
        project_root: Path,
        app_key: str,
        app_secret: str,
        base_url: str,
        session: requests.Session | None = None,
    ) -> None:
        if not app_key or not app_secret:
            raise LST1633ProviderError("LS credentials are missing")
        self.project_root = project_root
        self.app_key = app_key
        self.app_secret = app_secret
        self.base_url = _official_base_url(base_url)
        self.session = session or requests.Session()
        self.oauth_calls = 0
        self.data_calls = 0
        self.retry_count = 0
        self.run_dir: Path | None = None

    def _authenticate(self) -> str:
        self.oauth_calls += 1
        response = self.session.post(
            self.base_url + TOKEN_ENDPOINT,
            headers={"content-type": "application/x-www-form-urlencoded"},
            params={
                "grant_type": "client_credentials",
                "appkey": self.app_key,
                "appsecretkey": self.app_secret,
                "scope": "oob",
            },
            timeout=30,
        )
        try:
            payload = response.json()
        except ValueError as error:
            raise LST1633ProviderError("LS OAuth returned non-JSON") from error
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if response.status_code != 200 or not isinstance(token, str) or not token:
            raise LST1633ProviderError("LS OAuth failed")
        return token

    def __call__(self, market_date: date) -> pd.DataFrame:
        if self.oauth_calls or self.data_calls:
            raise LST1633ProviderError("LS t1633 builder instance is single-use")
        token = self._authenticate()
        target = market_date.strftime("%Y%m%d")
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex
        self.run_dir = (
            self.project_root / "data/landing/ls_openapi/t1633_daily" / target / run_id
        )
        captured: dict[tuple[str, str], tuple[dict[str, object], str, datetime]] = {}
        scopes = (
            ("KOSPI", "0", "0", "AMOUNT"),
            ("KOSPI", "0", "1", "QUANTITY"),
            ("KOSDAQ", "1", "0", "AMOUNT"),
            ("KOSDAQ", "1", "1", "QUANTITY"),
        )
        try:
            for sequence, (market, market_code, selector, measure) in enumerate(scopes, start=1):
                label = f"{sequence:02d}_{market.lower()}_{measure.lower()}"
                response = None
                payload: Any = None
                digest = ""
                captured_at = datetime.now(timezone.utc)
                for attempt in range(MAX_RETRIES_PER_SCOPE + 1):
                    if self.data_calls >= MAX_RAW_CALLS:
                        raise LST1633ProviderError("LS t1633 raw call budget exceeded")
                    response = self.session.post(
                        self.base_url + PROGRAM_ENDPOINT,
                        headers={
                            "content-type": "application/json; charset=utf-8",
                            "authorization": f"Bearer {token}",
                            "tr_cd": TR_CODE,
                            "tr_cont": "N",
                            "tr_cont_key": "",
                        },
                        json={"t1633InBlock": _request_block(market_code, selector, market_date)},
                        timeout=30,
                    )
                    self.data_calls += 1
                    body = response.content
                    if _contains_secret(body, (self.app_key, self.app_secret, token)):
                        raise LST1633ProviderError("LS t1633 response echoed a credential")
                    digest = hashlib.sha256(body).hexdigest()
                    captured_at = datetime.now(timezone.utc)
                    try:
                        payload = response.json()
                    except ValueError:
                        payload = None
                    if response.status_code == 200 and isinstance(payload, dict):
                        break
                    transient = response.status_code in TRANSIENT_HTTP_STATUS
                    failure_path = self.run_dir / (
                        f"{label}.retry{attempt + 1}.failure.provenance.json"
                        if transient and attempt < MAX_RETRIES_PER_SCOPE
                        else f"{label}.failure.provenance.json"
                    )
                    _atomic_json(failure_path, {
                        "schema": "stock_data.ls_t1633_daily_failure_v1",
                        "source": "LS_OPENAPI",
                        "operation": TR_CODE,
                        "endpoint": PROGRAM_ENDPOINT,
                        "market": market,
                        "market_code": market_code,
                        "selector": selector,
                        "measure": measure,
                        "market_date": market_date.isoformat(),
                        "captured_at": captured_at.isoformat().replace("+00:00", "Z"),
                        "http_status": response.status_code,
                        "rsp_cd": payload.get("rsp_cd") if isinstance(payload, dict) else None,
                        "failure": "HTTP_ERROR" if isinstance(payload, dict) else "NON_JSON_RESPONSE",
                        "raw_response_sha256": digest,
                        "raw_response_bytes": len(body),
                        "raw_response_persisted": False,
                        "retry_count": attempt,
                        "credentials_persisted": False,
                        "token_persisted": False,
                    })
                    if transient and attempt < MAX_RETRIES_PER_SCOPE:
                        self.retry_count += 1
                        retry_after = response.headers.get("Retry-After", "0")
                        try:
                            delay = min(max(float(retry_after), 0.0), 5.0)
                        except (TypeError, ValueError):
                            delay = 0.0
                        if delay:
                            time.sleep(delay)
                        continue
                    if not isinstance(payload, dict):
                        raise LST1633ProviderError("LS t1633 returned non-JSON")
                    raise LST1633ProviderError(f"LS t1633 HTTP {response.status_code}")
                if response is None or not isinstance(payload, dict):
                    raise LST1633ProviderError("LS t1633 scope did not return JSON")
                row = _exact_row(payload, target)
                _atomic_bytes(self.run_dir / f"{label}.response.json", body)
                _atomic_json(self.run_dir / f"{label}.provenance.json", {
                    "schema": "stock_data.ls_t1633_daily_landing_v1",
                    "source": "LS_OPENAPI",
                    "operation": TR_CODE,
                    "endpoint": PROGRAM_ENDPOINT,
                    "market": market,
                    "market_code": market_code,
                    "selector": selector,
                    "measure": measure,
                    "market_date": market_date.isoformat(),
                    "captured_at": captured_at.isoformat().replace("+00:00", "Z"),
                    "http_status": response.status_code,
                    "rsp_cd": payload.get("rsp_cd"),
                    "row_count": 1,
                    "raw_response_sha256": digest,
                    "retry_count": self.retry_count,
                    "credentials_persisted": False,
                    "token_persisted": False,
                })
                captured[(market, measure)] = (row, digest, captured_at)
            if len(captured) != MAX_DATA_CALLS:
                raise LST1633ProviderError("LS t1633 four-scope capture incomplete")
            frames = []
            for market in ("KOSPI", "KOSDAQ"):
                amount, amount_sha, amount_at = captured[(market, "AMOUNT")]
                quantity, quantity_sha, quantity_at = captured[(market, "QUANTITY")]
                frames.append(normalize_ls_t1633_market_pair(
                    amount_row=amount,
                    quantity_row=quantity,
                    market=market,
                    collected_at=max(amount_at, quantity_at),
                    amount_landing_sha256=amount_sha,
                    quantity_landing_sha256=quantity_sha,
                ))
            _atomic_json(self.run_dir / "checkpoint.json", {
                "schema": "stock_data.ls_t1633_daily_capture_v1",
                "status": "CAPTURE_COMPLETE",
                "market_date": market_date.isoformat(),
                "oauth_calls": self.oauth_calls,
                "data_calls": self.data_calls,
                "retry_count": self.retry_count,
                "landing_responses": MAX_DATA_CALLS,
                "credentials_persisted": False,
                "token_persisted": False,
            })
            return pd.concat(frames, ignore_index=True)
        except Exception as error:
            if self.run_dir is not None:
                _atomic_json(self.run_dir / "checkpoint.json", {
                    "schema": "stock_data.ls_t1633_daily_capture_v1",
                    "status": "CAPTURE_FAILED",
                    "market_date": market_date.isoformat(),
                    "oauth_calls": self.oauth_calls,
                    "data_calls": self.data_calls,
                    "retry_count": self.retry_count,
                    "error_type": type(error).__name__,
                    "credentials_persisted": False,
                    "token_persisted": False,
                })
            raise


__all__ = [
    "LST1633DailyCandidateBuilder",
    "LST1633ProviderError",
    "MAX_DATA_CALLS",
    "OFFICIAL_BASE_URL",
]
