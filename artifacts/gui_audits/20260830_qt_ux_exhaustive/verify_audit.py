from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
EXPECTED_SURFACES = [
    "Dashboard",
    "Index_Graph",
    "Equity",
    "Research_Workspace",
    "US_ETF",
    "Watchlist",
    "Data_Status",
    "Account",
    "Net_Worth",
    "Backtest",
]
EXPECTED_VIEWPORTS = {
    "2560x1440", "1920x1080", "1600x900", "1366x768", "1280x720",
}


def load(name: str):
    return json.loads((HERE / name).read_text(encoding="utf-8"))


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def evidence_records(value):
    if isinstance(value, dict):
        if isinstance(value.get("path"), str) and isinstance(value.get("sha256"), str):
            yield value
        for child in value.values():
            yield from evidence_records(child)
    elif isinstance(value, list):
        for child in value:
            yield from evidence_records(child)


def verify_record(record: dict) -> None:
    path = ROOT / record["path"]
    require(path.is_file(), f"missing evidence: {path}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    require(digest == record["sha256"], f"hash mismatch: {path}")


def gate_inventory() -> None:
    inventory = load("inventory.json")
    counts = inventory["counts"]
    require(counts == {
        "surfaces": 10,
        "controls": 117,
        "executed": 95,
        "disabled": 13,
        "safety_skipped": 9,
        "unresolved": 0,
    }, f"unexpected inventory counts: {counts}")
    names = [row["surface"] for row in inventory["surfaces"]]
    require(names == EXPECTED_SURFACES, f"surface inventory mismatch: {names}")
    require(len(inventory["controls"]) == counts["controls"], "control list/count mismatch")
    disposition = {
        "EXECUTED_VERIFIED", "DISABLED_PREREQUISITE", "SKIPPED_SAFETY",
    }
    require(all(row.get("disposition") in disposition for row in inventory["controls"]),
            "unreconciled control disposition")
    print("EXHAUSTIVE_GUI_INVENTORY_OK")


def gate_viewport() -> None:
    ledger = load("ledger.json")
    require([row["surface"] for row in ledger["surfaces"]] == EXPECTED_SURFACES,
            "ledger surface mismatch")
    for row in ledger["surfaces"]:
        require(set(row["viewport_evidence"]) == EXPECTED_VIEWPORTS,
                f"viewport mismatch: {row['surface']}")
        require(row["before"]["path"] != row["after"]["path"],
                f"before/after not distinct: {row['surface']}")
        for record in [row["before"], row["after"], *row["viewport_evidence"].values()]:
            verify_record(record)
    screenshots = list((HERE / "evidence").glob("*.png"))
    require(len(screenshots) >= 150, f"insufficient screenshots: {len(screenshots)}")
    runtime = ledger["runtime"]
    require(runtime["screen_geometry"] == [2560, 1440], "physical display mismatch")
    require(runtime["max_user_display"] == [2560, 1440], "requested max display mismatch")
    print("EXHAUSTIVE_GUI_VIEWPORT_OK")


def gate_interaction() -> None:
    ledger = load("ledger.json")
    followup = load("followup.json")
    supplemental = load("supplemental.json")
    require(ledger["closed_cleanly"] is True, "main run did not close cleanly")
    require(ledger["qt_messages"] == [], "Qt messages in main run")
    require(followup["closed_cleanly"] is True, "follow-up did not close cleanly")
    require(followup["qt_messages"] == [], "Qt messages in follow-up")
    require(len(followup["surfaces"]) == 10, "follow-up surface count")
    for row in ledger["surfaces"]:
        gi = row["global_input"]
        require(gi["verified"] and gi["dialog_opened"] and gi["escape_closed"],
                f"global keyboard interaction incomplete: {row['surface']}")
        require(row["primary_action"]["found"], f"primary missing: {row['surface']}")
        require(bool(row["keyboard_primary"]["label"]), f"keyboard primary missing: {row['surface']}")
        detail = row["detail_or_pane"]
        detail_complete = bool(detail.get("kind")) and any([
            detail.get("opened") is True,
            detail.get("changed") is True and detail.get("restored") is True,
            detail.get("selected") is True,
            detail.get("restored") is True,
            detail.get("kind") == "global_switcher_is_modal_or_detail",
        ])
        require(detail_complete, f"detail/pane missing: {row['surface']}")
        require(row["control_counts"]["UNRESOLVED"] == 0,
                f"unresolved control: {row['surface']}")
    for row in followup["surfaces"]:
        require(not row["direct_focus_failures"], f"direct focus failure: {row['surface']}")
        require(all(item["acquired"] for item in row["direct_focus"]),
                f"focus acquisition failure: {row['surface']}")
        require(row["tab_reached_page_controls"] + len(row["tab_missing"]) == row["focusable"],
                f"Tab accounting mismatch: {row['surface']}")
        verify_record(row["focus_evidence"])
    require(len(supplemental["post_action_assertions"]) == 10,
            "supplemental post-action count")
    require(supplemental["all_post_actions_pass"] is True,
            "supplemental post-action assertion failure")
    require(all(row["expected"] and row["assertion_pass"]
                for row in supplemental["post_action_assertions"]),
            "post-action expected/result contract missing")
    print("EXHAUSTIVE_GUI_INTERACTION_OK")


def gate_stress() -> None:
    ledger = load("ledger.json")
    stress = load("stress.json")
    supplemental = load("supplemental.json")
    require(len(stress["research_pane_matrix"]) == 20, "pane matrix count")
    require(len(stress["large_font_matrix"]) == 20, "large-font count")
    require(len(stress["scenarios"]) == 11, "scenario count")
    require(all(row.get("result") in {"ASSESSED", "ASSESSED_WITHOUT_MUTATION", "NOT_APPLICABLE"}
                for row in stress["scenarios"]), "undecided scenario")
    require(stress["qt_message_count"] == 0 and stress["closed_cleanly"] is True,
            "stress run warning/close failure")
    require(ledger["duration_s"] >= 300, "audit too short")
    require(ledger["median_event_gap_s"] >= 0.5, "interaction cadence implausible")
    table_sets = 0
    for surface in ledger["surfaces"]:
        for table in surface["table_volume_stress"]:
            rows = [entry["rows"] for entry in table["volumes"]]
            require(rows == [0, 1, 100, 1000], f"table volume mismatch: {surface['surface']}")
            require(all(entry["row_count_verified"] for entry in table["volumes"]),
                    f"table row verification failed: {surface['surface']}")
            table_sets += 1
    require(table_sets >= 5, f"insufficient table stress sets: {table_sets}")
    require(supplemental["all_required_scenarios_pass"] is True,
            "supplemental scenario protocol failure")
    require(set(supplemental["scenarios"]) == {
        "interrupted_workflow", "returning_user", "lifecycle_positions",
        "round_trip", "data_seasoning",
    }, "supplemental scenario set mismatch")
    require(supplemental["account_table_stress_complete"] is True,
            "Account table stress incomplete")
    require(len(supplemental["account_table_stress"]) >= 1,
            "dynamic Account table not exercised")
    for record in evidence_records(stress):
        verify_record(record)
    for record in evidence_records(supplemental):
        verify_record(record)
    print("EXHAUSTIVE_GUI_STRESS_OK")


def gate_report() -> None:
    report = (HERE / "REPORT.md").read_text(encoding="utf-8")
    manifest = (HERE / "INTERACTION_MANIFEST.md").read_text(encoding="utf-8")
    critique = (HERE / "CRITIQUE.md").read_text(encoding="utf-8")
    require("## Verdict: FAIL" in report, "legal completed verdict missing")
    require("Incomplete" not in report.replace("not `Incomplete`", ""), "stale Incomplete verdict")
    for section in [
        "Hard-gate scorecard", "Ranked Top 5", "Roadmap",
        "Independent review and corrections", "Hold this while fixing",
    ]:
        require(section in report, f"report section missing: {section}")
    findings = re.findall(r"^### UX-\d{2} —", report, flags=re.MULTILINE)
    require(len(findings) == 16, f"finding count mismatch: {len(findings)}")
    for label in [
        "**Layer:**", "**Severity:**", "**Surface + viewport + panes:**",
        "**Persona:**", "**Reproduce:**", "**Observed:**", "**Expected:**",
        "**Evidence:**", "**Suspected code location:**", "**Smallest possible patch:**",
    ]:
        require(report.count(label) == 16, f"finding field count mismatch: {label}")
    require("### Persona lock" in report and "### Severity tally" in report,
            "persona/severity verdict metadata missing")
    require("Phase times and manifest completeness" in report,
            "phase/manifest completeness missing")
    require("Interaction Manifest: complete (60/60 required entry types; 6 per surface)" in report,
            "explicit manifest ratio missing")
    require("Hold this in your hands" in report,
            "holistic hold paragraph missing")
    require("154 retained images" in report, "final screenshot count is stale")
    require("10/10 user surfaces" in manifest, "manifest surface summary missing")
    require("117 visible actionable controls" in manifest, "manifest control summary missing")
    require("## Final classification" in critique, "fresh critique classification missing")
    require("UX-01" in critique and "UX-16" in critique, "critique coverage missing")
    for name in ["market_core.md", "research_data.md", "portfolio_lab.md"]:
        require((HERE / "reviews" / name).is_file(), f"review missing: {name}")
    print("EXHAUSTIVE_GUI_REPORT_OK")


def gate_safety() -> None:
    ledger = load("ledger.json")
    runtime = ledger["runtime"]
    flags = [
        "provider_refresh_injected", "account_mutation", "order_action",
        "transfer_action", "scheduler_activation", "protected_data_access",
        "product_code_write",
    ]
    require(all(runtime[name] is False for name in flags), f"safety flag set: {runtime}")
    require(not (ROOT / "artifacts" / "gui_audits" / "20260830_qt_ux_exhaustive" / "PRODUCT_WRITE").exists(),
            "product-write sentinel present")
    print("EXHAUSTIVE_GUI_SAFETY_OK")


def gate_context() -> None:
    persona = (HERE / "PERSONA.md").read_text(encoding="utf-8")
    for token in ["2560", "Windows", "한국어", "키보드", "마우스"]:
        require(token.lower() in persona.lower(), f"persona/context token missing: {token}")
    ledger = load("ledger.json")
    require({f"{w}x{h}" for w, h in ledger["runtime"]["viewports"]} == EXPECTED_VIEWPORTS,
            "context viewport matrix mismatch")
    print("EXHAUSTIVE_GUI_CONTEXT_OK")


def gate_timing() -> None:
    ledger = load("ledger.json")
    require(ledger["duration_s"] >= 300, "interaction run shorter than five minutes")
    require(ledger["event_count"] >= 80, "too few material interaction events")
    require(ledger["median_event_gap_s"] >= 0.5, "batched interaction cadence")
    print("EXHAUSTIVE_GUI_TIMING_OK")


def gate_states() -> None:
    gate_stress()
    ledger = load("ledger.json")
    require(all(row["long_content_stress"]["applicable"] for row in ledger["surfaces"]),
            "long-content state missing")
    print("EXHAUSTIVE_GUI_STATES_OK")


def gate_accessibility() -> None:
    gate_interaction()
    inventory = load("inventory.json")
    require(inventory["counts"]["unresolved"] == 0, "unresolved accessibility disposition")
    followup = load("followup.json")
    require(all(not row["direct_focus_failures"] for row in followup["surfaces"]),
            "direct focus failure")
    print("EXHAUSTIVE_GUI_ACCESSIBILITY_OK")


def gate_performance() -> None:
    ledger = load("ledger.json")
    supplemental = load("supplemental.json")
    require(ledger["runtime"]["startup_ms"] > 0, "startup timing missing")
    performance = supplemental["performance"]
    for name in ["startup", "tab_switch", "safe_action", "resize"]:
        row = performance[name]
        require(len(row["samples_ms"]) >= 5, f"insufficient performance samples: {name}")
        require(row["pass"] is True, f"performance budget failed: {name}")
        require(row["median_ms"] <= row["median_budget_ms"], f"median breach: {name}")
        require(row["p95_ms"] <= row["p95_budget_ms"], f"p95 breach: {name}")
    require(performance["tab_switch"]["deliberate_wait_excluded"] is True,
            "tab timing includes observation wait")
    require(performance["all_budgets_pass"] is True, "performance summary failed")
    print("EXHAUSTIVE_GUI_PERFORMANCE_OK")


def gate_scenarios() -> None:
    stress = load("stress.json")
    require(len(stress["scenarios"]) == 11, "scenario count")
    require(all(row.get("result") for row in stress["scenarios"]), "scenario result missing")
    print("EXHAUSTIVE_GUI_SCENARIOS_OK")


def gate_critique() -> None:
    critique = (HERE / "CRITIQUE.md").read_text(encoding="utf-8")
    require("## Final classification" in critique, "classification section missing")
    for number in range(1, 17):
        require(f"UX-{number:02d}" in critique, f"critique missing UX-{number:02d}")
    print("EXHAUSTIVE_GUI_CRITIQUE_OK")


def gate_reviews() -> None:
    requirements = {
        "market_core.md": ["Dashboard", "Index_Graph", "Equity", "US_ETF", "Top 3"],
        "research_data.md": ["Research Workspace", "Watchlist", "Data Status", "Top 3"],
        "portfolio_lab.md": ["Account", "Net Worth", "Backtest", "Top 3"],
    }
    for name, tokens in requirements.items():
        text = (HERE / "reviews" / name).read_text(encoding="utf-8")
        require(all(token in text for token in tokens), f"review coverage missing: {name}")
        require("evidence/" in text and "Suspected" in text, f"review evidence missing: {name}")
    print("EXHAUSTIVE_GUI_REVIEWS_OK")


GATES = {
    "inventory": gate_inventory,
    "viewport": gate_viewport,
    "interaction": gate_interaction,
    "stress": gate_stress,
    "report": gate_report,
    "safety": gate_safety,
    "context": gate_context,
    "timing": gate_timing,
    "states": gate_states,
    "accessibility": gate_accessibility,
    "performance": gate_performance,
    "scenarios": gate_scenarios,
    "critique": gate_critique,
    "reviews": gate_reviews,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gate", choices=[*GATES, "all"], required=True)
    args = parser.parse_args()
    try:
        if args.gate == "all":
            for fn in GATES.values():
                fn()
            print("EXHAUSTIVE_GUI_AUDIT_COMPLETE")
        else:
            GATES[args.gate]()
    except (AssertionError, KeyError, OSError, ValueError) as exc:
        print(f"AUDIT_VERIFY_FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
