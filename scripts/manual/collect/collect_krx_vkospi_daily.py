"""One explicitly finalized KRX VKOSPI capture plus offline daily append."""
from __future__ import annotations

import argparse
from contextlib import redirect_stderr, redirect_stdout
from datetime import date, datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.manual.pilot.pilot_pykrx_etf import _load_credentials  # noqa: E402
from scripts.manual.pilot.pykrx_etf_pilot_support import (  # noqa: E402
    AppendOnlyLedger,
    PilotStopped,
    shared_d_owned_krx_lock,
    write_bytes_atomic_new,
    write_json_atomic,
)
from stock_data.contracts.vkospi_daily import KR_VKOSPI_DAILY, KR_VKOSPI_RAW_DAILY  # noqa: E402
from stock_data.orchestration.vkospi_daily_incremental import run_offline_daily_append  # noqa: E402
from stock_data.providers.krx_mdc.vkospi import (  # noqa: E402
    BUSINESS_URL,
    OFFICIAL_CODE,
    parse_history_body,
    request_payload,
)
from stock_data.storage.contract_parquet import read_dataset  # noqa: E402
from stock_data.validation.vkospi_daily import (  # noqa: E402
    validate_vkospi_daily,
    validate_vkospi_raw_daily,
)


def _verified_daily_noop(root: Path, market_date: str) -> dict[str, object] | None:
    checkpoint = root / "data/state/kr_vkospi_daily.json"
    if not checkpoint.is_file():
        return None
    state = json.loads(checkpoint.read_text(encoding="utf-8"))
    if state.get("status") != "DAILY_INCREMENTAL_ACCEPTED_PIT_LIMITED":
        return None
    if state.get("last_accepted_market_date") != market_date:
        return None
    raw = read_dataset(
        root / "data/raw/kr_vkospi_daily", KR_VKOSPI_RAW_DAILY, validate_vkospi_raw_daily,
    )
    normalized = read_dataset(
        root / "data/normalized/kr_vkospi_daily", KR_VKOSPI_DAILY, validate_vkospi_daily,
    )
    if raw.loc[raw["market_date"].eq(market_date)].shape[0] != 1:
        raise PilotStopped("CHECKPOINT_CONFLICT: Raw accepted-date row is not exact")
    if normalized.loc[normalized["market_date"].eq(market_date)].shape[0] != 1:
        raise PilotStopped("CHECKPOINT_CONFLICT: Normalized accepted-date row is not exact")
    return {
        "status": "NOOP_IDEMPOTENT",
        "market_date": market_date,
        "business_calls": 0,
        "retry_count": 0,
        "raw_mutation": False,
        "normalized_mutation": False,
        "retained_rows": len(normalized),
    }


def collect_one_finalized_date(
    root: Path,
    *,
    market_date: date,
    finality_confirmed: bool,
    session=None,
    credentials: tuple[str | None, str | None] | None = None,
) -> dict[str, object]:
    if not finality_confirmed:
        raise PilotStopped("explicit finalized-date confirmation is required")
    root = root.resolve()
    selected = market_date.isoformat()
    if noop := _verified_daily_noop(root, selected):
        return noop

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid4().hex
    run_dir = root / "data/landing/krx/vkospi_daily_exact" / market_date.strftime("%Y%m%d") / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    ledger = AppendOnlyLedger(run_dir / "call_ledger.jsonl", secrets=())
    payload = request_payload(market_date.strftime("%Y%m%d"), market_date.strftime("%Y%m%d"))
    write_json_atomic(run_dir / "manifest.json", {
        "run_id": run_id,
        "dataset": "kr_vkospi_daily",
        "identity": "코스피 200 변동성지수",
        "official_code": OFFICIAL_CODE,
        "request": payload,
        "business_request_limit": 1,
        "retry_count": 0,
        "finalized_market_date": selected,
    })
    lock = root / "data/state/d_owned_krx_short_selling.lock"
    with shared_d_owned_krx_lock(lock, run_id=run_id):
        if session is None:
            krx_id, krx_pw = credentials or _load_credentials(root / ".env")
            if not krx_id or not krx_pw:
                raise PilotStopped("AUTH_FAILURE: KRX credentials are not configured")
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                from pykrx.website.comm import get_session
                session = get_session()
            if (session is None or not getattr(session, "is_authenticated", False)
                    or not session.is_valid()):
                raise PilotStopped("AUTH_FAILURE: KRX session is invalid")
        started = time.monotonic()
        response = session.post(BUSINESS_URL, data=payload, timeout=20)
        body = response.content
        body_path = run_dir / "response.json"
        write_bytes_atomic_new(body_path, body)
        ledger.append(
            "HTTP_RESPONSE", business_sequence=1, status_code=response.status_code,
            response_bytes=len(body), response_sha256=hashlib.sha256(body).hexdigest(),
            elapsed_ms=round((time.monotonic() - started) * 1000, 3),
        )
        if response.status_code in {403, 429}:
            raise PilotStopped(f"RATE_LIMIT_OR_ACCESS:{response.status_code}")
        if response.status_code != 200:
            raise PilotStopped(f"HTTP_STATUS:{response.status_code}")
        rows, _, _ = parse_history_body(body)
        observed = {
            datetime.strptime(str(row["TRD_DD"]), "%Y/%m/%d").date().isoformat()
            for row in rows
        }
        if observed != {selected}:
            raise PilotStopped("VALIDATION_FAILURE: exact finalized date was not returned")

    result = run_offline_daily_append(
        body_path,
        finalized_market_date=market_date,
        finality_confirmed=True,
        run_id=run_id,
        raw_root=root / "data/raw/kr_vkospi_daily",
        normalized_root=root / "data/normalized/kr_vkospi_daily",
        state_root=root / "data/state",
    )
    output = {
        "status": result.status,
        "run_id": run_id,
        "market_date": selected,
        "business_calls": 1,
        "retry_count": 0,
        "landing": str(run_dir.relative_to(root)),
        "inserted_rows": result.inserted_rows,
        "retained_rows": result.total_rows,
        "pit_status": "PIT_LIMITED_PUBLICATION_REVISION_UNRESOLVED",
    }
    write_json_atomic(run_dir / "checkpoint.json", output)
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--market-date", type=date.fromisoformat, required=True)
    parser.add_argument("--confirm-finalized-live", action="store_true")
    args = parser.parse_args(argv)
    if not args.confirm_finalized_live:
        raise SystemExit("--confirm-finalized-live is required")
    result = collect_one_finalized_date(
        args.root, market_date=args.market_date, finality_confirmed=True,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
