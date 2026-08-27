from pathlib import Path
import json
import sys


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))

from stock_data.derived.treasury_spread import build_treasury_spread_dataset  # noqa: E402


if __name__ == "__main__":
    print(
        json.dumps(
            build_treasury_spread_dataset(
                input_root=ROOT / "data/normalized/fred_treasury_yield_daily",
                input_state_path=ROOT / "data/state/fred_treasury_yield_daily.json",
                output_root=ROOT / "data/derived/us_treasury_spread_daily",
                output_state_path=ROOT / "data/state/us_treasury_spread_daily.json",
            ),
            ensure_ascii=False,
        )
    )
