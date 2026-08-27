from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys


SCRIPT = Path(__file__).resolve().parents[3] / "scripts/maintenance/refresh_manual_account_market_values.py"


def _write_inputs(root: Path) -> tuple[Path, Path, Path]:
    basis = root / "basis.json"
    symbols = root / "symbols.json"
    observations = root / "observations.json"
    basis.write_text(json.dumps({
        "schema_version": 1, "source_sheet": "아빠",
        "snapshot_date": "2026-02-03", "currency": "KRW",
        "holdings": [{
            "section": "ISA", "name": "Fixture Alpha", "ticker": "111111",
            "quantity": 2, "average_cost": 100, "purchase_total": 200,
        }],
    }), encoding="utf-8")
    symbols.write_text(json.dumps({
        "schema_version": 1, "symbols": [{
            "section": "ISA", "ticker": "111111",
            "provider_symbol": "111111.KS", "exchange": "XKRX",
            "currency": "KRW",
        }],
    }), encoding="utf-8")
    observations.write_text(json.dumps({
        "schema_version": 1, "results": [{
            "section": "ISA", "ticker": "111111", "status": "AVAILABLE",
            "provider": "YAHOO_CHART_API", "provider_symbol": "111111.KS",
            "exchange": "XKRX", "currency": "KRW", "unit": "KRW_PER_SHARE",
            "price": "150", "as_of": "2026-08-26T05:00:00+00:00",
            "captured_at": "2026-08-26T05:00:05+00:00",
            "finality": "AS_RETRIEVED",
        }],
    }), encoding="utf-8")
    return basis, symbols, observations


def _run(root: Path, inputs: tuple[Path, Path, Path]):
    basis, symbols, observations = inputs
    return subprocess.run([
        sys.executable, str(SCRIPT), "--project-root", str(root),
        "--basis", str(basis), "--symbol-map", str(symbols),
        "--observations", str(observations),
    ], capture_output=True, text=True, encoding="utf-8", timeout=20)


def test_api_zero_cli_atomically_updates_and_preserves_basis_and_prior_cache(
    tmp_path: Path,
) -> None:
    inputs = _write_inputs(tmp_path)
    basis_before = inputs[0].read_bytes()

    accepted = _run(tmp_path, inputs)

    assert accepted.returncode == 0
    summary = json.loads(accepted.stdout)
    assert summary == {
        "available_rows": 1, "provider_calls": 0, "reason": None,
        "requested_symbols": 1, "status": "UPDATED", "unavailable_rows": 0,
    }
    output = tmp_path / "data/local/manual_account_market_values/latest.json"
    cache_before = output.read_bytes()
    assert inputs[0].read_bytes() == basis_before
    assert not tuple(output.parent.glob(".*.tmp"))

    observations = json.loads(inputs[2].read_text(encoding="utf-8"))
    observations["results"][0]["provider_symbol"] = "999999.KS"
    inputs[2].write_text(json.dumps(observations), encoding="utf-8")
    rejected = _run(tmp_path, inputs)

    assert rejected.returncode == 1
    rejected_summary = json.loads(rejected.stdout)
    assert rejected_summary["status"] == "REJECTED_PRIOR_PRESERVED"
    assert rejected_summary["provider_calls"] == 0
    assert output.read_bytes() == cache_before
    assert inputs[0].read_bytes() == basis_before
