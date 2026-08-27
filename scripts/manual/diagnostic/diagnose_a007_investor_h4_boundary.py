"""Exactly-one-call Landing-only H4 boundary-pair diagnostic."""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from scripts.manual.diagnostic import a007_investor_h4_boundary_diagnostic_support as support
from scripts.manual.diagnostic.diagnose_a007_investor_range import run_diagnostic
LANDING_ROOT=ROOT/"data/landing/diagnostics/a007_investor_h4_boundary"
D_OWNED_LOCK_PATH=ROOT/"data/state/d_owned_krx_short_selling.lock"
def main()->int:
    p=argparse.ArgumentParser(); p.add_argument("--env-file",type=Path,default=ROOT/".env"); p.add_argument("--acknowledge-no-active-krx-stream",action="store_true"); p.add_argument("--confirm-one-live-request",action="store_true"); p.add_argument("--confirm-landing-only",action="store_true"); p.add_argument("--confirm-scope"); a=p.parse_args()
    if not(a.acknowledge_no_active_krx_stream and a.confirm_one_live_request and a.confirm_landing_only and a.confirm_scope==support.SCOPE_ID): print(f"Refusing to run: all confirmations and --confirm-scope {support.SCOPE_ID} are required",file=sys.stderr); return 2
    print(json.dumps(run_diagnostic(env_file=a.env_file,project_root=ROOT,landing_root=LANDING_ROOT,lock_path=D_OWNED_LOCK_PATH,diagnostic_support=support),ensure_ascii=False)); return 0
if __name__=="__main__": raise SystemExit(main())
