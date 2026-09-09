"""Rule 1, enforced rather than documented.

Walks the syntax tree of every P0 file and fails on an import from the frozen
tree.  Reading a data file those systems produced is fine; importing their logic
is what this forbids.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

P0_PACKAGES = ("research_kernel", "mechanisms", "paper_engine", "execution_engine")

FORBIDDEN_ROOTS = (
    "ai",
    "trading_system",
    "trading-system",
    "frontend_pipeline",
    "signals",
    "scrapers",
    "legacy",
    "core",
    "risk",
    "hedge_fund",
    "production",
    "data_pipeline",
    "data_v2",
    "alpha_foundry_v5",
    "market_physics_v3",
    "src.futur",
    "futur",
    "institutional",
)


def _p0_files():
    for pkg in P0_PACKAGES:
        d = REPO_ROOT / pkg
        if not d.exists():
            continue
        for f in sorted(d.rglob("*.py")):
            yield f


def _imported_roots(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import, cannot leave the package
                continue
            if node.module:
                yield node.module


@pytest.mark.parametrize("path", list(_p0_files()), ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_no_legacy_import(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offending = []
    for module in _imported_roots(tree):
        root = module.split(".")[0]
        if root in FORBIDDEN_ROOTS or module.startswith("src.futur"):
            offending.append(module)
    assert not offending, "%s imports the frozen tree: %s" % (
        path.relative_to(REPO_ROOT),
        ", ".join(offending),
    )


def test_the_guard_would_catch_a_violation(tmp_path):
    bad = tmp_path / "bad.py"
    bad.write_text("from ai.level_2 import something\n", encoding="utf-8")
    tree = ast.parse(bad.read_text(encoding="utf-8"))
    roots = [m.split(".")[0] for m in _imported_roots(tree)]
    assert "ai" in roots and "ai" in FORBIDDEN_ROOTS
