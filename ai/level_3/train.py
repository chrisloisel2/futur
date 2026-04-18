"""
level_3/train.py — ORCHESTRATION DE L'ENTRAÎNEMENT DES EXPERTS
===============================================================

Point d'entrée principal pour entraîner tous les sous-modèles specialists
et assembler le routeur.

Workflow
--------
  1. Assigner les contextes à chaque barre (déterministe)
  2. Diagnostiquer la distribution des contextes
  3. Pour chaque contexte : entraîner un expert sur train ∩ contexte
  4. Valider chaque expert sur val ∩ contexte
  5. Assembler le routeur avec les experts acceptés
  6. Sauvegarder le routeur

Usage
-----
    from ai.level_3.train import train_specialists
    from pathlib import Path

    router = train_specialists(
        df=df_labeled,
        train_mask=train_mask,
        val_mask=val_mask,
        out_dir=Path("runs/pipeline/level_3"),
    )
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from ai.level_3.contexts import (
    MarketContext, ALL_CONTEXTS,
    assign_context, diagnose_context_distribution,
)
from ai.level_3.specialist import (
    train_specialist, SpecialistConfig,
    CONTEXT_SIDE, CONTEXT_FEATURES,
)
from ai.level_3.router import ContextRouter, RouterConfig


def train_specialists(
    df: pd.DataFrame,
    train_mask: np.ndarray,
    val_mask: np.ndarray,
    out_dir: Path,
    specialist_cfg: Optional[SpecialistConfig] = None,
    router_cfg: Optional[RouterConfig] = None,
    contexts: Optional[List[MarketContext]] = None,
    verbose: bool = True,
) -> ContextRouter:
    """
    Entraîne tous les experts par contexte et retourne le routeur assemblé.

    Arguments
    ---------
    df              : DataFrame avec features + labels (y_long, y_short)
    train_mask      : masque booléen split train
    val_mask        : masque booléen split val
    out_dir         : répertoire de sortie
    specialist_cfg  : config XGBoost pour les experts
    router_cfg      : config du routeur (seuils d'acceptation)
    contexts        : liste de contextes à entraîner (None = tous)
    verbose         : afficher les diagnostics détaillés

    Retourne
    --------
    ContextRouter prêt à l'emploi (experts enregistrés)
    """
    t_start = time.time()
    specialist_cfg = specialist_cfg or SpecialistConfig()
    router_cfg     = router_cfg     or RouterConfig()
    out_dir        = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    contexts_to_train = contexts or [
        MarketContext.TREND_LONG,
        MarketContext.TREND_SHORT,
        MarketContext.MEAN_REVERSION,
        MarketContext.BREAKOUT,
        MarketContext.HIGH_VOL,
    ]

    print("\n" + "=" * 70)
    print("STAGE 3 — EXPERTS PAR CONTEXTE DE MARCHÉ")
    print("=" * 70)
    print(f"   Contextes à entraîner : {[c.value for c in contexts_to_train]}")

    # ── Étape 1 : Assigner les contextes ─────────────────────────────────────
    print("\n   [1/4] Assignation des contextes...")
    context_series = assign_context(df)
    df = df.copy()
    df["market_context"] = context_series

    # ── Étape 2 : Diagnostiquer la distribution ───────────────────────────────
    print("\n   [2/4] Distribution des contextes :")
    all_mask = np.ones(len(df), dtype=bool)
    diagnose_context_distribution(
        df,
        masks={
            "train": train_mask,
            "val":   val_mask,
            "all":   all_mask,
        }
    )

    # Sauvegarder la distribution
    _save_context_distribution(df, train_mask, val_mask, out_dir)

    # ── Étape 3 : Entraîner les experts ───────────────────────────────────────
    print("\n   [3/4] Entraînement des experts...")
    router   = ContextRouter(router_cfg)
    results  = {}

    for context in contexts_to_train:
        ctx_val      = context.value
        context_mask = (df["market_context"] == ctx_val).values

        # Déterminer le côté (long/short/both) selon le contexte
        side = CONTEXT_SIDE.get(ctx_val, "long")

        # Ajuster le côté selon les labels disponibles
        side = _resolve_side(df, context, side)
        if side is None:
            print(f"\n   [{ctx_val}] ✗ aucun label disponible — ignoré")
            results[ctx_val] = {"status": "no_label"}
            continue

        expert = train_specialist(
            df           = df,
            context      = context,
            context_mask = context_mask,
            train_mask   = train_mask,
            val_mask     = val_mask,
            side         = side,
            out_dir      = out_dir,
            cfg          = specialist_cfg,
        )

        if expert is None:
            results[ctx_val] = {"status": "rejected"}
            continue

        # Enregistrer dans le routeur
        router.register_expert(
            context    = context,
            model      = expert["model"],
            scaler     = expert["scaler"],
            features   = expert["features"],
            metrics    = expert["metrics"],
            side       = expert["side"],
            calibrator = expert.get("calibrator"),
        )
        results[ctx_val] = {"status": "accepted", **expert["metrics"]}

    # ── Étape 4 : Sauvegarder le routeur ─────────────────────────────────────
    print("\n   [4/4] Sauvegarde du routeur...")
    router.save(out_dir)

    # Rapport final
    elapsed = time.time() - t_start
    _print_training_summary(results, elapsed)
    _save_training_report(results, out_dir, elapsed)

    return router


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_side(df: pd.DataFrame, context: MarketContext, default_side: str) -> Optional[str]:
    """
    Résout le côté effectif selon les labels disponibles dans df.
    TREND_LONG → y_long si disponible, sinon None
    TREND_SHORT → y_short si disponible, sinon None
    both → choisit y_long en priorité
    """
    if default_side == "long":
        return "long" if "y_long" in df.columns else None
    if default_side == "short":
        return "short" if "y_short" in df.columns else None
    if default_side == "both":
        if "y_long" in df.columns:
            return "both"
        if "y_short" in df.columns:
            return "short"
    return None


def _save_context_distribution(
    df: pd.DataFrame,
    train_mask: np.ndarray,
    val_mask: np.ndarray,
    out_dir: Path,
) -> None:
    """Sauvegarde la distribution des contextes en JSON."""
    dist = {}
    for split_name, mask in [("train", train_mask), ("val", val_mask), ("all", np.ones(len(df), bool))]:
        sub = df.loc[mask, "market_context"]
        n   = len(sub)
        split_dist = {}
        for ctx in MarketContext:
            count = int((sub == ctx.value).sum())
            split_dist[ctx.value] = {"n": count, "pct": round(count / max(n, 1), 3)}
        dist[split_name] = {"n_total": n, "contexts": split_dist}

    with open(out_dir / "context_distribution.json", "w") as f:
        json.dump(dist, f, indent=2)


def _print_training_summary(results: dict, elapsed: float) -> None:
    """Affiche le résumé de l'entraînement."""
    print("\n" + "─" * 70)
    print("   RÉSUMÉ LEVEL 3 — EXPERTS PAR CONTEXTE")
    print("─" * 70)
    accepted = [k for k, v in results.items() if v.get("status") == "accepted"]
    rejected = [k for k, v in results.items() if v.get("status") == "rejected"]
    no_label = [k for k, v in results.items() if v.get("status") == "no_label"]

    print(f"   Experts acceptés  : {len(accepted)}/{len(results)}")
    for ctx in accepted:
        r = results[ctx]
        print(f"     ✓ {ctx:<20}  AUC={r.get('auc','N/A')}  F1={r.get('macro_f1','N/A')}  "
              f"n_train={r.get('n_train','N/A')}")
    if rejected:
        print(f"   Experts rejetés   : {rejected}")
    if no_label:
        print(f"   Sans label        : {no_label}")
    print(f"\n   Durée totale : {elapsed:.1f}s")
    print("─" * 70)


def _save_training_report(results: dict, out_dir: Path, elapsed: float) -> None:
    """Sauvegarde le rapport complet de l'entraînement."""
    report = {
        "elapsed_seconds": round(elapsed, 1),
        "n_contexts_trained": len(results),
        "n_accepted": sum(1 for v in results.values() if v.get("status") == "accepted"),
        "experts": results,
    }
    with open(out_dir / "training_report.json", "w") as f:
        json.dump(report, f, indent=2)
