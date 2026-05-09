"""
risk/uncertainty_gate.py — FILTRE D'INCERTITUDE CONFORMAL SIMPLE
=================================================================

Couche entre le signal ML et la décision d'exécution.

Logique :
  1. Si le modèle sort p10/p90 → utilise l'intervalle conformal.
  2. Sinon → fallback sur la volatilité réalisée (approximate).

Règle : un intervalle trop large → taille zéro, pas de trade.

Intégration :
    from risk.uncertainty_gate import conformal_width, uncertainty_decision

    result = uncertainty_decision(
        edge=pred["p_long"] - 0.5,
        width=conformal_width(pred.get("p10", 0), pred.get("p90", 1)),
        width_threshold=0.30,
    )
    if not result["allow_trade"]:
        action = "WAIT"
    else:
        size_multiplier = result["size_multiplier"]
"""
from __future__ import annotations


def conformal_width(pred_p10: float, pred_p90: float) -> float:
    """Largeur de l'intervalle conformal [p10, p90]."""
    return abs(pred_p90 - pred_p10)


def uncertainty_from_volatility(rv_24: float) -> tuple[float, str]:
    """
    Fallback quand p10/p90 ne sont pas disponibles.

    Mappe la volatilité réalisée 24h sur un indicateur d'incertitude approximatif.
    rv_24 : réalised vol 24h en fraction (ex. 0.05 = 5%).

    Retourne (width_approx, "approximate").
    """
    width_approx = min(1.0, rv_24 * 6.0)
    return width_approx, "approximate"


def uncertainty_decision(
    edge: float,
    width: float,
    width_threshold: float = 0.30,
) -> dict:
    """
    Décision basée sur la largeur de l'intervalle d'incertitude.

    Args:
        edge            : signal brut (p_long - 0.5, positif = haussier)
        width           : largeur de l'intervalle (conformal ou approximate)
        width_threshold : seuil maximum d'incertitude acceptable

    Returns:
        dict avec :
          allow_trade     : bool
          reason          : str
          size_multiplier : float  (0.0 / 0.25 / 1.0)
    """
    if width > width_threshold:
        return {
            "allow_trade":     False,
            "reason":          "conformal_interval_too_wide",
            "size_multiplier": 0.0,
            "width":           round(width, 4),
            "threshold":       width_threshold,
            "status":          "high",
        }

    if width > width_threshold * 0.7:
        return {
            "allow_trade":     True,
            "reason":          "medium_uncertainty_reduced_size",
            "size_multiplier": 0.25,
            "width":           round(width, 4),
            "threshold":       width_threshold,
            "status":          "medium",
        }

    return {
        "allow_trade":     True,
        "reason":          "low_uncertainty",
        "size_multiplier": 1.0,
        "width":           round(width, 4),
        "threshold":       width_threshold,
        "status":          "low",
    }


def gate_signal(pred: dict, width_threshold: float = 0.30) -> dict:
    """
    Point d'entrée haut niveau : enrichit le signal avec l'info d'incertitude.

    Args:
        pred            : dict de prédiction (sortie du PredictionEngine)
        width_threshold : seuil conformal

    Returns:
        pred enrichi avec champ "uncertainty"
    """
    p10 = pred.get("p10")
    p90 = pred.get("p90")

    if p10 is not None and p90 is not None:
        width  = conformal_width(float(p10), float(p90))
        source = "conformal"
    else:
        rv_24  = pred.get("rv_24", pred.get("rv_60", 0.03))
        width, source = uncertainty_from_volatility(float(rv_24))

    edge   = float(pred.get("p_long", 0.5)) - 0.5
    result = uncertainty_decision(edge=edge, width=width, width_threshold=width_threshold)
    result["source"] = source

    return {**pred, "uncertainty": result}
