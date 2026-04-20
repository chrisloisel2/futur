"""
level_3/predict.py — INFÉRENCE AVEC LES EXPERTS SPÉCIALISÉS
============================================================

Ce module est le point d'entrée pour l'inférence live ou batch.
Il combine level_2 (edge global) et level_3 (experts par contexte).

Flux d'inférence
----------------
  1. Recevoir une barre (ou un batch) avec ses features
  2. Assigner le contexte de marché (router déterministe)
  3. Obtenir p_long et p_short de level_2 (requis)
  4. Si un expert est disponible pour ce contexte et accepté :
       p_final = (1 - α) * p_level2 + α * p_specialist
     Sinon :
       p_final = p_level2  ← pas de dégradation possible

Garantie de sécurité
--------------------
  Le niveau 3 ne peut PAS dégrader les performances de level_2.
  Si tous les experts sont rejetés ou non disponibles, le comportement
  est identique à un pipeline sans level_3.

Usage batch (backtest)
----------------------
    from ai.level_3.predict import SpecialistPredictor
    predictor = SpecialistPredictor.load(Path("runs/pipeline/level_3"))
    routing_df = predictor.predict_batch(df, p_long_l2, p_short_l2)
    # routing_df.p_long_final, routing_df.p_short_final

Usage live (une barre)
----------------------
    result = predictor.predict_row(row_dict, p_long=0.68, p_short=0.32)
    # result.p_long_final, result.context, result.expert_used
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Union

import numpy as np
import pandas as pd

from ai.level_3.contexts import MarketContext, assign_context
from ai.level_3.router import ContextRouter, RouterConfig, RoutingResult


# ─────────────────────────────────────────────────────────────────────────────
# Prédicteur principal
# ─────────────────────────────────────────────────────────────────────────────

class SpecialistPredictor:
    """
    Wraps le routeur pour l'inférence.
    Gère les cas dégénérés (router absent, experts tous rejetés, etc.)

    Cycle de vie :
        predictor = SpecialistPredictor.load(router_dir)
        df_result = predictor.predict_batch(df, p_long, p_short)
    """

    def __init__(self, router: ContextRouter):
        self.router = router

    # ──────────────────────────────────────────────────────────────────────────
    # Chargement
    # ──────────────────────────────────────────────────────────────────────────

    @classmethod
    def load(
        cls,
        router_dir: Path,
        cfg: Optional[RouterConfig] = None,
    ) -> "SpecialistPredictor":
        """Charge le prédicteur depuis le disque."""
        router = ContextRouter.load(Path(router_dir), cfg)
        n_accepted = sum(
            1 for e in router._experts.values() if e.get("accepted", False)
        )
        print(f"   SpecialistPredictor chargé : {n_accepted} expert(s) actif(s)")
        return cls(router)

    @classmethod
    def from_router(cls, router: ContextRouter) -> "SpecialistPredictor":
        """Crée un prédicteur depuis un routeur déjà assemblé."""
        return cls(router)

    # ──────────────────────────────────────────────────────────────────────────
    # Inférence batch
    # ──────────────────────────────────────────────────────────────────────────

    def predict_batch(
        self,
        df: pd.DataFrame,
        p_long_l2: Union[np.ndarray, pd.Series],
        p_short_l2: Union[np.ndarray, pd.Series],
        context_col: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        Prédit pour toutes les barres du DataFrame.

        Arguments
        ---------
        df           : DataFrame avec les features
        p_long_l2    : probabilités long de level_2
        p_short_l2   : probabilités short de level_2
        context_col  : colonne de contexte préassignée (optionnel)

        Retourne
        --------
        DataFrame avec colonnes :
          market_context, p_long_final, p_short_final,
          p_long_specialist, p_short_specialist,
          specialist_weight, expert_used
        """
        p_l2 = np.asarray(p_long_l2,  dtype=np.float64)
        p_s2 = np.asarray(p_short_l2, dtype=np.float64)

        return self.router.route_batch(df, p_l2, p_s2, context_col)

    # ──────────────────────────────────────────────────────────────────────────
    # Inférence sur une seule barre (live)
    # ──────────────────────────────────────────────────────────────────────────

    def predict_row(
        self,
        row: Union[dict, pd.Series],
        p_long: float,
        p_short: float,
    ) -> RoutingResult:
        """
        Prédit pour une seule barre.

        Arguments
        ---------
        row     : dict ou pd.Series avec les features
        p_long  : probabilité long de level_2
        p_short : probabilité short de level_2

        Retourne
        --------
        RoutingResult avec p_long_final, p_short_final, context, expert_used
        """
        if isinstance(row, dict):
            df_row = pd.DataFrame([row])
        else:
            df_row = row.to_frame().T

        result_df = self.predict_batch(
            df_row,
            p_long_l2  = np.array([p_long]),
            p_short_l2 = np.array([p_short]),
        )

        r = result_df.iloc[0]
        return RoutingResult(
            context             = str(r["market_context"]),
            expert_used         = bool(r["expert_used"]),
            p_long_final        = float(r["p_long_final"]),
            p_short_final       = float(r["p_short_final"]),
            p_long_specialist   = float(r["p_long_specialist"])  if not np.isnan(r["p_long_specialist"])  else None,
            p_short_specialist  = float(r["p_short_specialist"]) if not np.isnan(r["p_short_specialist"]) else None,
            specialist_weight   = float(r["specialist_weight"]),
            reason              = f"expert={'yes' if r['expert_used'] else 'no (fallback level_2)'}",
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Diagnostic post-inférence
    # ──────────────────────────────────────────────────────────────────────────

    def diagnose(
        self,
        df: pd.DataFrame,
        p_long_l2: np.ndarray,
        p_short_l2: np.ndarray,
        y_long: Optional[np.ndarray] = None,
        y_short: Optional[np.ndarray] = None,
    ) -> Dict:
        """
        Analyse l'impact de level_3 par contexte.

        Si y_long/y_short sont fournis, calcule l'AUC comparative
        level_2 vs level_3 par contexte.

        Retourne un rapport détaillé.
        """
        result_df = self.predict_batch(df, p_long_l2, p_short_l2)

        report = {
            "n_total":      len(df),
            "n_expert_used": int(result_df["expert_used"].sum()),
            "pct_expert_used": round(result_df["expert_used"].mean(), 3),
            "by_context": {},
        }

        print(f"\n   Diagnostic Level 3 :")
        print(f"   Expert utilisé sur {report['pct_expert_used']:.1%} des barres "
              f"({report['n_expert_used']:,}/{report['n_total']:,})")

        for ctx in MarketContext:
            ctx_mask = result_df["market_context"] == ctx.value
            n_ctx = int(ctx_mask.sum())
            if n_ctx == 0:
                continue

            n_used = int(result_df.loc[ctx_mask, "expert_used"].sum())
            avg_weight = float(result_df.loc[ctx_mask, "specialist_weight"].mean())

            ctx_report: Dict = {
                "n": n_ctx,
                "n_expert_used": n_used,
                "pct_expert_used": round(n_used / max(n_ctx, 1), 3),
                "avg_specialist_weight": round(avg_weight, 3),
            }

            # Comparer AUC si labels fournis
            if y_long is not None and n_used > 50:
                from sklearn.metrics import roc_auc_score
                try:
                    p_l2_ctx = p_long_l2[ctx_mask.values]
                    p_l3_ctx = result_df.loc[ctx_mask, "p_long_final"].values
                    y_ctx    = y_long[ctx_mask.values]
                    valid    = y_ctx >= 0
                    if valid.sum() > 30:
                        auc_l2 = float(roc_auc_score(y_ctx[valid], p_l2_ctx[valid]))
                        auc_l3 = float(roc_auc_score(y_ctx[valid], p_l3_ctx[valid]))
                        ctx_report["auc_level2"] = round(auc_l2, 4)
                        ctx_report["auc_level3"] = round(auc_l3, 4)
                        ctx_report["auc_delta"]  = round(auc_l3 - auc_l2, 4)
                        sign = "↑" if auc_l3 > auc_l2 else "↓"
                        print(f"     {ctx.value:<20}  n={n_ctx:>5,}  "
                              f"AUC L2={auc_l2:.4f}  L3={auc_l3:.4f}  "
                              f"{sign}{abs(auc_l3-auc_l2):.4f}")
                except Exception:
                    pass

            report["by_context"][ctx.value] = ctx_report

        return report

    # ──────────────────────────────────────────────────────────────────────────
    # Résumé
    # ──────────────────────────────────────────────────────────────────────────

    def summary(self) -> dict:
        """Délègue au routeur."""
        return self.router.summary()
