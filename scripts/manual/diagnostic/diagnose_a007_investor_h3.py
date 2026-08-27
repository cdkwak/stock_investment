"""Exactly-one-call Landing-only Investor H3 availability diagnostic."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.manual.diagnostic import a007_investor_h3_diagnostic_support as support
from scripts.manual.diagnostic.diagnose_a007_investor_range import run_diagnostic


LANDING_ROOT = ROOT / "data/landing/diagnostics/a007_investor_h3"
D_OWNED_LOCK_PATH = ROOT / "data/state/d_owned_krx_short_selling.lock"
CONFIRM_SCOPE = support.SCOPE_ID


def main() -> int:
    parser = argparse.ArgumentParser(description="One-call Landing-only Investor H3 diagnostic")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--acknowledge-no-active-krx-stream", action="store_true")
    parser.add_argument("--confirm-one-live-request", action="store_true")
    parser.add_argument("--confirm-landing-only", action="store_true")
    parser.add_argument("--confirm-scope")
    args = parser.parse_args()
    if not (
        args.acknowledge_no_active_krx_stream and args.confirm_one_live_request
        and args.confirm_landing_only and args.confirm_scope == CONFIRM_SCOPE
    ):
        print(f"Refusing to run: all confirmations and --confirm-scope {CONFIRM_SCOPE} are required", file=sys.stderr)
        return 2
    result = run_diagnostic(
        env_file=args.env_file, project_root=ROOT, landing_root=LANDING_ROOT,
        lock_path=D_OWNED_LOCK_PATH, diagnostic_support=support,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
