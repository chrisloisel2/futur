"""
tests/test_no_forbidden_imports_from_src.py -- Phase 2 rebuild gate.

Structural rule, not a one-time finding: nothing under src/ may depend on
legacy/ (non-importable archive, see legacy/__init__.py), frontend_pipeline/
(retired frontend_pipeline/api_server.py moved to legacy/dead_frontend/;
the rest of frontend_pipeline/ is a Docker-deployed dashboard, a consumer of
src/, never the other way around), or the second, divergently-broken
`trading-system/` institutional package (see docs/FOUNDATION_AUDIT.md §5).

This must fail loudly the moment any of those three dependencies is
reintroduced -- that is the point of the test, not an incidental property.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"

# Anchored to the start of an import statement's module path so a real,
# active module that merely CONTAINS the word "legacy" (e.g.
# src/institutional/engines/legacy_bridge.py, imported throughout the live
# backtest/runner path) never false-positives: "legacy_bridge" is not
# followed by ".", whitespace, or end-of-line, so it can't match.
FORBIDDEN_IMPORT_PATTERN = re.compile(
    r"^\s*(?:from|import)\s+(legacy|frontend_pipeline)(?:\.|\s|$)",
    re.MULTILINE,
)

# trading-system/ can't be imported via normal Python syntax (hyphen in the
# name) -- the real risk is sys.path manipulation or importlib dynamic
# loading pointing at it. Checked as a blanket substring rather than an
# import-statement pattern: confirmed zero occurrences anywhere in src/
# today (any phrasing), so this is safe to be maximally strict from the
# start rather than trying to enumerate every way it could be referenced.
FORBIDDEN_STRING_PATTERN = re.compile(r"trading[-_]system", re.IGNORECASE)


def _all_src_py_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def test_src_directory_has_python_files_to_check():
    """Sanity check the scan below isn't silently checking zero files."""
    files = _all_src_py_files()
    assert len(files) > 50, (
        f"expected src/ to contain many .py files, found {len(files)} -- "
        f"the scan below would pass vacuously if this drops to 0."
    )


def test_no_module_under_src_imports_legacy_or_frontend_pipeline():
    violations = []
    for f in _all_src_py_files():
        text = f.read_text(encoding="utf-8", errors="replace")
        for m in FORBIDDEN_IMPORT_PATTERN.finditer(text):
            line_no = text.count("\n", 0, m.start()) + 1
            violations.append(f"{f.relative_to(ROOT)}:{line_no} imports {m.group(1)!r}")
    assert not violations, (
        "src/ must never import from legacy/ or frontend_pipeline/ "
        "(Phase 2 rebuild rule) -- violations found:\n" + "\n".join(violations)
    )


def test_no_module_under_src_references_the_trading_system_path():
    violations = []
    for f in _all_src_py_files():
        text = f.read_text(encoding="utf-8", errors="replace")
        if FORBIDDEN_STRING_PATTERN.search(text):
            violations.append(str(f.relative_to(ROOT)))
    assert not violations, (
        "src/ must never reference the second trading-system/ institutional "
        "package, by any mechanism (Phase 2 rebuild rule) -- found in:\n"
        + "\n".join(violations)
    )
