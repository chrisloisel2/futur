"""
src/institutional/live_alpha_lab/multiplicity.py
─────────────────────────────────────────────────────────────────────────────
DÉFLATION PAR LE NOMBRE D'ESSAIS — le seul transfert utile depuis
alpha_foundry_v5 vers le Live Alpha Lab.

Le problème
───────────
Les campagnes edge_discovery ont testé des CENTAINES de mécanismes. Deux
candidats en sont ressortis validés (LIQ_REPEAT_DENSITY 22,1 bps,
BTC_LEAD_ALT_CASCADE 46,87 bps). Leurs t-stats sont évalués contre le null
d'UN essai — alors qu'ils sont le maximum de centaines. Le bon point de
comparaison n'est pas « un t de 3,3 est-il grand ? » mais « un t de 3,3 est-il
grand POUR LE MEILLEUR DE N ESSAIS ? ».

Sans ce compte, un programme qui teste assez de mécanismes finit
mécaniquement par en produire un qui a l'air significatif. C'est la raison
d'être du ledger de multiplicité : les essais se comptent, ils ne se
racontent pas.

Ce que ce module calcule, et ce qu'il ne calcule pas
───────────────────────────────────────────────────
Il calcule la COMPOSANTE MULTIPLICITÉ du Deflated Sharpe Ratio : l'espérance
du maximum de N tirages du null (Bailey & López de Prado), retranchée du
t-stat observé.

Il ne calcule PAS le DSR complet, qui corrige aussi l'asymétrie et
l'aplatissement de la série de rendements. Ces moments demandent la série
elle-même ; le registre de validation ne conserve que des agrégats (moyenne,
IC bootstrap, n indépendant). Corriger la multiplicité sur ce qui est
disponible et le DIRE vaut mieux que ne rien corriger, et mieux qu'inventer
des moments qu'on n'a pas.

⚠ Le compte d'essais utilisé est une BORNE BASSE (voir
scripts/build_multiplicity_ledger.py : plusieurs workers n'exposent pas de
compte machine-lisible). Un compte sous-estimé produit un haircut
sous-estimé : les chiffres déflatés de ce module sont donc OPTIMISTES, et un
candidat qui ne survit pas déjà à cette borne basse ne survivra à rien.
"""
from __future__ import annotations

import math
from typing import Optional

EULER_MASCHERONI = 0.5772156649015329


def _norm_ppf(p: float) -> float:
    """Quantile de la loi normale centrée réduite (Acklam), sans scipy —
    scipy n'est pas une dépendance de ce paquet et l'introduire pour une
    fonction inverse serait disproportionné. Précision ~1e-9, largement
    au-delà de ce que la finesse des comptes d'essais justifie."""
    if not 0.0 < p < 1.0:
        raise ValueError(f"p doit être dans ]0,1[, reçu {p}")
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def expected_max_null_tstat(n_trials: int) -> float:
    """Espérance du maximum de `n_trials` tirages indépendants d'un null
    standard — formule de Bailey & López de Prado, celle-là même qui est au
    cœur du DSR :

        E[max] ≈ (1-γ)·Φ⁻¹(1 - 1/N) + γ·Φ⁻¹(1 - 1/(N·e))

    C'est le seuil qu'un candidat doit dépasser pour être autre chose que
    « le meilleur d'un grand nombre de tirages sans edge ».
    """
    if n_trials < 1:
        raise ValueError("n_trials doit valoir au moins 1")
    if n_trials == 1:
        return 0.0
    n = float(n_trials)
    return ((1 - EULER_MASCHERONI) * _norm_ppf(1 - 1 / n)
            + EULER_MASCHERONI * _norm_ppf(1 - 1 / (n * math.e)))


def deflate(observed_tstat: float, n_trials: int) -> dict:
    """Verdict de déflation pour un candidat.

    `p_deflated` est la probabilité que l'edge soit réel une fois le nombre
    d'essais pris en compte. Lu comme n'importe quelle p-value : au-dessus de
    0,95, le candidat survit à sa propre campagne.
    """
    threshold = expected_max_null_tstat(n_trials)
    deflated = observed_tstat - threshold
    return {
        "observed_tstat": round(float(observed_tstat), 4),
        "n_trials": int(n_trials),
        "expected_max_null_tstat": round(threshold, 4),
        "deflated_tstat": round(deflated, 4),
        "p_deflated": round(_norm_cdf(deflated), 4),
        "survives": bool(deflated > 0),
        "note": "composante MULTIPLICITÉ du DSR seulement — l'asymétrie et "
                "l'aplatissement de la série ne sont pas corrigés (le registre "
                "ne conserve que des agrégats). Compte d'essais = BORNE BASSE, "
                "donc haircut sous-estimé et verdict optimiste.",
    }


def tstat_from_two_sided_ci(mean: float, lo: float, hi: float,
                            confidence: float = 0.95) -> Optional[float]:
    """t-stat déduit d'un intervalle bootstrap bilatéral.

    Le registre de validation conserve des IC, pas toujours des t-stats.
    Recoupé sur le seul candidat qui porte les deux (BTC_LEAD_ALT_CASCADE :
    la dérivation donne 3,33 contre 3,315 déclaré), ce qui valide la méthode
    plutôt que de la supposer."""
    if hi <= lo:
        return None
    z = _norm_ppf(1 - (1 - confidence) / 2)
    se = (hi - lo) / (2 * z)
    return None if se <= 0 else float(mean) / se


def tstat_from_one_sided_bound(mean: float, bound: float,
                               tail: float = 0.05) -> Optional[float]:
    """t-stat déduit d'un percentile bootstrap unilatéral (ex. p05)."""
    se = (float(mean) - float(bound)) / _norm_ppf(1 - tail)
    return None if se <= 0 else float(mean) / se
