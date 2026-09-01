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
LOG_PATH = ROOT / "reports" / "ops" / "disk_watchdog.jsonl"

WARNING_FREE_GB = 30.0
CRITICAL_FREE_GB = 20.0
EMERGENCY_FREE_GB = 12.0

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

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "free_gb": round(free, 3), "level": level,
        "warning_threshold_gb": WARNING_FREE_GB, "critical_threshold_gb": CRITICAL_FREE_GB,
        "emergency_threshold_gb": EMERGENCY_FREE_GB,
        "actions": actions,
    }
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a") as f:
        f.write(json.dumps(record) + "\n")

    return record


def main() -> int:
    record = run_once()
    print(f"[disk_watchdog] free={record['free_gb']:.2f}GB level={record['level']} "
         f"actions={record['actions'] or 'none'}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
