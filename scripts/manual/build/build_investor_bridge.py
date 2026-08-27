from pathlib import Path
import json
import sys


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from stock_data.published.investor_bridge import build_investor_bridge  # noqa: E402


if __name__ == "__main__":
    print(json.dumps(build_investor_bridge(project_root=ROOT), ensure_ascii=False))
