"""Bounded Yahoo per-symbol option-chain research pilot for UR-094.

The live route is authorized only by docs/data/operations/YAHOO_SYMBOL_OPTION_PCR_PILOT.md.
It retains exact public responses before parsing and has no Normalized, Published,
canonical, scheduler, GUI, or Backtest write path.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Mapping
from urllib.parse import quote
from uuid import uuid4
from zoneinfo import ZoneInfo

import requests


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stock_data.providers.yahoo_symbol_options import (  # noqa: E402
    SymbolOptionPCRStatus,
    YahooOptionChainSnapshot,
    YahooSymbolOptionError,
    derive_yahoo_symbol_volume_pcr,
    parse_yahoo_option_chain,
)


KST = ZoneInfo("Asia/Seoul")
XNYS = ZoneInfo("America/New_York")
SYMBOLS = ("SPY", "QQQ", "IWM", "TLT", "SOXX", "SOXL", "TQQQ")
MULTIPLIER_VERIFIED_SYMBOLS = frozenset(SYMBOLS)
EXPIRY_AT_UTC = datetime(2026, 9, 18, tzinfo=timezone.utc)
EXPIRY_EPOCH = 1_789_689_600
EXPIRY_OSI = "260918"
LATEST_COMPLETED_XNYS_SESSION = date(2026, 8, 19)
WINDOW_START_KST = datetime(2026, 8, 20, 19, 30, tzinfo=KST)
WINDOW_END_KST = datetime(2026, 8, 20, 22, 29, 59, tzinfo=KST)
MAX_CALLS = len(SYMBOLS)
TIMEOUT_SECONDS = 20
LANDING_ROOT = ROOT / "data" / "landing" / "yahoo_symbol_option_chain"
URL_TEMPLATE = "https://query1.finance.yahoo.com/v7/finance/options/{symbol}"
OSI_PATTERN = re.compile(r"^(?P<root>[A-Z][A-Z0-9]{0,5})(?P<expiry>\d{6})(?P<side>[CP])(?P<strike>\d{8})$")


class YahooOptionPilotStopped(RuntimeError):
    pass


def _json_bytes(payload: object) -> bytes:
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str) + "\n").encode("utf-8")


def _write_atomic(path: Path, body: bytes, *, replace: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not replace and path.exists():
        raise YahooOptionPilotStopped(f"refusing to overwrite retained file: {path}")
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        if not replace and path.exists():
            raise YahooOptionPilotStopped(f"retained file appeared during write: {path}")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_json_atomic(path: Path, payload: object, *, replace: bool = True) -> None:
    _write_atomic(path, _json_bytes(payload), replace=replace)


def _iso_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _assert_live_window(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise YahooOptionPilotStopped("operation time must be timezone-aware")
    now_kst = now.astimezone(KST)
    if not WINDOW_START_KST <= now_kst <= WINDOW_END_KST:
        raise YahooOptionPilotStopped("outside exact UR-094 pre-XNYS-open operation window; api_calls=0")
    return now_kst


def _validate_osi_contract(snapshot: YahooOptionChainSnapshot) -> None:
    for contract in snapshot.contracts:
        match = OSI_PATTERN.fullmatch(contract.contract_symbol)
        if match is None:
            raise YahooSymbolOptionError(f"non-OSI contract identity: {contract.contract_symbol}")
        if match.group("root") != snapshot.symbol:
            raise YahooSymbolOptionError(f"adjusted or cross-symbol option root: {contract.contract_symbol}")
        if match.group("expiry") != EXPIRY_OSI:
            raise YahooSymbolOptionError(f"contract expiry identity differs: {contract.contract_symbol}")
        expected_side = "C" if contract.side == "CALL" else "P"
        if match.group("side") != expected_side:
            raise YahooSymbolOptionError(f"contract side identity differs: {contract.contract_symbol}")
        try:
            encoded = Decimal(match.group("strike")) / Decimal(1000)
            observed = Decimal(str(contract.strike))
        except InvalidOperation as exc:
            raise YahooSymbolOptionError(f"contract strike identity is invalid: {contract.contract_symbol}") from exc
        if encoded != observed:
            raise YahooSymbolOptionError(f"contract strike identity differs: {contract.contract_symbol}")


def _latest_standard_trade_by_side(snapshot: YahooOptionChainSnapshot) -> dict[str, datetime | None]:
    result: dict[str, datetime | None] = {"CALL": None, "PUT": None}
    for contract in snapshot.contracts:
        if contract.contract_size != "REGULAR" or contract.last_trade_at_utc is None:
            continue
        current = result[contract.side]
        if current is None or contract.last_trade_at_utc > current:
            result[contract.side] = contract.last_trade_at_utc
    return result


def _serialize_result(result, *, landing_sha256: str, trade_freshness: Mapping[str, object]) -> dict[str, object]:
    payload = asdict(result)
    payload["status"] = result.status.value
    for key in (
        "captured_at_utc", "captured_at_kst", "latest_contract_trade_at_utc",
        "latest_contract_trade_at_kst",
    ):
        value = payload[key]
        payload[key] = value.isoformat() if isinstance(value, datetime) else value
    payload["landing_sha256"] = landing_sha256
    payload["trade_freshness"] = dict(trade_freshness)
    return payload


def evaluate_retained_body(
    body: bytes,
    *,
    symbol: str,
    captured_at_utc: datetime,
    landing_sha256: str,
    evaluated_at_utc: datetime | None = None,
) -> dict[str, object]:
    """Evaluate one already-retained response without constructing a request."""
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise YahooSymbolOptionError("retained response is not valid JSON") from exc
    if not isinstance(payload, Mapping):
        raise YahooSymbolOptionError("retained response root must be an object")
    snapshot = parse_yahoo_option_chain(payload, symbol=symbol, captured_at_utc=captured_at_utc)
    if snapshot.expiry_at_utc != EXPIRY_AT_UTC:
        raise YahooSymbolOptionError("provider expiry differs from selected expiry")
    _validate_osi_contract(snapshot)
    derived = derive_yahoo_symbol_volume_pcr(
        symbol,
        (snapshot,),
        multiplier_verified_symbols=MULTIPLIER_VERIFIED_SYMBOLS,
        now_utc=evaluated_at_utc or captured_at_utc,
    )
    latest = _latest_standard_trade_by_side(snapshot)
    latest_dates = {
        side: value.astimezone(XNYS).date().isoformat() if value is not None else None
        for side, value in latest.items()
    }
    trade_fresh = all(value == LATEST_COMPLETED_XNYS_SESSION.isoformat() for value in latest_dates.values())
    freshness = {
        "required_latest_completed_xnys_session": LATEST_COMPLETED_XNYS_SESSION.isoformat(),
        "latest_regular_trade_date_by_side_xnys": latest_dates,
        "passed": trade_fresh,
    }
    result = _serialize_result(derived, landing_sha256=landing_sha256, trade_freshness=freshness)
    if not trade_fresh:
        result.update({
            "value": None,
            "status": "STALE_TRADE_EVIDENCE",
            "reason": "At least one REGULAR call/put side lacks a trade on the fixed latest completed XNYS session.",
        })
    return result


def _retain_response(
    run_dir: Path,
    *,
    symbol: str,
    body: bytes,
    status_code: int | None,
    content_type: str,
    request_started_at_utc: datetime,
    request_ended_at_utc: datetime,
    captured_at_utc: datetime,
    transport_error: str | None = None,
) -> tuple[Path, dict[str, object]]:
    symbol_dir = run_dir / symbol
    stage = run_dir / f".{symbol}.stage"
    if symbol_dir.exists() or stage.exists():
        raise YahooOptionPilotStopped(f"duplicate retained symbol scope: {symbol}")
    stage.mkdir(parents=False)
    digest = hashlib.sha256(body).hexdigest()
    metadata = {
        "operation_id": "ur094-yahoo-option-20260918-20260820",
        "symbol": symbol,
        "expiry_at_utc": _iso_utc(EXPIRY_AT_UTC),
        "expiry_epoch": EXPIRY_EPOCH,
        "request_url": URL_TEMPLATE.format(symbol=quote(symbol, safe="")),
        "request_parameters": {"date": str(EXPIRY_EPOCH)},
        "request_started_at_utc": _iso_utc(request_started_at_utc),
        "request_ended_at_utc": _iso_utc(request_ended_at_utc),
        "captured_at_utc": _iso_utc(captured_at_utc),
        "captured_at_kst": captured_at_utc.astimezone(KST).isoformat(),
        "http_status": status_code,
        "response_content_type": content_type,
        "response_bytes": len(body),
        "response_sha256": digest,
        "timeout_seconds": TIMEOUT_SECONDS,
        "retry_count": 0,
        "transport_error": transport_error,
        "landing_body_file": f"{EXPIRY_EPOCH}.response",
    }
    try:
        _write_atomic(stage / f"{EXPIRY_EPOCH}.response", body, replace=False)
        _write_json_atomic(stage / f"{EXPIRY_EPOCH}.metadata.json", metadata, replace=False)
        if hashlib.sha256((stage / f"{EXPIRY_EPOCH}.response").read_bytes()).hexdigest() != digest:
            raise YahooOptionPilotStopped("retained response read-back hash differs")
        stage.replace(symbol_dir)
    except BaseException:
        if stage.exists():
            for child in stage.iterdir():
                child.unlink(missing_ok=True)
            stage.rmdir()
        raise
    return symbol_dir, metadata


def _new_manifest(capture_id: str) -> dict[str, object]:
    return {
        "operation_id": "ur094-yahoo-option-20260918-20260820",
        "capture_id": capture_id,
        "status": "IN_PROGRESS",
        "selected_symbols": list(SYMBOLS),
        "selected_expiry_at_utc": _iso_utc(EXPIRY_AT_UTC),
        "selected_expiry_epoch": EXPIRY_EPOCH,
        "latest_completed_xnys_session": LATEST_COMPLETED_XNYS_SESSION.isoformat(),
        "call_budget": MAX_CALLS,
        "calls_consumed": 0,
        "retry_count": 0,
        "conditional_symbols_called": [],
        "price_only_symbols_called": [],
        "scopes": [],
        "results": {},
        "forbidden_mutations": {
            "normalized": 0,
            "published": 0,
            "canonical": 0,
            "backtest": 0,
            "scheduler": 0,
        },
    }


def _failure_result(
    *, symbol: str, status: str, reason: str, captured_at_utc: datetime, landing_sha256: str,
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "value": None,
        "status": status,
        "reason": reason,
        "landing_sha256": landing_sha256,
        "captured_at_utc": _iso_utc(captured_at_utc),
        "backtest_eligible": False,
    }


def _option_chain_root_valid(body: bytes) -> bool:
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(payload, Mapping):
        return False
    chain = payload.get("optionChain")
    return (
        isinstance(chain, Mapping)
        and chain.get("error") is None
        and isinstance(chain.get("result"), list)
    )


def run_live(*, session=requests, now_fn=lambda: datetime.now(timezone.utc), landing_root: Path = LANDING_ROOT) -> dict[str, object]:
    now = now_fn()
    _assert_live_window(now)
    capture_id = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ") + "_" + uuid4().hex
    run_dir = landing_root / capture_id
    run_dir.mkdir(parents=True, exist_ok=False)
    manifest = _new_manifest(capture_id)
    _write_json_atomic(run_dir / "manifest.json", manifest, replace=False)
    global_stop: str | None = None
    for symbol in SYMBOLS:
        if int(manifest["calls_consumed"]) >= MAX_CALLS:
            global_stop = "CALL_BUDGET_EXHAUSTED"
            break
        url = URL_TEMPLATE.format(symbol=quote(symbol, safe=""))
        request_started = now_fn().astimezone(timezone.utc)
        body = b""
        response = None
        error = None
        try:
            response = session.get(
                url,
                params={"date": EXPIRY_EPOCH},
                headers={"Accept": "application/json", "User-Agent": "stock-investment-rev1/0.1"},
                timeout=TIMEOUT_SECONDS,
                allow_redirects=False,
            )
            body = response.content if isinstance(response.content, bytes) else bytes(response.content)
        except requests.RequestException as exc:
            error = type(exc).__name__
        request_ended = now_fn().astimezone(timezone.utc)
        captured = request_ended
        manifest["calls_consumed"] = int(manifest["calls_consumed"]) + 1
        status_code = int(response.status_code) if response is not None else None
        content_type = str(response.headers.get("Content-Type", "")) if response is not None else ""
        _, metadata = _retain_response(
            run_dir,
            symbol=symbol,
            body=body,
            status_code=status_code,
            content_type=content_type,
            request_started_at_utc=request_started,
            request_ended_at_utc=request_ended,
            captured_at_utc=captured,
            transport_error=error,
        )
        scope = {
            "symbol": symbol,
            "call_sequence": manifest["calls_consumed"],
            "http_status": status_code,
            "response_sha256": metadata["response_sha256"],
            "retained": True,
            "retry_count": 0,
        }
        manifest["scopes"].append(scope)
        if error is not None:
            global_stop = f"TRANSPORT_ERROR:{error}"
        elif status_code in {301, 302, 303, 307, 308}:
            global_stop = f"REDIRECT_FORBIDDEN:{status_code}"
        elif status_code in {401, 403, 429}:
            global_stop = f"HTTP_RESTRICTION:{status_code}"
        elif status_code != 200:
            global_stop = f"HTTP_STATUS:{status_code}"
        elif "json" not in content_type.lower():
            global_stop = "NON_JSON_CONTENT_TYPE"
        elif not _option_chain_root_valid(body):
            global_stop = "OPTION_CHAIN_ROOT_INVALID"
        if global_stop is not None:
            result = _failure_result(
                symbol=symbol,
                status="GLOBAL_TRANSPORT_STOP",
                reason=global_stop,
                captured_at_utc=captured,
                landing_sha256=str(metadata["response_sha256"]),
            )
        else:
            try:
                result = evaluate_retained_body(
                    body,
                    symbol=symbol,
                    captured_at_utc=captured,
                    landing_sha256=str(metadata["response_sha256"]),
                )
            except YahooSymbolOptionError as exc:
                result = _failure_result(
                    symbol=symbol,
                    status="MALFORMED_OR_EMPTY",
                    reason=str(exc),
                    captured_at_utc=captured,
                    landing_sha256=str(metadata["response_sha256"]),
                )
        scope["outcome_status"] = result["status"]
        scope["global_stop_reason"] = global_stop
        manifest["results"][symbol] = result
        _write_json_atomic(run_dir / "manifest.json", manifest)
        if global_stop is not None:
            break
    attempted = {str(scope["symbol"]) for scope in manifest["scopes"]}
    manifest["not_attempted_symbols"] = [symbol for symbol in SYMBOLS if symbol not in attempted]
    manifest["global_stop_reason"] = global_stop
    manifest["status"] = "LIVE_ATTEMPT_COMPLETE"
    manifest["completed_at_utc"] = _iso_utc(now_fn())
    _write_json_atomic(run_dir / "manifest.json", manifest)
    return {"run_dir": str(run_dir), **manifest}


def replay_run(run_dir: Path) -> dict[str, object]:
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    replayed_at = datetime.now(timezone.utc)
    replay_results: dict[str, object] = {}
    mismatches: list[str] = []
    for scope in manifest.get("scopes", []):
        symbol = str(scope["symbol"])
        symbol_dir = run_dir / symbol
        metadata = json.loads((symbol_dir / f"{EXPIRY_EPOCH}.metadata.json").read_text(encoding="utf-8"))
        body = (symbol_dir / f"{EXPIRY_EPOCH}.response").read_bytes()
        digest = hashlib.sha256(body).hexdigest()
        if digest != metadata["response_sha256"]:
            raise YahooOptionPilotStopped(f"Landing hash mismatch: {symbol}")
        captured = datetime.fromisoformat(str(metadata["captured_at_utc"]).replace("Z", "+00:00"))
        global_stop = scope.get("global_stop_reason")
        if global_stop:
            result = _failure_result(
                symbol=symbol,
                status="GLOBAL_TRANSPORT_STOP",
                reason=str(global_stop),
                captured_at_utc=captured,
                landing_sha256=digest,
            )
        else:
            try:
                result = evaluate_retained_body(
                    body,
                    symbol=symbol,
                    captured_at_utc=captured,
                    landing_sha256=digest,
                    evaluated_at_utc=replayed_at,
                )
            except YahooSymbolOptionError as exc:
                result = _failure_result(
                    symbol=symbol,
                    status="MALFORMED_OR_EMPTY",
                    reason=str(exc),
                    captured_at_utc=captured,
                    landing_sha256=digest,
                )
        replay_results[symbol] = result
        if result != manifest.get("results", {}).get(symbol):
            mismatches.append(symbol)
    return {
        "operation_id": manifest["operation_id"],
        "capture_id": manifest["capture_id"],
        "replayed_at_utc": _iso_utc(replayed_at),
        "api_calls": 0,
        "retry_count": 0,
        "landing_hashes_verified": len(manifest.get("scopes", [])),
        "replay_results": replay_results,
        "replay_mismatches": mismatches,
        "replay_matches_live": not mismatches,
        "forbidden_mutations": manifest["forbidden_mutations"],
    }


def write_checkpoint(run_dir: Path, artifact_path: Path) -> dict[str, object]:
    manifest_path = run_dir / "manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    replay = replay_run(run_dir)
    checkpoint = {
        "queue_id": "UR-094",
        "recorded_at_kst": datetime.now(KST).isoformat(),
        "result": (
            "BOUNDED_LIVE_ATTEMPT_AND_API0_REPLAY_COMPLETE"
            if replay["replay_matches_live"]
            else "API0_REPLAY_MISMATCH_FAIL_CLOSED"
        ),
        "operation_id": manifest["operation_id"],
        "landing": {
            "run_dir": str(run_dir),
            "manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
            "retained_scope_count": len(manifest.get("scopes", [])),
        },
        "live_ledger": {
            "planned_symbols": manifest["selected_symbols"],
            "calls_consumed": manifest["calls_consumed"],
            "retry_count": manifest["retry_count"],
            "not_attempted_symbols": manifest.get("not_attempted_symbols", []),
            "global_stop_reason": manifest.get("global_stop_reason"),
            "scopes": manifest.get("scopes", []),
            "results": manifest.get("results", {}),
            "conditional_symbols_called": manifest["conditional_symbols_called"],
            "price_only_symbols_called": manifest["price_only_symbols_called"],
        },
        "api_zero_replay": replay,
        "promotions": 0,
        "scheduler_mutations": 0,
        "backtest_mutations": 0,
        "normalized_or_published_mutations": 0,
        "operations_not_to_repeat": [
            "This UR-094 live scope and every consumed symbol call",
            "UR-021 licensed-source work",
            "UR-069 numeric-free GUI composition",
            "UR-073 accepted API-zero implementation tests",
        ],
    }
    _write_json_atomic(artifact_path, checkpoint, replace=False)
    return checkpoint


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--execute-live", action="store_true")
    mode.add_argument("--replay-run", type=Path)
    parser.add_argument("--artifact-output", type=Path)
    args = parser.parse_args()
    if args.execute_live:
        if args.artifact_output is not None:
            parser.error("--artifact-output is valid only with --replay-run")
        result = run_live()
    else:
        run_dir = args.replay_run.resolve()
        result = (
            write_checkpoint(run_dir, args.artifact_output.resolve())
            if args.artifact_output is not None
            else replay_run(run_dir)
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
