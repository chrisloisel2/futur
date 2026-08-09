#!/usr/bin/env python3
"""
scripts/generate_deployment_manifest.py — approuve l'état ACTUEL des
fichiers de config suivis (configs/alpha20_runners.yaml, configs/alpha20.yaml)
sur CETTE machine.

À exécuter manuellement, jamais automatiquement, après toute modification
volontaire et relue par un humain de ces fichiers -- y compris après avoir
vérifié qu'un changement distant (ex. une démotion de runner décidée sur
une autre machine) a bien été appliqué ici. Écrit
configs/DEPLOYMENT_MANIFEST.json (jamais committé -- état propre à cette
machine, voir src/alpha20/deployment_guard.py).

    .venv/bin/python scripts/generate_deployment_manifest.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.alpha20.deployment_guard import MANIFEST_PATH, TRACKED_CONFIGS, current_hashes  # noqa: E402


def main() -> None:
    git_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True
    ).stdout.strip() or None
    git_branch = subprocess.run(
        ["git", "branch", "--show-current"], cwd=ROOT, capture_output=True, text=True
    ).stdout.strip() or None

    manifest = {
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_commit,
        "git_branch": git_branch,
        "tracked_files": TRACKED_CONFIGS,
        "config_hash": current_hashes(),
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2))
    print(json.dumps(manifest, indent=2))
    print(f"\n-> {MANIFEST_PATH}")


if __name__ == "__main__":
    main()
