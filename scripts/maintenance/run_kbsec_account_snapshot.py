from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sys
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from stock_data.orchestration.kbsec_account_daily import run_kbsec_account_daily
from stock_data.orchestration.kbsec_account_runtime import (
    load_kbsec_account_environment,
)


LAST_RECEIPT = "artifacts/scheduler_logs/STOCK_DATA_KBSEC_ACCOUNT_DAILY_last.json"


def _atomic_report(path: Path, report: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = (
        json.dumps(
            report, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    if path.read_bytes() != body:
        raise OSError("scheduled KB report readback differs")


def _parse_clock(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("--as-of must include a timezone offset")
    return parsed


def _exit_code(report: dict[str, object]) -> int:
    status = report.get("status")
    if status in {"TERMINAL_SUCCESS", "DRY_RUN_READY"}:
        return 0
    if status == "NOOP_OCCURRENCE_ALREADY_CLAIMED":
        return 0 if report.get("retained_status") == "TERMINAL_SUCCESS" else 1
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run one secret-safe daily KB read-only account refresh."
    )
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--as-of", help="aware ISO-8601 clock for provider-free dry-run")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    project_root = args.project_root.resolve()
    try:
        if args.as_of is not None and not args.dry_run:
            raise ValueError("--as-of is provider-free dry-run only")
        environment = load_kbsec_account_environment(project_root, os.environ)
        report = run_kbsec_account_daily(
            project_root, environment, now=_parse_clock(args.as_of),
            dry_run=args.dry_run,
        )
        if not args.dry_run and report.get("status") != "NOOP_OCCURRENCE_ALREADY_CLAIMED":
            receipt = report.get("receipt")
            if not isinstance(receipt, str):
                raise ValueError("terminal KB receipt path is missing")
            receipt_path = (project_root / receipt).resolve()
            receipt_path.relative_to(project_root)
            terminal = json.loads(receipt_path.read_text(encoding="utf-8"))
            if not isinstance(terminal, dict):
                raise ValueError("terminal KB receipt differs")
            _atomic_report(project_root / LAST_RECEIPT, terminal)
    except Exception:
        print(json.dumps({
            "operation": "KBSEC_ACCOUNT_READONLY_DAILY",
            "status": "CLI_FAILURE",
            "reason": "SANITIZED_INTERNAL_FAILURE",
            "supplier_calls": 0,
        }, sort_keys=True))
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return _exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
