"""One-use, unauthenticated Yahoo option requests-fallback pilot for UR-098.

This script intentionally does not import yfinance, use curl_cffi, send POST
requests, load configuration, or access opaque authentication material.  It is
not an application client, scheduler entry point, or numeric-data promotion.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import requests


SYMBOL = "QQQ"
BASE_URL = "https://query2.finance.yahoo.com/v7/finance/options/QQQ"
TIMEOUT_SECONDS = 10
BUSINESS_CALL_BUDGET = 2


class PilotValidationError(ValueError):
    """A response is not an accepted as-retrieved option observation."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _write_bytes_atomic(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        handle.write(body)
        temporary = Path(handle.name)
    temporary.replace(path)


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    _write_bytes_atomic(path, json.dumps(payload, sort_keys=True, indent=2).encode("utf-8"))


def _decode_json(body: bytes) -> Mapping[str, Any]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PilotValidationError("JSON_DECODE_FAILED") from exc
    if not isinstance(payload, Mapping):
        raise PilotValidationError("JSON_ROOT_NOT_OBJECT")
    return payload


def _single_root(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    chain = payload.get("optionChain")
    if not isinstance(chain, Mapping) or chain.get("error") is not None:
        raise PilotValidationError("OPTION_CHAIN_ROOT_INVALID")
    result = chain.get("result")
    if not isinstance(result, list) or len(result) != 1 or not isinstance(result[0], Mapping):
        raise PilotValidationError("OPTION_CHAIN_RESULT_INVALID")
    root = result[0]
    quote = root.get("quote")
    if not isinstance(quote, Mapping) or str(quote.get("symbol", "")).upper() != SYMBOL:
        raise PilotValidationError("SYMBOL_MISMATCH")
    return root


def _finite_number(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(float(value))
    )


def _nonnegative_number(value: object) -> bool:
    return _finite_number(value) and float(value) >= 0


def _nullable_nonnegative_integer(value: object) -> bool:
    return value is None or (
        _nonnegative_number(value) and int(float(value)) == float(value)
    )


def parse_expirations(payload: Mapping[str, Any], *, captured_at_utc: datetime) -> tuple[int, ...]:
    """Validate and return the future expiration epoch list without exposing values."""
    root = _single_root(payload)
    values = root.get("expirationDates")
    if not isinstance(values, list) or not values:
        raise PilotValidationError("EXPIRATION_LIST_EMPTY_OR_INVALID")
    captured_epoch = int(captured_at_utc.timestamp())
    expirations = tuple(
        int(value)
        for value in values
        if _nonnegative_number(value) and int(float(value)) == float(value) and int(value) > captured_epoch
    )
    if len(expirations) != len(values) or len(set(expirations)) != len(expirations):
        raise PilotValidationError("EXPIRATION_LIST_INVALID")
    return expirations


def validate_nearest_chain(
    payload: Mapping[str, Any], *, nearest_expiry: int, captured_at_utc: datetime,
) -> dict[str, object]:
    """Check the requested schema without deriving or displaying numeric results."""
    root = _single_root(payload)
    quote = root["quote"]
    currency = quote.get("currency")
    timezone_name = quote.get("exchangeTimezoneName")
    if not isinstance(currency, str) or not currency:
        raise PilotValidationError("CURRENCY_MISSING")
    if not isinstance(timezone_name, str) or not timezone_name:
        raise PilotValidationError("TIMEZONE_MISSING")
    options = root.get("options")
    if not isinstance(options, list) or len(options) != 1 or not isinstance(options[0], Mapping):
        raise PilotValidationError("NEAREST_OPTION_OBJECT_INVALID")
    option = options[0]
    if option.get("expirationDate") != nearest_expiry:
        raise PilotValidationError("NEAREST_EXPIRY_MISMATCH")
    captured_epoch = int(captured_at_utc.astimezone(timezone.utc).timestamp())
    side_counts: dict[str, int] = {}
    nullable_volume_count = 0
    nullable_open_interest_count = 0
    for field in ("calls", "puts"):
        rows = option.get(field)
        if not isinstance(rows, list) or not rows:
            raise PilotValidationError(f"{field.upper()}_EMPTY_OR_INVALID")
        for row in rows:
            if not isinstance(row, Mapping):
                raise PilotValidationError(f"{field.upper()}_ROW_INVALID")
            if not isinstance(row.get("contractSymbol"), str) or not row["contractSymbol"]:
                raise PilotValidationError("CONTRACT_SYMBOL_INVALID")
            if "expirationDate" not in row:
                raise PilotValidationError("ROW_EXPIRY_MISSING")
            row_expiry = row["expirationDate"]
            if not (
                _nonnegative_number(row_expiry)
                and int(float(row_expiry)) == float(row_expiry)
            ):
                raise PilotValidationError("ROW_EXPIRY_INVALID")
            if int(row_expiry) != nearest_expiry:
                raise PilotValidationError("ROW_EXPIRY_MISMATCH")
            last_trade = row.get("lastTradeDate")
            if not (
                _nonnegative_number(last_trade)
                and int(float(last_trade)) == float(last_trade)
                and int(last_trade) > 0
            ):
                raise PilotValidationError("LAST_TRADE_DATE_INVALID")
            if int(last_trade) > captured_epoch:
                raise PilotValidationError("LAST_TRADE_DATE_AFTER_CAPTURE")
            for numeric_field in ("strike", "bid", "ask", "impliedVolatility"):
                if not _nonnegative_number(row.get(numeric_field)):
                    raise PilotValidationError(f"{numeric_field.upper()}_INVALID")
            if float(row["bid"]) > float(row["ask"]):
                raise PilotValidationError("CROSSED_BID_ASK")
            if not _nullable_nonnegative_integer(row.get("volume")):
                raise PilotValidationError("VOLUME_INVALID")
            if not _nullable_nonnegative_integer(row.get("openInterest")):
                raise PilotValidationError("OPEN_INTEREST_INVALID")
            nullable_volume_count += int(row.get("volume") is None)
            nullable_open_interest_count += int(row.get("openInterest") is None)
        side_counts[field] = len(rows)
    return {
        "schema_valid": True,
        "calls_row_count": side_counts["calls"],
        "puts_row_count": side_counts["puts"],
        "currency_present": True,
        "timezone_present": True,
        "row_expiry_last_trade_strike_bid_ask_iv_valid": True,
        "volume_nullable_row_count": nullable_volume_count,
        "open_interest_nullable_row_count": nullable_open_interest_count,
    }


def _capture_success(
    capture_root: Path,
    *,
    label: str,
    url: str,
    started_at: datetime,
    ended_at: datetime,
    body: bytes,
) -> tuple[str, str]:
    body_sha256 = hashlib.sha256(body).hexdigest()
    directory = capture_root / label
    _write_bytes_atomic(directory / "response.json", body)
    metadata = {
        "symbol": SYMBOL,
        "route": "QUERY2_UNAUTHENTICATED_REQUESTS_FALLBACK",
        "request_url": url,
        "request_started_at_utc": _iso(started_at),
        "request_ended_at_utc": _iso(ended_at),
        "captured_at_utc": _iso(ended_at),
        "timeout_seconds": TIMEOUT_SECONDS,
        "retry_count": 0,
        "request_method": "GET",
        "body_sha256": body_sha256,
        "body_bytes": len(body),
        "response_headers_recorded": False,
        "opaque_authentication_material_recorded": False,
    }
    metadata_body = json.dumps(metadata, sort_keys=True, indent=2).encode("utf-8")
    _write_bytes_atomic(directory / "metadata.json", metadata_body)
    return body_sha256, hashlib.sha256(metadata_body).hexdigest()


def _readback_verified(path: Path, expected_sha256: str) -> bytes:
    body = path.read_bytes()
    if hashlib.sha256(body).hexdigest() != expected_sha256:
        raise PilotValidationError("LANDING_READBACK_HASH_MISMATCH")
    return body


def _readback_metadata(
    path: Path,
    *,
    expected_sha256: str,
    expected_body_sha256: str,
) -> datetime:
    body = _readback_verified(path, expected_sha256)
    metadata = _decode_json(body)
    if metadata.get("body_sha256") != expected_body_sha256:
        raise PilotValidationError("LANDING_METADATA_BODY_HASH_MISMATCH")
    captured = metadata.get("captured_at_utc")
    if not isinstance(captured, str):
        raise PilotValidationError("LANDING_CAPTURE_TIME_INVALID")
    try:
        value = datetime.fromisoformat(captured.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PilotValidationError("LANDING_CAPTURE_TIME_INVALID") from exc
    if value.tzinfo is None or value.utcoffset() is None:
        raise PilotValidationError("LANDING_CAPTURE_TIME_INVALID")
    return value.astimezone(timezone.utc)


def _request(
    session: requests.Session,
    url: str,
) -> tuple[requests.Response | None, datetime, datetime, str | None]:
    started_at = _utc_now()
    try:
        response = session.get(url, timeout=TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        return None, started_at, _utc_now(), type(exc).__name__
    return response, started_at, _utc_now(), None


def run_live(*, landing_root: Path, artifact_path: Path) -> dict[str, object]:
    """Perform the exact two-or-fewer-GET pilot and write sanitized evidence."""
    capture_id = f"ur098-qqq-{_utc_now().strftime('%Y%m%dT%H%M%S.%fZ')}-{uuid.uuid4().hex}"
    capture_root = landing_root / capture_id
    ledger: dict[str, object] = {
        "queue_id": "UR-098",
        "symbol": SYMBOL,
        "route": "QUERY2_UNAUTHENTICATED_REQUESTS_FALLBACK",
        "timeout_seconds": TIMEOUT_SECONDS,
        "retry_count": 0,
        "support_get_calls": 0,
        "business_call_budget": BUSINESS_CALL_BUDGET,
        "business_calls_consumed": 0,
        "post_attempts": 0,
        "capture_id": capture_id,
        "numeric_data_accepted": False,
        "dashboard_mutation": False,
        "automation_mutation": False,
        "backtest_mutation": False,
        "opaque_authentication_material_recorded": False,
    }
    session = requests.Session()

    response, started, ended, transport_error = _request(session, BASE_URL)
    ledger["business_calls_consumed"] = 1
    if transport_error is not None:
        ledger.update(outcome="TESTED_ROUTE_TRANSPORT_FAILURE", exception_type=transport_error)
        _write_json_atomic(artifact_path, ledger)
        return ledger
    assert response is not None
    if response.status_code != 200:
        outcome = (
            "TESTED_ROUTE_UNAUTHORIZED" if response.status_code in (401, 403)
            else "TESTED_ROUTE_RATE_LIMITED" if response.status_code == 429
            else f"TESTED_ROUTE_HTTP_{response.status_code}"
        )
        ledger.update(outcome=outcome, response_body_retained=False)
        _write_json_atomic(artifact_path, ledger)
        return ledger
    try:
        first_body = bytes(response.content)
        first_payload = _decode_json(first_body)
        _single_root(first_payload)
    except PilotValidationError as exc:
        ledger.update(outcome=str(exc), response_body_retained=False)
        _write_json_atomic(artifact_path, ledger)
        return ledger
    first_sha256, first_metadata_sha256 = _capture_success(
        capture_root, label="expiration_list", url=BASE_URL,
        started_at=started, ended_at=ended, body=first_body,
    )
    try:
        retained_first = _readback_verified(
            capture_root / "expiration_list" / "response.json", first_sha256,
        )
        first_capture = _readback_metadata(
            capture_root / "expiration_list" / "metadata.json",
            expected_sha256=first_metadata_sha256,
            expected_body_sha256=first_sha256,
        )
        expirations = parse_expirations(_decode_json(retained_first), captured_at_utc=first_capture)
    except PilotValidationError as exc:
        ledger.update(
            outcome=str(exc), expiration_list_retained=True,
            expiration_list_body_sha256=first_sha256,
            expiration_list_metadata_sha256=first_metadata_sha256,
        )
        _write_json_atomic(artifact_path, ledger)
        return ledger
    nearest_expiry = min(expirations)
    chain_url = f"{BASE_URL}?date={nearest_expiry}"
    response, started, ended, transport_error = _request(session, chain_url)
    ledger["business_calls_consumed"] = 2
    if transport_error is not None:
        ledger.update(
            outcome="TESTED_ROUTE_TRANSPORT_FAILURE",
            exception_type=transport_error,
            expiration_list_retained=True,
            expiration_list_body_sha256=first_sha256,
        )
        _write_json_atomic(artifact_path, ledger)
        return ledger
    assert response is not None
    if response.status_code != 200:
        outcome = (
            "TESTED_ROUTE_UNAUTHORIZED" if response.status_code in (401, 403)
            else "TESTED_ROUTE_RATE_LIMITED" if response.status_code == 429
            else f"TESTED_ROUTE_HTTP_{response.status_code}"
        )
        ledger.update(
            outcome=outcome,
            response_body_retained=False,
            expiration_list_retained=True,
            expiration_list_body_sha256=first_sha256,
        )
        _write_json_atomic(artifact_path, ledger)
        return ledger
    try:
        chain_body = bytes(response.content)
        chain_payload = _decode_json(chain_body)
        _single_root(chain_payload)
    except PilotValidationError as exc:
        ledger.update(
            outcome=str(exc),
            expiration_list_retained=True,
            expiration_list_body_sha256=first_sha256,
            response_body_retained=False,
        )
        _write_json_atomic(artifact_path, ledger)
        return ledger
    chain_sha256, chain_metadata_sha256 = _capture_success(
        capture_root, label="nearest_chain", url=chain_url,
        started_at=started, ended_at=ended, body=chain_body,
    )
    try:
        retained_chain = _readback_verified(
            capture_root / "nearest_chain" / "response.json", chain_sha256,
        )
        chain_capture = _readback_metadata(
            capture_root / "nearest_chain" / "metadata.json",
            expected_sha256=chain_metadata_sha256,
            expected_body_sha256=chain_sha256,
        )
        schema = validate_nearest_chain(
            _decode_json(retained_chain), nearest_expiry=nearest_expiry,
            captured_at_utc=chain_capture,
        )
    except PilotValidationError as exc:
        ledger.update(
            outcome=str(exc), expiration_list_retained=True,
            expiration_list_body_sha256=first_sha256,
            nearest_chain_retained=True,
            nearest_chain_body_sha256=chain_sha256,
            nearest_chain_metadata_sha256=chain_metadata_sha256,
        )
        _write_json_atomic(artifact_path, ledger)
        return ledger
    ledger.update(
        outcome="AS_RETRIEVED_SCHEMA_VALID_NUMERIC_DATA_NOT_ACCEPTED",
        expiration_list_retained=True,
        expiration_list_body_sha256=first_sha256,
        expiration_list_metadata_sha256=first_metadata_sha256,
        nearest_chain_retained=True,
        nearest_chain_body_sha256=chain_sha256,
        nearest_chain_metadata_sha256=chain_metadata_sha256,
        nearest_expiry_selected=True,
        **schema,
    )
    _write_json_atomic(artifact_path, ledger)
    return ledger


def replay(*, artifact_path: Path, landing_root: Path) -> dict[str, object]:
    """Validate retained successful bodies without constructing network access."""
    ledger = json.loads(artifact_path.read_text(encoding="utf-8"))
    capture_id = ledger.get("capture_id")
    if not isinstance(capture_id, str):
        raise PilotValidationError("CHECKPOINT_CAPTURE_ID_INVALID")
    root = landing_root / capture_id
    replay_result: dict[str, object] = {"api_calls": 0, "retry_count": 0, "replay_valid": False}
    if ledger.get("outcome") != "AS_RETRIEVED_SCHEMA_VALID_NUMERIC_DATA_NOT_ACCEPTED":
        replay_result["replay_valid"] = True
        return replay_result
    first_body = (root / "expiration_list" / "response.json").read_bytes()
    chain_body = (root / "nearest_chain" / "response.json").read_bytes()
    if hashlib.sha256(first_body).hexdigest() != ledger.get("expiration_list_body_sha256"):
        raise PilotValidationError("EXPIRATION_LANDING_HASH_MISMATCH")
    if hashlib.sha256(chain_body).hexdigest() != ledger.get("nearest_chain_body_sha256"):
        raise PilotValidationError("CHAIN_LANDING_HASH_MISMATCH")
    first_capture = _readback_metadata(
        root / "expiration_list" / "metadata.json",
        expected_sha256=str(ledger.get("expiration_list_metadata_sha256")),
        expected_body_sha256=str(ledger.get("expiration_list_body_sha256")),
    )
    chain_capture = _readback_metadata(
        root / "nearest_chain" / "metadata.json",
        expected_sha256=str(ledger.get("nearest_chain_metadata_sha256")),
        expected_body_sha256=str(ledger.get("nearest_chain_body_sha256")),
    )
    expirations = parse_expirations(_decode_json(first_body), captured_at_utc=first_capture)
    nearest = _single_root(_decode_json(chain_body))["options"][0]["expirationDate"]
    if not isinstance(nearest, int):
        raise PilotValidationError("RETAINED_NEAREST_EXPIRY_INVALID")
    if nearest not in expirations:
        raise PilotValidationError("RETAINED_NEAREST_EXPIRY_NOT_IN_LIST")
    replay_result.update(
        replay_valid=bool(
            validate_nearest_chain(
                _decode_json(chain_body), nearest_expiry=nearest,
                captured_at_utc=chain_capture,
            )
        )
    )
    return replay_result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--landing-root", type=Path, default=Path("data/landing/yahoo_option_requests_fallback"))
    parser.add_argument("--artifact", type=Path, default=Path("artifacts/agent_runs/ur098_yahoo_option_requests_fallback.json"))
    parser.add_argument("--replay", action="store_true")
    args = parser.parse_args()
    if args.replay:
        result = replay(artifact_path=args.artifact, landing_root=args.landing_root)
        checkpoint = json.loads(args.artifact.read_text(encoding="utf-8"))
        checkpoint["api_zero_replay"] = result
        _write_json_atomic(args.artifact, checkpoint)
    else:
        result = run_live(landing_root=args.landing_root, artifact_path=args.artifact)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
