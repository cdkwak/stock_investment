"""Write the local Obsidian investing-journal draft for one KRX session."""

from __future__ import annotations

import argparse
from datetime import date
import logging
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from stock_data.journal import JournalError, write_investing_journal  # noqa: E402


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", type=_date, dest="journal_date")
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="build and print the complete draft without writing journal or brief files",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        result = write_investing_journal(
            args.project_root,
            args.journal_date,
            dry_run=args.dry_run,
        )
    except JournalError as exc:
        print(f"investing_journal: {exc}", file=sys.stderr)
        return 1
    if result.journal_content is not None and args.dry_run:
        print(f"investing_journal: {result.status} would write {result.journal_path}")
        if result.brief_path is not None:
            print(f"investing_journal: would write brief {result.brief_path}")
        print(result.journal_content, end="" if result.journal_content.endswith("\n") else "\n")
    else:
        print(
            f"investing_journal: {result.status}"
            + (f" {result.journal_path}" if result.journal_path is not None else "")
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
