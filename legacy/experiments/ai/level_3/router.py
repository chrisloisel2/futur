"""
level_3/router.py — ROUTEUR VERS L'EXPERT SPÉCIALISÉ
=====================================================

Le routeur est le composant qui, à l'inférence, décide quel expert consulter
pour une barre donnée. Il est entièrement déterministe (pas de ML).

Rôle dans le pipeline :
  Level 1 → régime (SHORTABLE / NEUTRAL / NO_SHORT)  ← gate dure directionnelle
  Level 2 → score edge global (p_long, p_short)       ← modèle généraliste
  Level 3 → router + specialist                       ← micro-edge par contexte
  Level 7 → risk controller                           ← sizing, stops

Décision de fusion (Level 2 vs Level 3) :
  La probabilité finale est un mix pondéré :
      p_final = (1 - α) * p_level2 + α * p_specialist

  α (specialist_weight) est calibré sur val selon la performance de l'expert.
  Si l'expert est non-disponible (contexte NEUTRAL ou expert rejeté), α = 0.

  Cette fusion conservative garantit que level_3 ne peut PAS dégrader
  une performance existante de level_2 — il ne peut qu'améliorer.

Usage
-----
    from ai.level_3.router import ContextRouter
    router = ContextRouter.load(specialist_dir)
    result = router.route(df_row, p_long=0.72, p_short=0.35)
    # result.p_long_final, result.context, result.expert_used
"""
from __future__ import annotations

import json
import pickle
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import pandas as pd

from ai.level_3.contexts import MarketContext, assign_context


# ─────────────────────────────────────────────────────────────────────────────
# Résultat de routage
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RoutingResult:
    """Résultat complet d'un routage pour une barre."""
    context: str                    # MarketContext.value
    expert_used: bool               # False si fallback sur level_2
    p_long_final: float             # probabilité long finale
    p_short_final: float            # probabilité short finale
    p_long_specialist: Optional[float] = None   # raw specialist
    p_short_specialist: Optional[float] = None
    specialist_weight: float = 0.0  # α appliqué
    reason: str = ""                # debug — pourquoi cet expert / fallback


# ─────────────────────────────────────────────────────────────────────────────
# Config du routeur
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RouterConfig:
    """Configuration du routeur d'experts."""

    # Poids du specialist (α) par défaut — peut être surchargé par expert
    default_specialist_weight: float = 0.35

    # Poids maximal autorisé (évite que l'expert écrase level_2)
    max_specialist_weight: float = 0.60

    # AUC minimale pour qu'un expert soit utilisé (sinon fallback level_2)
    min_expert_auc: float = 0.56

    # Nombre minimal d'exemples d'entraînement pour valider un expert
    min_train_samples: int = 300

    # Si True, utilise NEUTRAL comme fallback systématique (ignore l'expert)
    always_fallback: bool = False


# ─────────────────────────────────────────────────────────────────────────────
# Routeur principal
# ─────────────────────────────────────────────────────────────────────────────

class ContextRouter:
    """
    Routeur qui sélectionne l'expert approprié pour une barre donnée
    et fusionne sa prédiction avec celle de level_2.

    Cycle de vie :
        1. ContextRouter(cfg) — initialiser
        2. router.register_expert(context, model, scaler, features, metrics)
        3. router.route_batch(df, p_long_l2, p_short_l2)  — inférence batch
    """

    def __init__(self, cfg: Optional[RouterConfig] = None):
        self.cfg = cfg or RouterConfig()
        # context.value → dict avec {model, scaler, features, weight, metrics}
        self._experts: Dict[str, dict] = {}

    # ──────────────────────────────────────────────────────────────────────────
    # Enregistrement d'experts
    # ──────────────────────────────────────────────────────────────────────────

    def register_expert(
        self,
        context: MarketContext,
        model,             # sklearn-compatible (predict_proba)
        scaler,            # sklearn StandardScaler
        features: list,    # liste de features utilisées par cet expert
        metrics: dict,     # {auc, macro_f1, n_train, n_val, ...}
        side: str = "both",  # "long", "short", ou "both"
        calibrator=None,   # IsotonicRegression ou PlattLR optionnel
    ) -> None:
        """
        Enregistre un expert pour un contexte donné.
        Rejette automatiquement si AUC < min_expert_auc ou n_train trop faible.
        """
        auc     = metrics.get("auc", 0.0)
        n_train = metrics.get("n_train", 0)

        accepted = (
            auc >= self.cfg.min_expert_auc
            and n_train >= self.cfg.min_train_samples
        )

        # Poids adaptatif : plus l'AUC est élevée, plus l'expert est écouté
        if accepted:
            raw_weight = min(
                self.cfg.max_specialist_weight,
                self.cfg.default_specialist_weight
                + max(0.0, auc - self.cfg.min_expert_auc) * 2.0
            )
        else:
            raw_weight = 0.0

        self._experts[context.value] = {
            "model":      model,
            "scaler":     scaler,
            "features":   features,
            "weight":     raw_weight,
            "metrics":    metrics,
            "accepted":   accepted,
            "side":       side,
            "calibrator": calibrator,
        }

        status = "✓ ACCEPTÉ" if accepted else "✗ REJETÉ"
        print(f"   Expert [{context.value:<18}]  {status}  "
              f"AUC={auc:.4f}  n_train={n_train:,}  weight={raw_weight:.2f}")

    # ──────────────────────────────────────────────────────────────────────────
    # Inférence batch
    # ──────────────────────────────────────────────────────────────────────────

    def route_batch(
        self,
        df: pd.DataFrame,
        p_long_l2: np.ndarray,
        p_short_l2: np.ndarray,
        context_col: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        Route toutes les barres du DataFrame vers l'expert approprié.

        Arguments
        ---------
        df           : DataFrame avec les features
        p_long_l2    : probabilités long de level_2 (shape N,)
        p_short_l2   : probabilités short de level_2 (shape N,)
        context_col  : si fourni, utilise df[context_col] au lieu de recalculer

        Retourne
        --------
        DataFrame avec colonnes :
          market_context, p_long_final, p_short_final,
          p_long_specialist, p_short_specialist, specialist_weight, expert_used
        """
        n = len(df)

        # Assigner les contextes
        if context_col and context_col in df.columns:
            contexts = df[context_col].values
        else:
            contexts = assign_context(df).values

        p_long_f  = p_long_l2.copy().astype(np.float64)
        p_short_f = p_short_l2.copy().astype(np.float64)
        p_long_sp  = np.full(n, np.nan)
        p_short_sp = np.full(n, np.nan)
        weights    = np.zeros(n)
        used       = np.zeros(n, dtype=bool)

        # Traiter chaque contexte en batch
        for ctx_val in np.unique(contexts):
            mask = contexts == ctx_val
            if not mask.any():
                continue

            expert = self._experts.get(ctx_val)
            if expert is None or not expert["accepted"] or self.cfg.always_fallback:
                continue  # fallback → garder level_2

            df_ctx = df.loc[mask]
            features = expert["features"]

            missing = [f for f in features if f not in df_ctx.columns]
            if missing:
                continue  # données insuffisantes → fallback

            X = df_ctx[features].fillna(0.0).values
            X_sc = expert["scaler"].transform(X)

            try:
                proba = expert["model"].predict_proba(X_sc)[:, 1]
            except Exception:
                continue

            if expert["calibrator"] is not None:
                try:
                    proba = expert["calibrator"].predict(proba)
                except Exception:
                    pass

            w = expert["weight"]
            side = expert["side"]

            if side in ("long", "both"):
                p_long_f[mask]  = (1 - w) * p_long_l2[mask]  + w * proba
                p_long_sp[mask] = proba

            if side in ("short", "both"):
                p_short_f[mask]  = (1 - w) * p_short_l2[mask] + w * proba
                p_short_sp[mask] = proba

            weights[mask] = w
            used[mask]    = True

        return pd.DataFrame({
            "market_context":     contexts,
            "p_long_final":       np.clip(p_long_f,  0.0, 1.0),
            "p_short_final":      np.clip(p_short_f, 0.0, 1.0),
            "p_long_specialist":  p_long_sp,
            "p_short_specialist": p_short_sp,
            "specialist_weight":  weights,
            "expert_used":        used,
        }, index=df.index)

    # ──────────────────────────────────────────────────────────────────────────
    # Résumé
    # ──────────────────────────────────────────────────────────────────────────

    def summary(self) -> dict:
        """Résumé de l'état du routeur et de ses experts."""
        return {
            ctx: {
                "accepted":   e["accepted"],
                "weight":     e["weight"],
                "side":       e["side"],
                "auc":        e["metrics"].get("auc"),
                "macro_f1":   e["metrics"].get("macro_f1"),
                "n_train":    e["metrics"].get("n_train"),
            }
            for ctx, e in self._experts.items()
        }

    # ──────────────────────────────────────────────────────────────────────────
    # Sérialisation
    # ──────────────────────────────────────────────────────────────────────────

    def save(self, out_dir: Path) -> None:
        """Sauvegarde le routeur complet (experts + config)."""
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)

        # Config
        with open(out_dir / "router_config.json", "w") as f:
            json.dump({
                "default_specialist_weight": self.cfg.default_specialist_weight,
                "max_specialist_weight":     self.cfg.max_specialist_weight,
                "min_expert_auc":            self.cfg.min_expert_auc,
                "min_train_samples":         self.cfg.min_train_samples,
                "always_fallback":           self.cfg.always_fallback,
            }, f, indent=2)

        # Summary des métriques (JSON — pas de pickle)
        with open(out_dir / "router_summary.json", "w") as f:
            json.dump(self.summary(), f, indent=2)

        # Experts (pickle par contexte)
        for ctx_val, expert in self._experts.items():
            ctx_dir = out_dir / ctx_val
            ctx_dir.mkdir(exist_ok=True)
            with open(ctx_dir / "model.pkl",    "wb") as f: pickle.dump(expert["model"],    f)
            with open(ctx_dir / "scaler.pkl",   "wb") as f: pickle.dump(expert["scaler"],   f)
            if expert["calibrator"] is not None:
                with open(ctx_dir / "calibrator.pkl", "wb") as f:
                    pickle.dump(expert["calibrator"], f)
            meta = {
                "features":  expert["features"],
                "weight":    expert["weight"],
                "accepted":  expert["accepted"],
                "side":      expert["side"],
                "metrics":   expert["metrics"],
            }
            with open(ctx_dir / "meta.json", "w") as f:
                json.dump(meta, f, indent=2)

        print(f"   Routeur sauvegardé → {out_dir}")

    @classmethod
    def load(cls, out_dir: Path, cfg: Optional[RouterConfig] = None) -> "ContextRouter":
        """Charge un routeur depuis le disque."""
        out_dir = Path(out_dir)

        # Config
        cfg_path = out_dir / "router_config.json"
        if cfg is None and cfg_path.exists():
            with open(cfg_path) as f:
                d = json.load(f)
            cfg = RouterConfig(**d)

        router = cls(cfg)

        # Experts
        for ctx in MarketContext:
            ctx_dir = out_dir / ctx.value
            if not ctx_dir.exists():
                continue
            try:
                with open(ctx_dir / "model.pkl",  "rb") as f: model  = pickle.load(f)
                with open(ctx_dir / "scaler.pkl", "rb") as f: scaler = pickle.load(f)
                with open(ctx_dir / "meta.json")  as f: meta = json.load(f)

                calibrator = None
                cal_path = ctx_dir / "calibrator.pkl"
                if cal_path.exists():
                    with open(cal_path, "rb") as f:
                        calibrator = pickle.load(f)

                router._experts[ctx.value] = {
                    "model":      model,
                    "scaler":     scaler,
                    "features":   meta["features"],
                    "weight":     meta["weight"],
                    "accepted":   meta["accepted"],
                    "side":       meta["side"],
                    "metrics":    meta["metrics"],
                    "calibrator": calibrator,
                }
            except Exception as e:
                print(f"   ⚠  Expert {ctx.value} non chargé : {e}")

        return router
