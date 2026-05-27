"""
level_7/state.py — PERSISTANCE DE L'ÉTAT DU RISK CONTROLLER
============================================================

Sépare la logique de persistance du RiskController.
Le RiskController connaît les règles de risque.
Ce module sait comment le sauvegarder et le recharger entre les runs.

Usage :
    rc = load_or_create_risk_controller(run_dir / "risk_state_long.json", cfg_long)
    # ... trading loop ...
    save_risk_state(rc, run_dir / "risk_state_long.json")
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from ai.level_7.config import RiskConfig


def load_or_create_risk_controller(
    state_path: Optional[Path],
    cfg: RiskConfig,
):
    """
    Charge l'état d'un RiskController depuis un fichier JSON, ou en crée un nouveau.

    Arguments
    ---------
    state_path : chemin vers le fichier JSON de sauvegarde (None = nouveau RC)
    cfg        : configuration du risque (RiskConfig)

    Retourne
    --------
    RiskController initialisé depuis l'état sauvegardé, ou neuf si absent.
    """
    try:
        from ai.models.level_7.RiskController import RiskController, RiskConfig as _RC
    except ImportError:
        raise ImportError(
            "RiskController introuvable. Vérifier ai/models/level_7/RiskController.py"
        )

    rc_cfg = _RC(
        rv_key=cfg.rv_key,
        min_abs_edge=cfg.min_abs_edge,
        min_scale=cfg.min_scale,
        cooldown_bars=cfg.cooldown_bars,
        daily_loss_limit_pct=cfg.max_daily_drawdown_pct,
        max_consecutive_losses=cfg.max_consecutive_losses,
    )
    rc = RiskController(cfg=rc_cfg)

    if state_path is None or not Path(state_path).exists():
        return rc

    try:
        with open(state_path) as f:
            state = json.load(f)
        _restore_state(rc, state)
        print(f"   [RiskController {cfg.side.upper()}] État restauré depuis {state_path}")
    except Exception as e:
        print(f"   [RiskController {cfg.side.upper()}] ⚠  Échec du chargement : {e}")
        print(f"      → Démarrage avec un RC vierge")

    return rc


def save_risk_state(rc, state_path: Path) -> None:
    """
    Sauvegarde l'état courant du RiskController en JSON.
    """
    state = _extract_state(rc)
    state_path = Path(state_path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with open(state_path, "w") as f:
        json.dump(state, f, indent=2)


def _extract_state(rc) -> dict:
    """Extrait l'état mutable du RC en dictionnaire sérialisable."""
    state = {}
    for attr in [
        "consecutive_losses",
        "last_trade_bar",
        "total_trades",
        "total_wins",
        "day_pnl",
        "peak_equity",
        "current_equity",
        "bar_counter",
    ]:
        if hasattr(rc, attr):
            v = getattr(rc, attr)
            try:
                import numpy as np
                if isinstance(v, (np.integer, np.floating)):
                    v = v.item()
            except ImportError:
                pass
            state[attr] = v
    return state


def _restore_state(rc, state: dict) -> None:
    """Restaure l'état mutable sur un RC existant."""
    for attr, value in state.items():
        if hasattr(rc, attr):
            setattr(rc, attr, value)
