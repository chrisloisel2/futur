"""
src/alpha20/guard.py — garde structurelle : AUCUN ordre réel ne peut partir.

Appelée en tout premier par CHAQUE entrypoint du tournoi (orchestrateur, mark,
réconciliation, dashboard, sélection). Trois vérifications indépendantes,
toutes doivent passer :

  1. Aucune variable d'environnement d'activation réelle n'est positionnée
     (liste blanche fermée — toute variable contenant TRADING/BROKER/ORDER
     couplée à ENABLE/LIVE/REAL est inspectée) ;
  2. configs/alpha20.yaml ne déclare aucune section `live`/`real_broker` avec
     `enabled: true` ;
  3. Aucun module d'exécution réelle n'existe dans le dépôt sous
     src/alpha20/execution/ — REAL_ADAPTER_MODULES est une liste blanche
     VIDE ; tout nom de fichier qui apparaît dans ce dossier et qui n'est pas
     dans FROZEN_PAPER_MODULES fait échouer la garde (empêche l'ajout futur
     d'un adapter réel sans toucher explicitement ce fichier).

`assert_paper_only()` lève SystemExit(2) si une seule condition échoue — le
process ne démarre pas. Aucun mode dégradé, aucun bypass par argument CLI.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import List

from src.alpha20 import CONFIG_PATH, ROOT, load_config

EXEC_DIR = ROOT / "src" / "alpha20" / "execution"
# Whitelist FERMÉE des modules d'exécution paper autorisés. Tout .py présent
# dans src/alpha20/execution/ qui n'est PAS ici fait échouer la garde.
FROZEN_PAPER_MODULES = {
    "__init__.py", "hedge_coordinator.py", "smart_router.py", "maker_model.py",
    "paper_broker.py",
}
_ENV_PATTERN = re.compile(r"(TRADING|BROKER|ORDER).*?(ENABLE|LIVE|REAL)"
                          r"|(ENABLE|LIVE|REAL).*?(TRADING|BROKER|ORDER)", re.I)


class RealTradingGuardError(RuntimeError):
    pass


def _check_env() -> List[str]:
    hits = []
    for k, v in os.environ.items():
        if _ENV_PATTERN.search(k) and str(v).strip().lower() in ("1", "true", "yes", "on"):
            hits.append(f"env {k}={v}")
    return hits


def _check_config() -> List[str]:
    hits = []
    cfg = load_config()
    for key in ("live", "real_broker", "real_trading"):
        section = cfg.get(key)
        if isinstance(section, dict) and section.get("enabled"):
            hits.append(f"configs/alpha20.yaml:{key}.enabled=true")
    return hits


def _check_modules() -> List[str]:
    if not EXEC_DIR.exists():
        return []
    unknown = sorted(p.name for p in EXEC_DIR.glob("*.py")
                     if p.name not in FROZEN_PAPER_MODULES)
    return [f"module d'exécution non whitelisté: src/alpha20/execution/{n}"
            for n in unknown]


def assert_paper_only(exit_on_fail: bool = True) -> None:
    hits = _check_env() + _check_config() + _check_modules()
    if hits:
        msg = ("REAL TRADING GUARD — démarrage refusé. Signaux détectés:\n  - "
               + "\n  - ".join(hits))
        if exit_on_fail:
            import sys
            print(msg, file=sys.stderr)
            raise SystemExit(2)
        raise RealTradingGuardError(msg)
