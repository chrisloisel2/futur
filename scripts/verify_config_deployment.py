#!/usr/bin/env python3
"""
scripts/verify_config_deployment.py
─────────────────────────────────────────────────────────────────────────────
Réconciliation Mac/qbee suite à l'incident du 2026-07-21 : la démotion de
`carry_solusdt`/`carry_bnbusdt` (déjà commitée sur Mac) n'avait jamais
atteint le fichier live de qbee, qui tourne sur une branche différente
(`feat/free-derivatives-backfill`) jamais synchronisée avec `main`. Le
dépôt de recherche et le comportement réel du tournoi pouvaient raconter
deux histoires différentes sans qu'aucun avertissement n'existe.

Ce script ne fait qu'un DIAGNOSTIC : il calcule le hash des fichiers de
config qui pilotent la sélection du tournoi (configs/alpha20_runners.yaml,
configs/alpha20.yaml) localement et sur un hôte distant (qbee), et signale
toute divergence. Il ne déploie rien automatiquement et ne modifie aucun
fichier — le déploiement atomique + vérification au démarrage de
l'orchestrateur (proposés dans le README de ce commit) restent à construire
séparément, avec une décision humaine explicite avant de toucher au code
de démarrage d'un système qui tourne en direct.

Usage :
    python3 scripts/verify_config_deployment.py                 # hash local seul
    python3 scripts/verify_config_deployment.py --remote qbee@100.127.59.114
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRACKED_CONFIGS = [
    "configs/alpha20_runners.yaml",
    "configs/alpha20.yaml",
]


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def local_manifest() -> dict:
    git_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.strip()
    hashes = {rel: file_hash(ROOT / rel) for rel in TRACKED_CONFIGS}
    return {
        "host": "local",
        "git_commit": git_commit,
        "config_hash": hashes,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def remote_manifest(remote: str) -> dict:
    """Hash des mêmes fichiers sur l'hôte distant, via SSH -- ne suppose PAS
    que son état git correspond au nôtre (c'est précisément ce qu'on vérifie),
    lit juste les fichiers de travail tels qu'ils sont réellement."""
    remote_script = (
        "cd ~/futur && "
        "git rev-parse HEAD 2>/dev/null; echo '---'; "
        "git branch --show-current 2>/dev/null; echo '---'; "
        + "; ".join(f"sha256sum {rel} 2>/dev/null || shasum -a 256 {rel}"
                    for rel in TRACKED_CONFIGS)
    )
    proc = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", remote, remote_script],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return {"host": remote, "error": proc.stderr.strip() or "ssh command failed",
               "generated_at": datetime.now(timezone.utc).isoformat()}
    lines = proc.stdout.strip().split("\n")
    sep_idx = [i for i, l in enumerate(lines) if l == "---"]
    git_commit = lines[0] if sep_idx else None
    branch = lines[sep_idx[0] + 1] if len(sep_idx) >= 1 else None
    hash_lines = lines[sep_idx[1] + 1:] if len(sep_idx) >= 2 else []
    hashes = {}
    for rel, line in zip(TRACKED_CONFIGS, hash_lines):
        parts = line.split()
        hashes[rel] = parts[0] if parts else None
    return {
        "host": remote,
        "git_commit": git_commit,
        "git_branch": branch,
        "config_hash": hashes,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--remote", default=None, help="user@host à comparer via SSH")
    args = ap.parse_args()

    local = local_manifest()
    print(json.dumps(local, indent=2))

    if not args.remote:
        return

    remote = remote_manifest(args.remote)
    print(json.dumps(remote, indent=2))

    if "error" in remote:
        print(f"\nDIAGNOSTIC: impossible de joindre {args.remote} ({remote['error']})")
        sys.exit(2)

    drift = {rel: {"local": local["config_hash"][rel], "remote": remote["config_hash"].get(rel)}
            for rel in TRACKED_CONFIGS
            if local["config_hash"][rel] != remote["config_hash"].get(rel)}

    if drift:
        print(f"\nDIAGNOSTIC: DÉRIVE DÉTECTÉE entre local (commit {local['git_commit'][:8]}) "
             f"et {args.remote} (branche {remote.get('git_branch')}, "
             f"commit {(remote.get('git_commit') or '?')[:8]}) :")
        print(json.dumps(drift, indent=2))
        sys.exit(1)
    else:
        print(f"\nDIAGNOSTIC: aucune dérive — {args.remote} sert exactement les fichiers "
             f"de config actuels de ce commit.")


if __name__ == "__main__":
    main()
