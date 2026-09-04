#!/usr/bin/env python3
"""
scripts/sweep_orphan_tmp.py
─────────────────────────────────────────────────────────────────────────────
Recense — et, UNIQUEMENT sur demande explicite, supprime — les fichiers
temporaires orphelins laissés par une écriture parquet atomique interrompue
(`src/institutional/data/atomic_parquet.py`).

Contexte (audit infrastructure 2026-09-04, item P0.4)
─────────────────────────────────────────────────────
`atomic_write_parquet` écrit `.<cible>.<hex>.tmp` à côté de sa cible puis
`os.replace`. Le nettoyage vivait dans un `finally`, qui ne survit ni à un
SIGKILL, ni à un OOM kill, ni à une coupure machine. Résultat mesuré :
62 orphelins pour 39,5 Go dans `data/enriched/`, datés du 4 juillet au
4 septembre — sur un disque à 98 % avec 19 Go libres.

La fuite elle-même est corrigée à la source (handler SIGTERM/SIGINT +
atexit). Ce script traite le stock déjà au sol, que rien ne peut nettoyer
rétroactivement.

DRY-RUN PAR DÉFAUT
──────────────────
Sans `--delete`, ce script n'écrit RIEN et ne supprime RIEN : il liste.
La règle du projet interdit toute suppression non demandée, et c'est à
l'humain de décider — même pour des fragments d'écriture avortée.

    python3 scripts/sweep_orphan_tmp.py                  # liste, ne touche à rien
    python3 scripts/sweep_orphan_tmp.py --delete         # supprime (décision explicite)
    python3 scripts/sweep_orphan_tmp.py --min-age-hours 24

Un `.tmp` plus récent que `--min-age-hours` (6 h par défaut) n'est JAMAIS
listé comme orphelin : il pourrait appartenir à une écriture en cours dans
un autre process.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.institutional.data.atomic_parquet import sweep_orphan_tmp

# Dossiers connus pour recevoir des écritures atomiques volumineuses.
DEFAULT_DIRS = ["data/enriched"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", action="append", default=None,
                    help="dossier à balayer (répétable). Défaut : %s" % DEFAULT_DIRS)
    ap.add_argument("--min-age-hours", type=float, default=6.0,
                    help="âge minimal pour considérer un .tmp comme orphelin (défaut 6 h)")
    ap.add_argument("--delete", action="store_true",
                    help="SUPPRIME réellement. Sans ce drapeau : dry-run, rien n'est touché.")
    ap.add_argument("--json", action="store_true", help="sortie JSON brute")
    args = ap.parse_args()

    dirs = args.dir or DEFAULT_DIRS
    results = [sweep_orphan_tmp(ROOT / d, delete=args.delete,
                                min_age_seconds=int(args.min_age_hours * 3600))
               for d in dirs]

    if args.json:
        print(json.dumps(results, indent=2))
        return 0

    total_bytes = sum(int(r["total_bytes"]) for r in results)
    total_n = sum(int(r["n_orphans"]) for r in results)
    for r in results:
        print(f"\n{r['directory']}  —  {r['n_orphans']} orphelin(s), {r['total_gb']} Go")
        for o in sorted(r["orphans"], key=lambda x: -x["bytes"]):
            print(f"   {o['bytes'] / (1024 ** 3):8.3f} Go  "
                  f"{o['age_seconds'] // 3600:5d} h  {Path(o['path']).name}")
    print(f"\nTOTAL : {total_n} orphelin(s), {total_bytes / (1024 ** 3):.2f} Go")
    if args.delete:
        deleted = sum(len(r["deleted"]) for r in results)
        print(f"SUPPRIMÉS : {deleted} fichier(s).")
    else:
        print("DRY-RUN : rien n'a été supprimé. Relancer avec --delete pour agir.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
