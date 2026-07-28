"""tests/architecture/test_truth_domain_has_no_alpha20_dependency.py --
Phase 4C gate: src/futur/truth/ must never import src.alpha20 or
src.institutional.

docs/TRUTH_ACCOUNTING.md has long claimed this boundary was enforced by
test_no_forbidden_imports_from_src.py -- it wasn't; that file only checks
for legacy/frontend_pipeline/trading-system, never alpha20/institutional.
That gap was harmless while nothing coupled the two domains, but Phase 4C
introduces the first real coupling (a shadow adapter that reads
src.alpha20's CarryBasisAdapter and feeds events into TruthEngine) -- the
direction of that dependency matters: alpha20 may import truth (the
adapter does, and legitimately so), truth must NEVER import alpha20 or
institutional back. This test is the actual enforcement that claim was
missing.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
TRUTH_SRC = ROOT / "src" / "futur" / "truth"

FORBIDDEN_IMPORT_PATTERN = re.compile(
    r"^\s*(?:from|import)\s+(src\.alpha20|src\.institutional|alpha20|institutional)(?:\.|\s|$)",
    re.MULTILINE,
)


def _all_truth_py_files() -> list[Path]:
    return sorted(TRUTH_SRC.rglob("*.py"))


def test_truth_directory_has_python_files_to_check():
    files = _all_truth_py_files()
    assert len(files) > 5, (
        f"expected src/futur/truth/ to contain several .py files, found "
        f"{len(files)} -- the scan below would pass vacuously if this drops to 0."
    )


def test_no_module_under_truth_imports_alpha20_or_institutional():
    violations = []
    for f in _all_truth_py_files():
        text = f.read_text(encoding="utf-8", errors="replace")
        for m in FORBIDDEN_IMPORT_PATTERN.finditer(text):
            line_no = text.count("\n", 0, m.start()) + 1
            violations.append(f"{f.relative_to(ROOT)}:{line_no} imports {m.group(1)!r}")
    assert not violations, (
        "src/futur/truth/ must never import src.alpha20 or src.institutional "
        "-- the shadow adapter (src/alpha20/tournament/truth_shadow/) may "
        "depend on truth, never the other way around. Violations found:\n"
        + "\n".join(violations)
    )
