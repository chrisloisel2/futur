#!/usr/bin/env python3
"""
scripts/backup_critical_state.py
─────────────────────────────────────────────────────────────────────────────
SAUVEGARDE de ce qui ne se re-télécharge pas — manifeste SHA-256, copie,
vérification, et restauration TESTÉE.

Le constat
──────────
125,8 M de barres, les ledgers de décisions scellés, les états de portefeuille :
tout sur une seule machine. `disk_watchdog.jsonl` surveille l'ESPACE, pas la
SURVIE. Une sauvegarde jamais restaurée est une hypothèse.

Le tri, qui est l'essentiel
───────────────────────────
`data/` pèse 79 Go, mais la moitié n'a aucune valeur de sauvegarde :

  IRREMPLAÇABLE — sortie d'un collecteur en temps réel. Une fois la fenêtre de
  rétention de l'exchange passée, c'est perdu définitivement. En particulier
  `derivatives_raw` : l'endpoint `openInterestHist` ne retient que ~30 jours,
  donc TOUT ce qui a plus d'un mois y est déjà irrécupérable.

  RE-TÉLÉCHARGEABLE — archives publiques (Binance Vision) ou dérivés
  calculables. `data/enriched/` pèse 48 Go à lui seul, soit 60 % de `data/`,
  et il est à la fois DÉRIVÉ et périmé (dernière barre fin juin 2026 pour 40
  des 50 symboles). Le sauvegarder coûterait 60 % du volume pour 0 % du
  risque.

Sauvegarder 79 Go au lieu de 26 n'est pas « plus prudent » : c'est trois fois
plus long, trois fois plus cher, et ça rend la restauration assez lourde pour
qu'on ne la teste jamais. Ce qui est exactement la façon dont les sauvegardes
échouent.

Usage
─────
  --manifest            écrit le manifeste SHA-256 du périmètre critique
  --copy DEST           copie le périmètre vers DEST (rsync, incrémental)
  --verify DEST         recalcule les SHA-256 côté DEST et compare
  --test-restore        restaure un échantillon vers un répertoire jetable et
                        vérifie octet à octet — la seule preuve qui compte
  --pack DEST           regroupe les partitions CLOSES en archives par
                        (symbole, jour) — sans quoi la copie est impraticable

Pourquoi --pack existe
──────────────────────
Le périmètre pèse 17,26 Go... répartis sur 3 285 247 fichiers, dont
3 171 884 dans `data/derivatives_raw` à **2,9 Ko de moyenne**. Soit 97 % du
compte de fichiers pour 51 % des octets.

Trois conséquences, toutes coûteuses :
  - sur un système de fichiers à blocs de 4 Ko, 2,9 Ko en occupent 4 : 8,85 Go
    de contenu tiennent 17 Go de disque, soit ~8 Go de pure perte — sur un
    disque à 94 % ;
  - un rsync fichier par fichier est dominé par le coût par fichier, pas par
    le débit ;
  - un stockage objet facture à la requête : 3,17 M de PUT est le vrai poste
    de dépense, pas les 17 Go.

Regrouper par (symbole, jour) ramène 3,17 M de fichiers à ~3 500 archives,
un facteur ~900. `--pack` ne touche QUE les partitions closes (date < ce jour)
et ne supprime JAMAIS l'original : le collecteur écrit en continu dans la
partition du jour, et une sauvegarde qui casse la collecte serait pire que pas
de sauvegarde.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "reports" / "ops" / "BACKUP_MANIFEST.json"

# Périmètre critique, avec le MOTIF de chaque entrée. Une liste sans motifs
# dérive : personne n'ose retirer une ligne dont il ignore pourquoi elle est là,
# et la sauvegarde grossit jusqu'à ne plus être testable.
CRITICAL = [
    ("data/derivatives_raw", "IRREMPLAÇABLE — collecteur REST ~5 min. "
     "L'endpoint openInterestHist ne retient que ~30 j : tout ce qui est plus "
     "vieux est DÉJÀ irrécupérable côté exchange."),
    ("data/microstructure_reduced", "IRREMPLAÇABLE — bande BBO/trades websocket. "
     "Aucune API ne rejoue un carnet passé."),
    ("data/spread_probe", "IRREMPLAÇABLE — coupe transversale du spread et du "
     "volume, échantillonnée toutes les 15 min. Un instant passé ne se resonde pas."),
    ("data/derivatives_live_metrics", "IRREMPLAÇABLE au-delà de ~30 j — queue "
     "live qui prolonge l'archive Vision."),
    ("data/hyperliquid", "IRREMPLAÇABLE — collecteur metaorders/l2Book."),
    ("data/positioning", "IRREMPLAÇABLE — séries de positionnement collectées."),
    ("data/execution_probe", "IRREMPLAÇABLE — sondes d'exécution horodatées."),
    ("reports/live_alpha_lab", "IRREMPLAÇABLE — ledgers de décisions et de "
     "résultats SCELLÉS, états de portefeuille. C'est la PREUVE forward ; "
     "append-only, donc non reconstructible."),
    ("reports/edge_discovery", "IRREMPLAÇABLE en pratique — des semaines de "
     "campagnes de recherche."),
    ("configs", "petit et critique — inclut les fichiers gitignorés "
     "(command_center_users.json) absents du dépôt."),
    ("state", "petit et critique — secrets de session, absents du dépôt."),
]

# Explicitement HORS périmètre, avec le motif. Aussi important que la liste
# ci-dessus : c'est ce qui empêche la sauvegarde de regrossir sans raison.
EXCLUDED = [
    ("data/enriched", "48 Go — DÉRIVÉ de klines publiques, et périmé (dernière "
     "barre fin juin 2026 pour 40 des 50 symboles). 60 % du volume, 0 % du risque."),
    ("data/derivatives_backfill", "5,7 Go — archives PUBLIQUES Binance Vision, "
     "re-téléchargeables à l'identique."),
    ("data/options_backfill", "586 Mo — re-téléchargeable depuis Deribit."),
    ("data/listings_backfill", "70 Mo — re-téléchargeable."),
    ("data/session_*", "sessions de recherche datées de mai 2026, dérivées."),
]

_HASH_CHUNK = 1 << 20


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_HASH_CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def walk(rel: str, base: Path = ROOT):
    d = base / rel
    if d.is_file():
        yield d
        return
    if not d.is_dir():
        return
    for dirpath, _, filenames in os.walk(d):
        for fn in sorted(filenames):
            f = Path(dirpath) / fn
            if f.is_file() and not f.is_symlink():
                yield f


def build_manifest(sample_only: bool = False) -> dict:
    """SHA-256 par fichier. `sample_only` hache 1 fichier sur 200 — utile pour
    vérifier la MÉCANIQUE sans passer 20 minutes à relire 26 Go."""
    entries, total_bytes, n_files, skipped = {}, 0, 0, 0
    for rel, why in CRITICAL:
        for f in walk(rel):
            n_files += 1
            size = f.stat().st_size
            total_bytes += size
            if sample_only and n_files % 200 != 0:
                skipped += 1
                continue
            entries[str(f.relative_to(ROOT))] = {"sha256": sha256_file(f), "bytes": size}
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "root": str(ROOT),
        "mode": "SAMPLE" if sample_only else "FULL",
        "critical_scope": [{"path": p, "why": w} for p, w in CRITICAL],
        "excluded_scope": [{"path": p, "why": w} for p, w in EXCLUDED],
        "n_files_in_scope": n_files,
        "n_files_hashed": len(entries),
        "n_files_skipped_sampling": skipped,
        "total_bytes": total_bytes,
        "total_gib": round(total_bytes / 1024 ** 3, 2),
        "files": entries,
    }


def verify(manifest: dict, dest: Path) -> dict:
    """Recalcule les SHA-256 côté destination. Un fichier ABSENT et un fichier
    ALTÉRÉ sont comptés séparément : ce ne sont pas la même panne."""
    missing, changed, ok = [], [], 0
    for rel, meta in manifest["files"].items():
        f = dest / rel
        if not f.is_file():
            missing.append(rel)
            continue
        if sha256_file(f) != meta["sha256"]:
            changed.append(rel)
            continue
        ok += 1
    return {"n_ok": ok, "n_missing": len(missing), "n_changed": len(changed),
            "missing": missing[:20], "changed": changed[:20],
            "verified_at": datetime.now(timezone.utc).isoformat()}


def copy_to(dest: Path) -> int:
    dest.mkdir(parents=True, exist_ok=True)
    for rel, _ in CRITICAL:
        src = ROOT / rel
        if not src.exists():
            print(f"[backup] ⚠ absent, ignoré : {rel}", flush=True)
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        rc = subprocess.call(["rsync", "-a", "--delete",
                              f"{src}/" if src.is_dir() else str(src), str(target)])
        print(f"[backup] {'✓' if rc == 0 else '✗'} {rel} -> {target}", flush=True)
        if rc != 0:
            return rc
    return 0


def test_restore(n_sample: int = 40) -> dict:
    """LA preuve. Restaure un échantillon vers un répertoire jetable et
    compare octet à octet. Une sauvegarde jamais restaurée est une hypothèse ;
    ce test est ce qui la transforme en fait."""
    files = []
    for rel, _ in CRITICAL:
        got = list(walk(rel))
        # un échantillon PAR entrée, pour qu'aucune ne soit jamais non testée
        step = max(1, len(got) // max(1, n_sample // max(1, len(CRITICAL))))
        files.extend(got[::step][:n_sample])
    files = files[:n_sample]
    if not files:
        return {"status": "NO_FILES_IN_SCOPE"}
    tmp = Path(tempfile.mkdtemp(prefix="restore-test-"))
    try:
        ok, bad = 0, []
        for f in files:
            rel = f.relative_to(ROOT)
            target = tmp / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)
            if sha256_file(target) == sha256_file(f) and target.stat().st_size == f.stat().st_size:
                ok += 1
            else:
                bad.append(str(rel))
        return {"status": "PASS" if not bad else "FAIL", "n_tested": len(files),
                "n_identical": ok, "mismatches": bad[:10],
                "scratch_dir": str(tmp), "tested_at": datetime.now(timezone.utc).isoformat()}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def pack(dest: Path, rel: str = "data/derivatives_raw") -> dict:
    """Archive chaque partition CLOSE en un .tar.gz, sans toucher l'original.

    « Close » = `date=` strictement antérieure à aujourd'hui (UTC). La
    partition du jour est en cours d'écriture par le collecteur : l'archiver
    donnerait une archive tronquée, et la relire pendant l'écriture est le
    genre de course qui produit un fichier gzip incomplet -- exactement celui
    rencontré dans le tape microstructure en analysant les spreads.
    """
    import tarfile
    src_root = ROOT / rel
    if not src_root.is_dir():
        return {"status": "SOURCE_ABSENTE", "path": rel}
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    dest.mkdir(parents=True, exist_ok=True)
    made, skipped_open, already = 0, 0, 0
    for part in sorted(src_root.glob("**/date=*")):
        if not part.is_dir():
            continue
        if part.name.split("=", 1)[1] >= today:
            skipped_open += 1
            continue
        rel_part = part.relative_to(ROOT)
        out = dest / (str(rel_part).replace("/", "__") + ".tar.gz")
        if out.exists():
            already += 1
            continue
        tmp = out.with_suffix(".tar.gz.tmp")
        with tarfile.open(tmp, "w:gz") as tf:
            tf.add(part, arcname=str(rel_part))
        # renommage atomique : une archive n'apparaît sous son nom définitif
        # que complète. Une archive partielle portant le bon nom serait
        # comptée comme sauvegardée.
        tmp.rename(out)
        made += 1
        if made % 200 == 0:
            print(f"[backup] {made} archives...", flush=True)
    return {"status": "OK", "n_archives_created": made,
            "n_already_present": already, "n_open_partitions_skipped": skipped_open,
            "dest": str(dest)}


def main() -> int:
    ap = argparse.ArgumentParser(description="Sauvegarde du périmètre critique.")
    ap.add_argument("--manifest", action="store_true")
    ap.add_argument("--sample", action="store_true",
                    help="avec --manifest : ne hache qu'un fichier sur 200")
    ap.add_argument("--copy", metavar="DEST")
    ap.add_argument("--verify", metavar="DEST")
    ap.add_argument("--test-restore", action="store_true")
    ap.add_argument("--pack", metavar="DEST",
                    help="regroupe les partitions closes de derivatives_raw en archives")
    ap.add_argument("--scope", action="store_true", help="affiche le périmètre et sort")
    args = ap.parse_args()

    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)

    if args.scope:
        print("PÉRIMÈTRE CRITIQUE (sauvegardé) :")
        for p, w in CRITICAL:
            d = ROOT / p
            size = sum(f.stat().st_size for f in walk(p)) / 1024 ** 3 if d.exists() else 0.0
            print(f"  {size:7.2f} Go  {p}\n              {w}")
        print("\nHORS PÉRIMÈTRE (délibérément) :")
        for p, w in EXCLUDED:
            print(f"  {p}\n              {w}")
        return 0

    if args.manifest:
        m = build_manifest(sample_only=args.sample)
        MANIFEST_PATH.write_text(json.dumps(m, indent=2, ensure_ascii=False))
        print(f"[backup] manifeste {m['mode']} : {m['n_files_hashed']}/{m['n_files_in_scope']} "
              f"fichiers hachés, {m['total_gib']} Go de périmètre -> {MANIFEST_PATH}")

    if args.test_restore:
        r = test_restore()
        print(f"[backup] restauration testée : {r['status']} "
              f"({r.get('n_identical')}/{r.get('n_tested')} identiques octet à octet)")
        if r["status"] != "PASS":
            print(f"[backup]   écarts : {r.get('mismatches')}")
            return 1

    if args.pack:
        r = pack(Path(args.pack))
        print(f"[backup] pack : {r}")

    if args.copy:
        rc = copy_to(Path(args.copy))
        if rc != 0:
            return rc

    if args.verify:
        if not MANIFEST_PATH.exists():
            print("[backup] ✗ pas de manifeste — lancer --manifest d'abord")
            return 1
        r = verify(json.loads(MANIFEST_PATH.read_text()), Path(args.verify))
        print(f"[backup] vérification : {r['n_ok']} OK, {r['n_missing']} manquants, "
              f"{r['n_changed']} altérés")
        return 0 if (r["n_missing"] == 0 and r["n_changed"] == 0) else 1

    if not any((args.manifest, args.copy, args.verify, args.test_restore, args.pack)):
        ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
