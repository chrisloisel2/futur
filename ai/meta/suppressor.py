"""
ai/meta/suppressor.py — Meta-Suppression Layer

Combine toutes les sources de suppression pour décider si un trade doit être filtré:

  1. Régime PANIC → suppression totale des LONG
  2. OOD detection → si la barre est hors distribution
  3. Ensemble disagreement → si les modèles ne sont pas d'accord
  4. Conformal uncertainty → largeur d'intervalle > seuil (risk/uncertainty_gate.py)
  5. Liquidity stress → taille réduite si marché illiquide

Hiérarchie (ordre de priorité):
  BLOCKED   → trade supprimé (score > 0.7)
  REDUCED   → trade avec taille réduite (score ∈ [0.4, 0.7))
  ALLOWED   → trade normal (score < 0.4)

Usage:
  sup = MetaSuppressor(ood_detector, ensemble_disagreement)
  result = sup.evaluate(bar, X, {"p_long": 0.62}, regime="PANIC", uncertainty={"width": 0.35})
  if result.allow:
      size_mult = result.size_multiplier
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from ai.meta.ood_detector import OODDetector
from ai.meta.ensemble_disagreement import EnsembleDisagreement


@dataclass
class SuppressionResult:
    allow:           bool
    size_multiplier: float          # [0, 1] — 1.0 si allow, 0.0 si blocked
    score:           float          # score d'inhibition [0, 1]
    reasons:         list[str]      # liste des raisons de suppression
    level:           str            # "ALLOWED" | "REDUCED" | "BLOCKED"

    def summary(self) -> dict:
        return {
            "allow":           self.allow,
            "size_multiplier": self.size_multiplier,
            "score":           round(self.score, 4),
            "level":           self.level,
            "reasons":         self.reasons,
        }


class MetaSuppressor:
    """
    Filtre méta-niveau qui combine toutes les sources d'inhibition.

    Chaque source contribue une pénalité [0, 1]:
      - Régime dangereux:     0.0 à 1.0
      - OOD:                  0.0 à 0.5
      - Ensemble disagreement:0.0 à 0.5
      - Uncertainty conformal:0.0 à 0.4
      - Liquidity stress:     0.0 à 0.3 (réduit la taille seulement)

    Score final = clip(somme pondérée, 0, 1)
    BLOCKED si score > 0.70
    REDUCED si score ∈ [0.40, 0.70)
    ALLOWED si score < 0.40
    """

    BLOCKED_THR = 0.70
    REDUCED_THR = 0.40

    def __init__(
        self,
        ood_detector:         Optional[OODDetector] = None,
        ensemble:             Optional[EnsembleDisagreement] = None,
        regime_weight:        float = 0.40,
        ood_weight:           float = 0.25,
        ensemble_weight:      float = 0.20,
        uncertainty_weight:   float = 0.15,
        stress_weight:        float = 0.50,
    ):
        self._ood      = ood_detector
        self._ensemble = ensemble
        self._w = {
            "regime":      regime_weight,
            "ood":         ood_weight,
            "ensemble":    ensemble_weight,
            "uncertainty": uncertainty_weight,
            "stress":      stress_weight,
        }

    # ------------------------------------------------------------------
    # Main evaluation
    # ------------------------------------------------------------------

    def evaluate(
        self,
        bar:         pd.Series | dict,
        X:           Optional[np.ndarray] = None,
        predictions: Optional[dict] = None,
        regime:      Optional[str] = None,
        uncertainty: Optional[dict] = None,
        side:        str = "long",
    ) -> SuppressionResult:
        """
        Évalue si le trade doit être supprimé.

        Args:
            bar         : features de la barre courante
            X           : array features pour OOD + ensemble (peut être None)
            predictions : dict {'p_long': float, 'p10': float, 'p90': float}
            regime      : RegimeState (str) actuel
            uncertainty : dict depuis uncertainty_gate.py (optionnel)
            side        : côté du trade ("long" | "short")
        """
        penalties  = {}
        reasons    = []

        # Hard rule: PANIC always blocks longs
        if regime == "PANIC" and side == "long":
            return SuppressionResult(
                allow=False, size_multiplier=0.0,
                score=1.0, reasons=["regime_PANIC_blocks_longs"], level="BLOCKED"
            )

        # 1. Régime dangereux
        regime_pen, regime_reasons = self._regime_penalty(regime, side)
        penalties["regime"] = regime_pen
        reasons.extend(regime_reasons)

        # 2. OOD detection
        if self._ood is not None and X is not None:
            ood_pen, ood_reasons = self._ood_penalty(X)
        else:
            ood_pen, ood_reasons = 0.0, []
        penalties["ood"] = ood_pen
        reasons.extend(ood_reasons)

        # 3. Ensemble disagreement
        if self._ensemble is not None and X is not None and self._ensemble.n_models() >= 2:
            ens_pen, ens_reasons = self._ensemble_penalty(X)
        else:
            ens_pen, ens_reasons = 0.0, []
        penalties["ensemble"] = ens_pen
        reasons.extend(ens_reasons)

        # 4. Conformal uncertainty
        if uncertainty is not None:
            unc_pen, unc_reasons = self._uncertainty_penalty(uncertainty)
        elif predictions is not None:
            unc_pen, unc_reasons = self._pred_uncertainty_penalty(predictions)
        else:
            unc_pen, unc_reasons = 0.0, []
        penalties["uncertainty"] = unc_pen
        reasons.extend(unc_reasons)

        # 5. Bar-level stress signals (fires even without OOD/ensemble)
        stress_pen, stress_reasons = self._bar_stress_penalty(bar)
        penalties["stress"] = stress_pen
        reasons.extend(stress_reasons)

        # Score agrégé
        score = float(np.clip(
            sum(self._w.get(k, 0.10) * v for k, v in penalties.items()),
            0.0, 1.0
        ))

        # Décision
        if score >= self.BLOCKED_THR:
            return SuppressionResult(
                allow=False, size_multiplier=0.0,
                score=score, reasons=reasons, level="BLOCKED"
            )
        if score >= self.REDUCED_THR:
            size_mult = round(1.0 - (score - self.REDUCED_THR) / (self.BLOCKED_THR - self.REDUCED_THR), 3)
            return SuppressionResult(
                allow=True, size_multiplier=size_mult,
                score=score, reasons=reasons, level="REDUCED"
            )
        return SuppressionResult(
            allow=True, size_multiplier=1.0,
            score=score, reasons=reasons, level="ALLOWED"
        )

    # ------------------------------------------------------------------
    # Individual penalty components
    # ------------------------------------------------------------------

    def _regime_penalty(self, regime: Optional[str], side: str) -> tuple[float, list[str]]:
        if regime is None:
            return 0.0, []

        _REGIME_PENALTIES = {
            "PANIC":        {"long": 1.0,  "short": 0.50},
            "DELEVERAGING": {"long": 0.80, "short": 0.10},
            "SQUEEZE":      {"long": 0.30, "short": 0.80},
            "COMPRESSION":  {"long": 0.15, "short": 0.15},
            "RECOVERY":     {"long": 0.10, "short": 0.40},
            "EXPANSION":    {"long": 0.00, "short": 0.10},
            "UNKNOWN":      {"long": 0.50, "short": 0.50},
        }

        regime_map = _REGIME_PENALTIES.get(regime, {})
        pen = regime_map.get(side, 0.2)
        reasons = [f"regime_{regime.lower()}_{side}"] if pen > 0.2 else []
        return pen, reasons

    def _ood_penalty(self, X: np.ndarray) -> tuple[float, list[str]]:
        try:
            score  = self._ood.score(X)
            pct    = self._ood.percentile(score)
            is_ood = self._ood.is_ood(X)
            if is_ood:
                pen = min(0.5, (pct - 90) / 10 * 0.5) if pct > 90 else 0.3
                return pen, [f"ood_distance_p{pct:.0f}"]
        except Exception:
            pass
        return 0.0, []

    def _ensemble_penalty(self, X: np.ndarray) -> tuple[float, list[str]]:
        try:
            disag = self._ensemble.disagreement(X)
            thr   = self._ensemble._threshold
            if disag > thr:
                pen = min(0.5, (disag - thr) / thr * 0.5)
                return pen, [f"ensemble_disagree_{disag:.3f}"]
        except Exception:
            pass
        return 0.0, []

    def _uncertainty_penalty(self, uncertainty: dict) -> tuple[float, list[str]]:
        width = float(uncertainty.get("width", 0.0))
        thr   = float(uncertainty.get("threshold", 0.30))
        if width > thr:
            pen = min(0.4, (width - thr) / thr * 0.4)
            return pen, [f"conformal_width_{width:.3f}"]
        return 0.0, []

    def _bar_stress_penalty(self, bar: pd.Series | dict) -> tuple[float, list[str]]:
        """Penalty from raw bar features — fires even without OOD or ensemble."""
        if isinstance(bar, pd.Series):
            bar = bar.to_dict()
        pen = 0.0
        reasons = []

        rv_24  = float(bar.get("rv_24", 0.02))
        rv_72  = float(bar.get("rv_72", 0.02))
        rv_ratio = float(bar.get("rv_ratio_24_72", rv_24 / max(rv_72, 1e-6)))
        if rv_ratio > 2.0:
            # 0.0 at rv_ratio=2.0, capped at 0.70 at rv_ratio=4.0
            p = min(0.70, (rv_ratio - 2.0) / 2.0)
            pen += p
            reasons.append(f"vol_spike_rv_ratio_{rv_ratio:.2f}")

        funding_z = abs(float(bar.get("funding_rate_z_72", 0.0)))
        if funding_z > 2.5:
            # 0.0 at z=2.5, capped at 0.40 at z=5.0
            p = min(0.40, (funding_z - 2.5) / 2.5)
            pen += p
            reasons.append(f"funding_extreme_z{funding_z:.1f}")

        mom_72 = abs(float(bar.get("mom_logret_72", 0.0)))
        if mom_72 > 0.12:
            # 0.0 at |mom|=0.12, capped at 0.40 at |mom|=0.25
            p = min(0.40, (mom_72 - 0.12) / 0.13)
            pen += p
            reasons.append(f"momentum_extreme_{mom_72:.3f}")

        return min(pen, 1.0), reasons

    def _pred_uncertainty_penalty(self, predictions: dict) -> tuple[float, list[str]]:
        p10 = predictions.get("p10")
        p90 = predictions.get("p90")
        if p10 is not None and p90 is not None:
            width = abs(float(p90) - float(p10))
            return self._uncertainty_penalty({"width": width, "threshold": 0.30})
        return 0.0, []
