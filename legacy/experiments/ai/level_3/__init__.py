"""
level_3 — CONDITIONAL SPECIALISTS (EXPERTS PAR CONTEXTE DE MARCHÉ)
===================================================================

Ce niveau entraîne des sous-modèles spécialisés par contexte de marché.
Chaque expert est conditionné sur un contexte détecté déterministiquement
par level_3 et reçoit uniquement les barres correspondantes à l'entraînement.

Architecture
------------
  contexts.py    → 5 contextes + règles déterministes de détection
  router.py      → sélectionne l'expert + fusionne avec level_2
  specialist.py  → entraîne un XGBoost par contexte
  train.py       → orchestration complète
  predict.py     → inférence batch et live

5 Contextes
-----------
  TREND_LONG     : tendance haussière structurelle (EMA alignées + momentum)
  TREND_SHORT    : tendance baissière structurelle (death cross + momentum négatif)
  MEAN_REVERSION : extension extrême + signal de retournement
  BREAKOUT       : cassure directionnelle forte (eff_ratio + boll_expansion)
  HIGH_VOL       : spike de volatilité (rv_ratio_24_72 > 1.6)

Principe de fusion conservative
---------------------------------
  p_final = (1 - α) * p_level2 + α * p_specialist
  - α ∈ [0, 0.6] selon l'AUC de l'expert sur val
  - Si l'expert est rejeté (AUC < 0.56), α = 0 → fallback level_2
  - GARANTIE : level_3 ne peut pas dégrader level_2

Usage typique (training)
------------------------
    from ai.level_3 import train_specialists
    from pathlib import Path

    router = train_specialists(
        df=df_labeled,
        train_mask=train_mask,
        val_mask=val_mask,
        out_dir=Path("runs/pipeline/level_3"),
    )

Usage typique (inférence)
-------------------------
    from ai.level_3 import SpecialistPredictor
    from pathlib import Path

    predictor = SpecialistPredictor.load(Path("runs/pipeline/level_3"))
    result_df = predictor.predict_batch(df, p_long_l2, p_short_l2)
    # result_df.p_long_final  → probabilité long fusionnée
    # result_df.p_short_final → probabilité short fusionnée

API publique
------------
"""
from ai.level_3.contexts import (
    MarketContext,
    ALL_CONTEXTS,
    LONG_CONTEXTS,
    SHORT_CONTEXTS,
    assign_context,
    diagnose_context_distribution,
)

from ai.level_3.router import (
    ContextRouter,
    RouterConfig,
    RoutingResult,
)

from ai.level_3.specialist import (
    SpecialistConfig,
    train_specialist,
    CONTEXT_FEATURES,
    CONTEXT_SIDE,
    FEATURES_MR,
    FEATURES_BREAKOUT,
    FEATURES_HIGH_VOL,
)

from ai.level_3.train import train_specialists

from ai.level_3.predict import SpecialistPredictor


__all__ = [
    # Contextes
    "MarketContext",
    "ALL_CONTEXTS",
    "LONG_CONTEXTS",
    "SHORT_CONTEXTS",
    "assign_context",
    "diagnose_context_distribution",
    # Routeur
    "ContextRouter",
    "RouterConfig",
    "RoutingResult",
    # Expert
    "SpecialistConfig",
    "train_specialist",
    "CONTEXT_FEATURES",
    "CONTEXT_SIDE",
    "FEATURES_MR",
    "FEATURES_BREAKOUT",
    "FEATURES_HIGH_VOL",
    # Orchestration
    "train_specialists",
    # Inférence
    "SpecialistPredictor",
]
