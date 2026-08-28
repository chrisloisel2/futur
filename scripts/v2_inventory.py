#!/usr/bin/env python3
"""Phase 0 (V2 master prompt) — regenerate the repo runtime inventory.

Recomputes, from the live tree, the facts an inventory classification is
based on: .py file counts per top-level directory, last git-touched date,
and whether anything under the currently-active research/runtime dirs
imports it. The CLASSIFICATION table below is a human judgement call
(CANONICAL_CANDIDATE / MIGRATE / LEGACY / BROKEN / UNVERIFIED) recorded
alongside the evidence it was made from — it is NOT recomputed, so a
directory whose "referenced_by_active_dirs" fact contradicts its recorded
classification is flagged as DRIFT for a human to re-triage.

Usage:
    python3 scripts/v2_inventory.py            # print report
    python3 scripts/v2_inventory.py --write    # also write docs/v2/INVENTORY.generated.md
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

TOP_LEVEL_DIRS = [
    "Server", "ai", "artifacts", "bin", "config", "configs", "core", "data",
    "data_pipeline", "deploy", "frontend_pipeline", "hedge_fund", "legacy",
    "production", "reports", "research", "risk", "runs", "scripts", "src",
    "state", "tests", "trading-system",
]

# Dirs treated as "currently active" for the reverse-import check.
ACTIVE_SEARCH_DIRS = ["src", "scripts", "research", "tests", "configs"]

# Phase 0 first-pass classification. Evidence for each verdict lives in
# docs/v2/MIGRATION.md. Re-triage whenever `drift` below is non-empty.
CLASSIFICATION = {
    "src": ("CANONICAL_CANDIDATE", "most recently touched runtime code (src/institutional, src/alpha20); "
            "has real exposure-limit enforcement (constraints.py, invariants.py) wired into backtest path"),
    "research": ("CANONICAL_CANDIDATE", "edge_factory: preregistration + governance + forensics discipline, "
                 "most recently touched (2026-07-22)"),
    "configs": ("CANONICAL_CANDIDATE", "active config tree used by src/, recently touched"),
    "reports": ("CANONICAL_CANDIDATE", "append-only research/experiment artifacts; do not delete or rewrite"),
    "data": ("CANONICAL_CANDIDATE", "append-only market/research data; do not delete or rewrite"),
    "scripts": ("MIGRATE", "148 files, mixture of live tooling still imported by research/ and one-off/dead scripts; "
                "needs per-file triage before src/futur/ migration"),
    "tests": ("MIGRATE", "root pytest suite covering alpha20/portfolio/multileg; no root pytest.ini, "
              "rootdir behavior unverified"),
    "core": ("MIGRATE", "still imported by scripts/ (walkforward_v3, paper_long_signal, backfill); "
             "overlaps src/institutional, needs consolidation not deletion"),
    "config": ("MIGRATE", "still imported by scripts/paper_long_signal.py, scripts/paper_multi_signal.py"),
    "ai": ("MIGRATE", "still imported by scripts/ (backfill_enriched_from_binance, walkforward_v3)"),
    "risk": ("MIGRATE", "still imported by scripts/paper_*_signal.py"),
    "data_pipeline": ("MIGRATE", "still imported by scripts/ (archive_derivatives, bootstrap_enriched, live_data_update)"),
    "frontend_pipeline": ("UNVERIFIED", "bind-mounted into docker-compose command-center service, exposed via "
                           "ngrok tunnel per docker-compose.yml; live-traffic status not verified this pass"),
    "artifacts": ("UNVERIFIED", "0 top-level .py, contains model/data registries; retention semantics not verified"),
    "runs": ("UNVERIFIED", "0 top-level .py, batch run logs; retention semantics not verified"),
    "state": ("UNVERIFIED", "0 top-level .py, purpose not verified this pass"),
    "Server": ("UNVERIFIED", "not referenced from any ACTIVE_SEARCH_DIRS in first-pass grep; needs confirmation before LEGACY"),
    "production": ("UNVERIFIED", "not referenced from any ACTIVE_SEARCH_DIRS in first-pass grep; docker-compose "
                   "command-center may still depend on it via a path this grep missed"),
    "trading-system": ("UNVERIFIED", "separate pyproject.toml (institutional v0.1.0) and its own tests/; hyphenated "
                        "dir name means it cannot be imported as a package — relationship to src/institutional unclear"),
    "hedge_fund": ("UNVERIFIED", "0 .py files at any depth found by this pass; check for non-.py runtime content before classifying LEGACY"),
    "bin": ("UNVERIFIED", "0 top-level .py; contains shell/launcher scripts, not yet inventoried"),
    "deploy": ("UNVERIFIED", "systemd units referenced by docs? not yet cross-checked against production/"),
    "legacy": ("LEGACY", "self-declared by directory name; last touched 2026-05-27 (stale vs. src/research at 2026-07-22); "
               "must be made non-importable by the runtime in Phase 1"),
}


def run(cmd: list[str]) -> str:
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False).stdout.strip()


def py_file_count(d: str) -> int:
    return len(list((ROOT / d).rglob("*.py"))) if (ROOT / d).is_dir() else 0


def last_git_touch(d: str) -> str:
    return run(["git", "log", "-1", "--format=%ad", "--date=short", "--", d]) or "UNKNOWN"


def referenced_by_active_dirs(d: str) -> list[str]:
    """Grep ACTIVE_SEARCH_DIRS for `import <d>` / `from <d>` style references."""
    module_name = d.replace("-", "_")
    hits: list[str] = []
    for active in ACTIVE_SEARCH_DIRS:
        if active == d:
            continue
        out = run([
            "grep", "-rlE", f"^(from|import) {module_name}(\\.| )",
            active,
        ])
        if out:
            hits.extend(out.splitlines())
    return hits


def build_report() -> str:
    head = run(["git", "rev-parse", "HEAD"])
    now = datetime.now(timezone.utc).isoformat()
    lines = [
        "<!-- AUTO-GENERATED by scripts/v2_inventory.py — do not hand-edit. -->",
        f"# V2 Phase 0 — Repo Inventory (generated {now}, HEAD={head})",
        "",
        "| dir | py_files | last_git_touch | referenced_by_active | classification | drift | note |",
        "|---|---|---|---|---|---|---|",
    ]
    for d in TOP_LEVEL_DIRS:
        n = py_file_count(d)
        last = last_git_touch(d)
        refs = referenced_by_active_dirs(d)
        cls, note = CLASSIFICATION.get(d, ("UNCLASSIFIED", "not yet triaged"))
        drift = ""
        if cls in ("LEGACY", "UNVERIFIED") and refs:
            drift = f"DRIFT: referenced by {len(refs)} file(s) in active dirs despite {cls} classification"
        ref_summary = f"{len(refs)} file(s)" if refs else "none found"
        lines.append(f"| {d} | {n} | {last} | {ref_summary} | {cls} | {drift} | {note} |")
    lines.append("")
    lines.append("Regenerate with: `python3 scripts/v2_inventory.py --write`")
    return "\n".join(lines)


def main() -> int:
    report = build_report()
    print(report)
    if "--write" in sys.argv:
        out = ROOT / "docs" / "v2" / "INVENTORY.generated.md"
        out.write_text(report + "\n")
        print(f"\nwrote {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
