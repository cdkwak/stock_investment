"""Run the approved one-call FSC Rights completion sentinel and safe promotion."""
from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stock_data.providers.data_go_kr.client import service_key_from_environment
from stock_data.providers.data_go_kr.rights_observation import promote_rights_diagnostic

from scripts.manual.pilot.rights_completion_sentinel_support import run_completion_sentinel


def main() -> int:
    result = run_completion_sentinel(
        project_root=ROOT,
        service_key=service_key_from_environment(ROOT),
    )
    summary = {key: value for key, value in result.items() if key != "diagnostic_root"}
    if result["status"] == "SOURCE_SNAPSHOT_COMPLETE":
        promoted = promote_rights_diagnostic(
            project_root=ROOT, diagnostic_root=Path(result["diagnostic_root"])
        )
        summary["promotion_status"] = promoted["status"]
        summary["normalized_row_count"] = promoted["row_count"]
    else:
        summary["promotion_status"] = "NOT_ATTEMPTED"
    print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "SOURCE_SNAPSHOT_COMPLETE" else 2


if __name__ == "__main__":
    sys.exit(main())
