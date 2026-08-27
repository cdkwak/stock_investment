"""Zero-network verifier for a retained A007 Investor H3 diagnostic run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.manual.diagnostic import a007_investor_h3_diagnostic_support as support
from scripts.manual.audit.verify_a007_investor_h1 import verify_retained_run as verify_historical_run


def verify_retained_run(*, project_root: Path, run_dir: Path, write_evidence: bool = False):
    return verify_historical_run(
        project_root=project_root, run_dir=run_dir, write_evidence=write_evidence,
        diagnostic_support=support, landing_name="a007_investor_h3",
        expected_end_date="20160106",
        verification_schema="a007.investor_h3.offline_verification",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Zero-network retained Investor H3 verifier")
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--write-append-only-evidence", action="store_true")
    args = parser.parse_args()
    result = verify_retained_run(
        project_root=args.project_root, run_dir=args.run_dir,
        write_evidence=args.write_append_only_evidence,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
