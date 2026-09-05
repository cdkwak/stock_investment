"""Precompute crisis-aligned overlay JSON from retained Parquet only."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from scripts.research import run_core_ammunition as core_runner  # noqa: E402
from stock_data.research.core_ammunition import prepare_value_series  # noqa: E402
from stock_data.research.compound_ladder import (  # noqa: E402
    require_disp60_threshold,
    require_drawdown_threshold,
    require_product_share_at_max,
)
from stock_data.research.crisis_overlay import (  # noqa: E402
    build_overlay_payload,
    round_payload,
    validate_overlay_payload,
)
from stock_data.research.leveraged_product import load_index_universe  # noqa: E402


OUTPUT_RELATIVE = Path("artifacts/research/crisis_overlay/overlay.json")


def _write_json(path: Path, payload: Any) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(
        payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"),
    ) + "\n"
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(body, encoding="utf-8", newline="\n")
    temporary.replace(path)
    return len(body.encode("utf-8"))


def run(
    project_root: Path,
    *,
    drawdown_threshold: float | None = None,
    disp60_threshold: float | None = None,
    product_share_at_max: float | None = None,
) -> tuple[Path, dict[str, Any], int]:
    root = Path(project_root).resolve()
    decided_drawdown = require_drawdown_threshold(drawdown_threshold)
    decided_disp60 = require_disp60_threshold(disp60_threshold)
    decided_share = require_product_share_at_max(product_share_at_max)
    episodes, frames, ladders = core_runner._episode_inputs(
        root,
        False,
        drawdown_threshold=decided_drawdown,
        disp60_threshold=decided_disp60,
        product_share_at_max=decided_share,
    )
    assets, _fx = core_runner._assets(root)
    treasury = core_runner._read_dataset(
        root, "fred_treasury_yield_daily", ("date", "dgs10"),
    )
    dgs10 = prepare_value_series(treasury["date"], treasury["dgs10"])
    universe = load_index_universe(root)
    payload = round_payload(build_overlay_payload(
        episodes=episodes,
        frames=frames,
        ladders=ladders,
        assets=assets,
        dgs10=dgs10,
        cycle_buckets=core_runner.CYCLE_BUCKETS,
        ladder_universe=universe,
        drawdown_threshold=decided_drawdown,
        disp60_threshold=decided_disp60,
        product_share_at_max=decided_share,
    ))
    validate_overlay_payload(payload)
    output = root / OUTPUT_RELATIVE
    size = _write_json(output, payload)
    if size >= 3_000_000:
        raise ValueError(f"overlay JSON exceeds 3 MB: {size} bytes")
    print(
        f"DONE crisis-overlay/v2 episodes={len(payload['episodes'])} "
        f"assets={len(payload['assets'])} bytes={size} output={output}"
    )
    return output, payload, size


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", type=Path, default=ROOT)
    parser.add_argument("--drawdown-threshold", type=float, default=None)
    parser.add_argument("--disp60-threshold", type=float, default=None)
    parser.add_argument("--product-share-at-max", type=float, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    run(
        args.project_root,
        drawdown_threshold=args.drawdown_threshold,
        disp60_threshold=args.disp60_threshold,
        product_share_at_max=args.product_share_at_max,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
