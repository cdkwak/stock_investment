from __future__ import annotations

import ast
from pathlib import Path
import re

import pytest

from stock_data.research.compound_ladder import LadderSpec


ROOT = Path(__file__).parents[3]
DECIDED = {
    "drawdown_threshold": -0.25,
    "disp60_threshold": -0.15,
    "product_share_at_max": 0.5,
    "levels": 3,
    "base_exposure": 0.8,
}
FORBIDDEN = {
    "levels=2": re.compile(r"\blevels\s*=\s*2\b"),
    "base_exposure=1.0": re.compile(r"\bbase_exposure\s*=\s*1\.0\b"),
    "product_share_at_max=1.0": re.compile(
        r"\bproduct_share_at_max\s*=\s*1\.0\b"
    ),
    "drawdown_threshold=-0.20": re.compile(
        r"\bdrawdown_threshold\s*=\s*-0\.20\b"
    ),
    "disp60_threshold=-0.10": re.compile(
        r"\bdisp60_threshold\s*=\s*-0\.10\b"
    ),
}


@pytest.mark.parametrize("missing", tuple(DECIDED))
def test_each_ladder_field_is_fail_loud_when_missing(missing: str) -> None:
    values = {key: value for key, value in DECIDED.items() if key != missing}

    with pytest.raises(
        ValueError,
        match=rf"^{missing} is undecided under rule ⑥; caller must pass it explicitly$",
    ):
        LadderSpec(**values)


def test_research_sources_contain_no_forbidden_ladder_defaults() -> None:
    for literal, pattern in FORBIDDEN.items():
        assert pattern.search(literal), f"broken positive control for {literal}"

    paths = sorted((ROOT / "src/stock_data/research").rglob("*.py"))
    paths.extend(sorted((ROOT / "scripts/research").rglob("*.py")))
    matches: list[str] = []
    for path in paths:
        source = path.read_text(encoding="utf-8")
        for literal, pattern in FORBIDDEN.items():
            for match in pattern.finditer(source):
                line = source.count("\n", 0, match.start()) + 1
                matches.append(f"{path.relative_to(ROOT)}:{line}: {literal}")

    assert matches == [], "forbidden rule-⑥ defaults remain:\n" + "\n".join(matches)


def test_every_non_guard_ladder_call_names_all_five_fields() -> None:
    required = set(DECIDED)
    paths = sorted((ROOT / "src").rglob("*.py"))
    paths.extend(sorted((ROOT / "scripts").rglob("*.py")))
    paths.extend(sorted((ROOT / "tests").rglob("*.py")))
    incomplete: list[str] = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else None
            if name != "LadderSpec":
                continue
            keywords = {item.arg for item in node.keywords if item.arg is not None}
            intentional_guard_call = path == Path(__file__) and any(
                item.arg is None for item in node.keywords
            )
            if not intentional_guard_call and (node.args or keywords != required):
                incomplete.append(
                    f"{path.relative_to(ROOT)}:{node.lineno}: {sorted(keywords)}"
                )

    assert incomplete == [], "incomplete LadderSpec call sites:\n" + "\n".join(incomplete)


def _is_literal(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return True
    return isinstance(node, ast.UnaryOp) and isinstance(node.operand, ast.Constant)


def test_production_ladder_calls_never_pass_literal_values() -> None:
    """Rule ⑥ forbids the ACT of baking an undecided value in, not just last time's values
    (review 09-06 13:30: a call with five fresh literals passed the two guards above). In
    src/ and scripts/ every LadderSpec keyword must be a name/expression the caller resolved
    (CLI argument, candidate definition, grid row) — never an inline literal. Tests and
    fixtures are exempt: literals are normal there."""
    paths = sorted((ROOT / "src").rglob("*.py")) + sorted((ROOT / "scripts").rglob("*.py"))
    literal_calls: list[str] = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = node.func.id if isinstance(node.func, ast.Name) else None
            if name != "LadderSpec":
                continue
            for keyword in node.keywords:
                if keyword.arg is not None and _is_literal(keyword.value):
                    literal_calls.append(
                        f"{path.relative_to(ROOT)}:{node.lineno}: {keyword.arg}=<literal>"
                    )

    assert literal_calls == [], (
        "LadderSpec called with inline literal values in production code (rule ⑥):\n"
        + "\n".join(literal_calls)
    )

