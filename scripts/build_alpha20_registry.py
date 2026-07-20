#!/usr/bin/env python3
"""
scripts/build_alpha20_registry.py — RECONSTRUIT le stamp de version du
registre configs/alpha20_runners.yaml (built_at, git_commit). Les
config_hash ne sont PAS stockés (dérivés à chaque chargement par
runner_registry — voir docstring du module). N'exécuter QUE sur changement
délibéré du registre, jamais automatiquement (le registre n'est pas un
artefact de cycle).
"""
from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "configs" / "alpha20_runners.yaml"


def main() -> None:
    sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                         capture_output=True, text=True, check=True
                         ).stdout.strip()
    built_at = datetime.now(timezone.utc).isoformat()
    # réécriture ciblée des deux lignes d'en-tête, préserve tout le reste tel quel
    lines = PATH.read_text().splitlines()
    out = []
    for l in lines:
        if l.startswith("built_at:"):
            out.append(f'built_at: "{built_at}"          '
                       f'# stampé par le script de build')
        elif l.startswith("git_commit:"):
            out.append(f'git_commit: "{sha}"                     '
                       f'# HEAD au moment du build')
        else:
            out.append(l)
    PATH.write_text("\n".join(out) + "\n")
    yaml.safe_load(PATH.read_text())          # valide la réécriture avant de sortir
    print(f"registre stampé: git_commit={sha} built_at={built_at}")


if __name__ == "__main__":
    main()
