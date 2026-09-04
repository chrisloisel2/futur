#!/usr/bin/env python3
"""
scripts/global_disk_watchdog.py
─────────────────────────────────────────────────────────────────────────────
GLOBAL_DISK_WATCHDOG (item P0.2, phase OPERATIONAL HARDENING + PORTFOLIO
TRUTH). Le garde-fou du collecteur microstructure (--min-free-disk-gb) ne
protège que LUI-MÊME : il ne voit pas les ~600GB consommés ailleurs sur la
machine (autres worktrees, Docker Desktop, /tmp -- cf P0.1 forensics,
2026-09-01). Ce watchdog surveille l'espace libre RÉEL de la machine,
indépendamment du processus responsable de sa consommation.

Seuils (inspectés contre l'état réel du système au 2026-09-01 : disque
915GB total, ~30GB libres au moment où ce watchdog a été écrit -- WARNING
est donc délibérément proche de l'état courant, ce n'est pas un seuil mal
calibré) :
  WARNING_FREE_GB  = 30  -> alerte seule (log), AUCUNE action.
  CRITICAL_FREE_GB = 20  -> stoppe PROPREMENT (systemctl --user stop --
                            SIGTERM puis SIGKILL après TimeoutStopSec, pas
                            un kill -9 direct) les collecteurs listés dans
                            CRITICAL_STOPPABLE.
  EMERGENCY_FREE_GB = 12 -> stoppe proprement CRITICAL_STOPPABLE +
                            EMERGENCY_STOPPABLE (tous les producteurs de
                            données non-essentiels).

JAMAIS de suppression/déplacement de données. JAMAIS d'arrêt d'un service
portant un state/ledger économique (paper trading, alpha20 tournament) --
les listes d'arrêt sont un OPT-IN explicite (jamais un "tout sauf X") :
un nouveau service futur non catégorisé n'est jamais stoppé par erreur, il
reste juste non couvert par le watchdog tant qu'il n'y est pas ajouté
explicitement.

Chaque exécution logue une ligne JSONL (append-only, jamais réécrit) dans
reports/ops/disk_watchdog.jsonl : timestamp, free_gb, level, action,
services_stopped, already_stopped.

Prévu pour être invoqué périodiquement par un timer systemd --user
(futur-disk-watchdog.timer, toutes les 5 min), même convention que le
reste du projet -- pas un démon séparé.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
LOG_PATH = ROOT / "reports" / "ops" / "disk_watchdog.jsonl"

WARNING_FREE_GB = 30.0
CRITICAL_FREE_GB = 20.0
EMERGENCY_FREE_GB = 12.0

# ⚠ VERROUILLAGE MUTUEL RÉEL, trouvé le 2026-09-04 (audit infra, item P0.4)
# ─────────────────────────────────────────────────────────────────────────
# `futur-microstructure-reduced.service` tourne avec `--min-free-disk-gb 20`.
# CRITICAL_FREE_GB valait AUSSI 20. Les deux seuils étant IDENTIQUES, le
# système à ~19 Go libres entrait dans un état absorbant :
#   - le watchdog stoppe le collecteur (free <= 20)
#   - le collecteur, relancé, refuse d'écrire (free < 20) et ressort
#   - rien, nulle part, ne libère d'espace
# Constaté : collecteur à l'arrêt depuis le 2026-09-04 06:46 UTC, jamais
# revenu, alors que le log ne montrait que des `already_inactive` — le
# watchdog croyait « avoir déjà agi » là où le système était en fait bloqué.
#
# Deux corrections :
#   1. HYSTÉRÉSIS. Un service arrêté pour cause de disque n'est considéré
#      comme relançable qu'AU-DESSUS de RESUME_FREE_GB, strictement supérieur
#      au seuil d'arrêt. Sans marge, tout redémarrage se ferait à la frontière
#      et rebasculerait immédiatement (battement).
#   2. DIAGNOSTIC EXPLICITE. Le watchdog détecte et LOGUE la condition de
#      verrouillage (`disk_deadlock`) au lieu de la laisser muette, et chiffre
#      l'espace récupérable en `.tmp` orphelins — le vrai levier, qui demande
#      une décision humaine (jamais de suppression automatique ici).
RESUME_FREE_GB = 40.0

# Plancher disque déclaré par les services surveillés (doit rester < au seuil
# d'arrêt correspondant, sinon verrouillage). Source de vérité : les
# ExecStart des units systemd --user.
# 2026-09-05 : plancher du collecteur abaissé de 20 à 15 Go dans son unit
# (entre EMERGENCY 12 et CRITICAL 20). Le watchdog est désormais la SEULE
# autorité qui l'arrête -- proprement, avec hystérésis (RESUME_FREE_GB) --
# et le plancher interne n'est plus qu'un filet de sécurité en dessous.
# `detect_threshold_deadlocks()` reste en place pour attraper toute
# réintroduction d'un plancher >= au seuil d'arrêt.
SERVICE_MIN_FREE_GB = {
    "futur-microstructure-reduced.service": 15.0,
}

# Dossiers balayés pour chiffrer les `.tmp` orphelins récupérables.
ORPHAN_TMP_DIRS = ["data/enriched"]

# ── listes explicites, opt-in uniquement ────────────────────────────────

# Collecteurs de données à forte écriture, purs producteurs (aucun
# state/ledger économique en jeu) -- stoppables sans risque de corruption.
CRITICAL_STOPPABLE = [
    "futur-microstructure-reduced.service",   # explicitement conçu comme le plus jetable (voir son design doc)
]

# Ensemble élargi à EMERGENCY : tous les collecteurs continus non-essentiels.
EMERGENCY_STOPPABLE = CRITICAL_STOPPABLE + [
    "futur-derivatives.service",     # alimente marks.py -- perte de fraîcheur tolérée (get_mark() dégrade proprement, jamais de prix halluciné)
    "futur-hl-collector.service",
    "futur-maker-probe.service",
]

# Ne JAMAIS toucher (état/ledgers économiques ou API servie) -- documenté
# pour la lisibilité, pas utilisé comme filtre (les listes ci-dessus sont
# déjà strictement opt-in).
ESSENTIAL_NEVER_STOP = [
    "futur-api.service", "futur-paper-v1.service", "futur-paper-mh.service",
    "futur-portfolio-mark.service", "futur-alpha20-tournament.service",
    "futur-alpha20-gate.service",
]


def free_gb() -> float:
    return shutil.disk_usage("/").free / (1024 ** 3)


def detect_threshold_deadlocks() -> list:
    """Un service dont le plancher disque est >= au seuil auquel le watchdog
    l'arrête ne peut PLUS JAMAIS redémarrer par lui-même. C'est une erreur de
    configuration, pas un incident : elle doit être visible dès qu'elle
    existe, pas seulement quand elle mord."""
    out = []
    for svc, floor in SERVICE_MIN_FREE_GB.items():
        stop_at = CRITICAL_FREE_GB if svc in CRITICAL_STOPPABLE else EMERGENCY_FREE_GB
        if floor >= stop_at:
            out.append({
                "service": svc, "service_min_free_gb": floor,
                "watchdog_stop_threshold_gb": stop_at,
                "problem": "le plancher du service est >= au seuil d'arrêt du watchdog : "
                           "état absorbant, le service ne peut jamais redémarrer seul",
            })
    return out


def reclaimable_orphan_tmp() -> dict:
    """Espace immobilisé par des écritures atomiques interrompues. Compté,
    JAMAIS supprimé ici (règle du projet : aucune suppression automatique) --
    c'est un chiffre à mettre sous les yeux de l'opérateur."""
    try:
        from src.institutional.data.atomic_parquet import sweep_orphan_tmp
    except Exception as exc:      # pragma: no cover
        return {"error": str(exc)[:200]}
    total, n, dirs = 0, 0, {}
    for rel in ORPHAN_TMP_DIRS:
        r = sweep_orphan_tmp(ROOT / rel, delete=False)
        dirs[rel] = {"n_orphans": r["n_orphans"], "total_gb": r["total_gb"]}
        total += int(r["total_bytes"]); n += int(r["n_orphans"])
    return {"n_orphans": n, "total_gb": round(total / (1024 ** 3), 3), "by_dir": dirs}


def is_active(service: str) -> bool:
    r = subprocess.run(["systemctl", "--user", "is-active", service],
                       capture_output=True, text=True)
    return r.stdout.strip() == "active"


def stop_service(service: str) -> str:
    """Retourne 'stopped', 'already_inactive', ou 'error:<message>'."""
    if not is_active(service):
        return "already_inactive"
    r = subprocess.run(["systemctl", "--user", "stop", service],
                       capture_output=True, text=True, timeout=60)
    if r.returncode == 0:
        return "stopped"
    return f"error:{r.stderr.strip()[:200]}"


def level_for(free: float) -> str:
    if free <= EMERGENCY_FREE_GB:
        return "EMERGENCY"
    if free <= CRITICAL_FREE_GB:
        return "CRITICAL"
    if free <= WARNING_FREE_GB:
        return "WARNING"
    return "OK"


def run_once() -> dict:
    free = free_gb()
    level = level_for(free)
    actions = {}

    to_stop = []
    if level == "CRITICAL":
        to_stop = CRITICAL_STOPPABLE
    elif level == "EMERGENCY":
        to_stop = EMERGENCY_STOPPABLE

    for svc in to_stop:
        actions[svc] = stop_service(svc)

    # item P0.4 : services à l'arrêt et non relançables, avec la RAISON.
    blocked = {}
    for svc, floor in SERVICE_MIN_FREE_GB.items():
        if is_active(svc):
            continue
        if free < max(floor, RESUME_FREE_GB):
            blocked[svc] = (f"disque insuffisant pour un redémarrage utile : "
                            f"{free:.1f} Go libres < max(plancher service {floor:.0f}, "
                            f"reprise {RESUME_FREE_GB:.0f})")
        else:
            blocked[svc] = "relançable (espace suffisant) -- redémarrage laissé à l'opérateur"

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "free_gb": round(free, 3), "level": level,
        "warning_threshold_gb": WARNING_FREE_GB, "critical_threshold_gb": CRITICAL_FREE_GB,
        "emergency_threshold_gb": EMERGENCY_FREE_GB, "resume_threshold_gb": RESUME_FREE_GB,
        "actions": actions,
        "stopped_services_status": blocked,
        "threshold_deadlocks": detect_threshold_deadlocks(),
        "reclaimable_orphan_tmp": reclaimable_orphan_tmp(),
    }
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a") as f:
        f.write(json.dumps(record) + "\n")

    return record


def main() -> int:
    record = run_once()
    print(f"[disk_watchdog] free={record['free_gb']:.2f}GB level={record['level']} "
         f"actions={record['actions'] or 'none'}", flush=True)
    for dl in record.get("threshold_deadlocks", []):
        print(f"[disk_watchdog] VERROUILLAGE DE SEUILS : {dl['service']} "
              f"(plancher {dl['service_min_free_gb']:.0f} Go >= arrêt "
              f"{dl['watchdog_stop_threshold_gb']:.0f} Go) -- {dl['problem']}", flush=True)
    orph = record.get("reclaimable_orphan_tmp") or {}
    if orph.get("total_gb"):
        print(f"[disk_watchdog] {orph['n_orphans']} .tmp orphelins = "
              f"{orph['total_gb']} Go récupérables (AUCUNE suppression automatique -- "
              f"scripts/sweep_orphan_tmp.py --delete pour décider)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
