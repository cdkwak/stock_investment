from __future__ import annotations

import argparse
from datetime import date, datetime
import json
import os
from pathlib import Path
import sys
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from stock_data.orchestration.toss_account_runtime import (
    TossAccountRecoveryError,
    _strict_receipt,
    load_toss_account_environment,
    run_toss_account_daily,
)


LAST_RECEIPT = "artifacts/scheduler_logs/STOCK_DATA_TOSS_ACCOUNT_DAILY_last.json"
OCCURRENCE_RECEIPTS = "data/state/toss_account_snapshot_occurrences"


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
        raise OSError("scheduled account report readback differs")


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


def _occurrence_receipt_bytes(project_root: Path) -> dict[str, bytes]:
    """Capture direct occurrence receipts so recovery evidence is call-bound."""

    directory = (project_root / OCCURRENCE_RECEIPTS).resolve()
    try:
        directory.relative_to(project_root)
    except ValueError as error:
        raise ValueError("account occurrence receipt boundary differs") from error
    if not directory.exists():
        return {}
    if not directory.is_dir():
        raise ValueError("account occurrence receipt boundary differs")
    receipts: dict[str, bytes] = {}
    for path in directory.glob("*.json"):
        if path.parent != directory or path.resolve().parent != directory or not path.is_file():
            raise ValueError("account occurrence receipt boundary differs")
        receipts[path.name] = path.read_bytes()
    return receipts


def _new_recovery_receipt(
    project_root: Path, before: dict[str, bytes],
) -> tuple[str, dict[str, object]]:
    """Return the one strict RECOVERY_REQUIRED receipt changed by this CLI call."""

    candidates: list[tuple[str, dict[str, object]]] = []
    directory = (project_root / OCCURRENCE_RECEIPTS).resolve()
    for name, body in _occurrence_receipt_bytes(project_root).items():
        if before.get(name) == body:
            continue
        path = directory / name
        try:
            occurrence_date = date.fromisoformat(path.stem)
            receipt = _strict_receipt(path, occurrence_date=occurrence_date)
        except (OSError, UnicodeError, ValueError):
            continue
        if receipt.get("status") == "RECOVERY_REQUIRED":
            candidates.append((path.relative_to(project_root).as_posix(), receipt))
    if len(candidates) != 1:
        raise ValueError("strict recovery receipt is unavailable")
    return candidates[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run one secret-safe daily Toss read-only account refresh."
    )
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--as-of", help="aware ISO-8601 clock for bounded validation")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    project_root = args.project_root.resolve()
    receipt_bytes_before: dict[str, bytes] = {}
    try:
        receipt_bytes_before = _occurrence_receipt_bytes(project_root)
        if args.as_of is not None and not args.dry_run:
            raise ValueError("--as-of is provider-free dry-run only")
        environment = load_toss_account_environment(project_root, os.environ)
        report = run_toss_account_daily(
            project_root, environment, now=_parse_clock(args.as_of),
            dry_run=args.dry_run,
        )
        if not args.dry_run and report.get("status") != "NOOP_OCCURRENCE_ALREADY_CLAIMED":
            receipt = report.get("receipt")
            if not isinstance(receipt, str):
                raise ValueError("terminal account receipt path is missing")
            receipt_path = (project_root / receipt).resolve()
            receipt_path.relative_to(project_root)
            terminal = json.loads(receipt_path.read_text(encoding="utf-8"))
            if not isinstance(terminal, dict):
                raise ValueError("terminal account receipt differs")
            _atomic_report(project_root / LAST_RECEIPT, terminal)
    except TossAccountRecoveryError:
        receipt_path, terminal = _new_recovery_receipt(
            project_root, receipt_bytes_before,
        )
        _atomic_report(project_root / LAST_RECEIPT, terminal)
        report = {
            "schema_version": terminal["schema_version"],
            "operation": terminal["operation"],
            "occurrence_date": terminal["occurrence_date"],
            "scheduled_for": terminal["scheduled_for"],
            "status": terminal["status"],
            "outcome": "SCHEDULE_INTERNAL_FAILURE",
            "reason": terminal["reason"],
            "token_calls": None,
            "account_calls": None,
            "receipt": receipt_path,
            "retained_status": None,
        }
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return _exit_code(report)
    except Exception:
        print(json.dumps({
            "operation": "TOSS_ACCOUNT_READONLY_DAILY",
            "status": "CLI_FAILURE",
            "reason": "SANITIZED_INTERNAL_FAILURE",
            "token_calls": 0,
            "account_calls": 0,
        }, sort_keys=True))
        return 1
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return _exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
