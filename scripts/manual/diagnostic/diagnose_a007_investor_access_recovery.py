"""Exactly one retry-free KRX Investor request after an enforced cooldown."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.manual.diagnostic import a007_investor_access_recovery_support as support
from scripts.manual.diagnostic.diagnose_a007_investor_range import run_diagnostic


LANDING_ROOT = ROOT / "data/landing/diagnostics/a007_investor_access_recovery"
LOCK_PATH = ROOT / "data/state/d_owned_krx_short_selling.lock"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--acknowledge-no-active-krx-stream", action="store_true")
    parser.add_argument("--acknowledge-cooldown-ended", action="store_true")
    parser.add_argument("--confirm-one-live-request", action="store_true")
    parser.add_argument("--confirm-landing-only", action="store_true")
    parser.add_argument("--confirm-scope")
    args = parser.parse_args()
    if not (
        args.acknowledge_no_active_krx_stream
        and args.acknowledge_cooldown_ended
        and args.confirm_one_live_request
        and args.confirm_landing_only
        and args.confirm_scope == support.SCOPE_ID
    ):
        print(f"Refusing to run: exact confirmations and --confirm-scope {support.SCOPE_ID} are required", file=sys.stderr)
        return 2
    result = run_diagnostic(
        env_file=args.env_file, project_root=ROOT, landing_root=LANDING_ROOT,
        lock_path=LOCK_PATH, diagnostic_support=support,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
