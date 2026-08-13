from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import tempfile
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
import pandas as pd

from stock_data.contracts.kbsec_snapshot import KBSEC_SNAPSHOT_CONTRACTS
from stock_data.providers.kbsec.client import KBSecResponse
from stock_data.providers.kbsec.market_summary import normalize_market_summary
from stock_data.storage.contract_parquet import read_dataset
from stock_data.validation.kbsec_snapshot import validate_kb_snapshot


EXPECTED_FILES = {
    "call_ledger.jsonl", "checkpoint.json", "market_response.body",
    "market_response.json", "provenance.json", "response_01.redacted.json",
    "response_02.redacted.json",
}


def _sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def audit(project_root: Path, run_id: str) -> dict[str, object]:
    root = project_root.resolve()
    run = root / "data/landing/kbsec/daily_snapshot" / run_id
    if run.parent != root / "data/landing/kbsec/daily_snapshot" or run.is_symlink():
        raise RuntimeError("invalid KB run path")
    files = {path.name for path in run.iterdir() if path.is_file()}
    if files != EXPECTED_FILES:
        raise RuntimeError("unexpected KB run topology")
    ledger = [json.loads(line) for line in (run / "call_ledger.jsonl").read_text(encoding="utf-8").splitlines()]
    if [(row.get("sequence"), row.get("operation"), row.get("http_status"), row.get("retry_count")) for row in ledger] != [
        (1, "oauth2/token", 200, 0), (2, "api/v1/ivsa0070", 200, 0)
    ]:
        raise RuntimeError("KB call ledger differs")
    checkpoint = json.loads((run / "checkpoint.json").read_text(encoding="utf-8"))
    provenance = json.loads((run / "provenance.json").read_text(encoding="utf-8"))
    if checkpoint.get("status") != "COMPLETE" or checkpoint.get("request_count") != 2 or checkpoint.get("retry_count") != 0:
        raise RuntimeError("KB checkpoint is not complete")
    raw = (run / "market_response.body").read_bytes()
    raw_hash = _sha(raw)
    if raw_hash != ledger[1].get("raw_response_sha256") or raw_hash != provenance.get("raw_response_sha256"):
        raise RuntimeError("KB market body hash differs")
    payload = json.loads(raw.decode("utf-8"))
    if payload != json.loads((run / "market_response.json").read_text(encoding="utf-8")):
        raise RuntimeError("KB raw and parsed Landing differ")
    token_evidence = json.loads((run / "response_01.redacted.json").read_text(encoding="utf-8"))
    market_evidence = json.loads((run / "response_02.redacted.json").read_text(encoding="utf-8"))
    if token_evidence.get("raw_response_sha256") != ledger[0].get("raw_response_sha256"):
        raise RuntimeError("KB token evidence hash differs")
    if market_evidence.get("raw_response_sha256") != raw_hash:
        raise RuntimeError("KB market evidence hash differs")
    exposed = json.dumps(token_evidence, ensure_ascii=False)
    if "[REDACTED]" not in exposed or '"access_token": "[REDACTED]"' not in exposed:
        raise RuntimeError("KB token metadata is not safely redacted")
    data_header = payload.get("dataHeader")
    data_body = payload.get("dataBody")
    if not isinstance(data_header, dict) or not isinstance(data_body, dict) or str(data_header.get("resultCode")) != "200":
        raise RuntimeError("KB response envelope differs")
    market_time = str(data_body.get("inq_dy_tm", ""))
    if market_time[:8] != checkpoint.get("market_date_source") or market_time[:8] != provenance.get("market_date_source"):
        raise RuntimeError("KB source timestamp differs")
    captured = datetime.fromisoformat(str(checkpoint["captured_at_utc"]))
    captured_kst = captured.astimezone(ZoneInfo("Asia/Seoul"))
    response = KBSecResponse(
        str(data_header.get("resultCode")), str(data_header.get("processCode", "")),
        data_body, payload, 200, str(data_header.get("resultMessage", "")),
        str(data_header.get("processMessage", "")),
    )
    expected = normalize_market_summary(response, collected_at=captured)
    contracts = {contract.name: contract for contract in KBSEC_SNAPSHOT_CONTRACTS}
    row_counts: dict[str, int] = {}
    for name, frame in expected.items():
        contract = contracts[name]
        frame = frame[list(contract.column_names)].sort_values(list(contract.sort_key), kind="stable").reset_index(drop=True)
        validate_kb_snapshot(frame, contract)
        dataset_root = root / "data/normalized" / name
        if not dataset_root.exists():
            dataset_root = root / "data/quarantine/kbsec_preopen_date_semantics" / run_id / name
        stored = read_dataset(dataset_root, contract, lambda value: validate_kb_snapshot(value, contract))
        actual = stored.loc[
            pd.to_datetime(stored["collected_at"], utc=True) == pd.Timestamp(captured)
        ].copy()
        actual = actual[list(contract.column_names)].sort_values(list(contract.sort_key), kind="stable").reset_index(drop=True)
        for column in contract.columns:
            if column.dtype == "date":
                frame[column.name] = pd.to_datetime(frame[column.name]).dt.strftime("%Y-%m-%d")
                actual[column.name] = pd.to_datetime(actual[column.name]).dt.strftime("%Y-%m-%d")
            elif column.dtype.startswith("timestamp"):
                frame[column.name] = pd.to_datetime(frame[column.name], utc=True)
                actual[column.name] = pd.to_datetime(actual[column.name], utc=True)
        pd.testing.assert_frame_equal(frame, actual, check_dtype=False, check_exact=True)
        row_counts[name] = len(frame)
    load_dotenv(root / ".env", override=False)
    secrets = [os.getenv(name, "").encode() for name in ("KBSEC_APP_KEY", "KBSEC_APP_SECRET") if os.getenv(name, "")]
    if any(secret in path.read_bytes() for path in run.iterdir() if path.is_file() for secret in secrets):
        raise RuntimeError("KB secret found in retained run")
    list_fields = {
        key: sorted({field for row in value if isinstance(row, dict) for field in row})
        for key, value in data_body.items() if isinstance(value, list)
    }
    preopen = captured_kst.hour < 9
    return {
        "schema": "stock_data.kbsec_daily_snapshot_audit", "version": 1,
        "status": "PASS_WITH_DATE_SEMANTICS_BLOCKER" if preopen else "PASS",
        "pipeline_status": "DATE_SEMANTICS_REVIEW_REQUIRED" if preopen else "OPERATIONAL",
        "network_calls": 0,
        "run_id": run_id, "captured_at_utc": checkpoint["captured_at_utc"],
        "market_timestamp_source": market_time, "market_response_sha256": raw_hash,
        "token_response_sha256": ledger[0]["raw_response_sha256"],
        "request_count": 2, "retry_count": 0, "row_counts": row_counts,
        "source_list_fields": list_fields,
        "date_evidence": {
            "capture_date_kst": captured_kst.date().isoformat(),
            "capture_phase": "PREOPEN" if preopen else "POST_CLOSE_WINDOW",
            "inquiry_date": market_time[:8],
            "liquidity_source_date": str(data_body.get("dt_5", "")),
            "global_symbol_source_dates": sorted({
                str(row.get("dt_tm", ""))[:8] for row in data_body.get("out4", [])
                if isinstance(row, dict) and str(row.get("dt_tm", "")).strip()
            }),
            "normalized_market_date_safe": not preopen,
        },
    }


def _write_content_addressed(root: Path, result: dict[str, object]) -> Path:
    body = (json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
    digest = _sha(body)
    target = root / "data/state/audits/kbsec_daily_snapshot" / f"{digest}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.read_bytes() != body: raise RuntimeError("KB audit collision")
        return target
    with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".kb-audit-", delete=False) as handle:
        temporary = Path(handle.name); handle.write(body); handle.flush(); os.fsync(handle.fileno())
    try:
        os.link(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def _promote_state(root: Path, result: dict[str, object], audit_path: Path) -> None:
    state_path = root / "data/state/kbsec_daily_snapshot.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    if not any(
        run.get("run_id") == result["run_id"] and run.get("status") == "COMPLETE"
        for run in state.get("runs", [])
    ):
        raise RuntimeError("audited KB run is absent from operational state")
    state["access_status"] = "AUTH_FIXED"
    state["daily_snapshot_status"] = result["pipeline_status"]
    state["latest_audit"] = {
        "run_id": result["run_id"],
        "status": result["status"],
        "path": audit_path.relative_to(root).as_posix(),
        "market_response_sha256": result["market_response_sha256"],
    }
    body = (json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
    with tempfile.NamedTemporaryFile(dir=state_path.parent, prefix=".kb-state-", delete=False) as handle:
        temporary = Path(handle.name); handle.write(body); handle.flush(); os.fsync(handle.fileno())
    try:
        os.replace(temporary, state_path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--write-audit", action="store_true")
    args = parser.parse_args()
    result = audit(args.project_root, args.run_id)
    if args.write_audit:
        root = args.project_root.resolve()
        audit_path = _write_content_addressed(root, result)
        _promote_state(root, result, audit_path)
        result["audit_path"] = audit_path.relative_to(root).as_posix()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
