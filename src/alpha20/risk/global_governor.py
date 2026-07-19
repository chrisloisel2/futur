"""
src/alpha20/risk/global_governor.py — governor UNIFIÉ (étape 4).

Un seul jeu de seuils pour tout le système (profil ALPHA20_LOW_RISK de
configs/alpha20.yaml) — remplace la coexistence 15 % (risk.yaml) / 3 %
(governor conservateur). Ordre d'évaluation : kill > cash > reduced, puis
limites journalière/hebdo/ES/delta/marge/venue qui déclassent d'un cran.

Pur et sans I/O : `evaluate(metrics)` reçoit des mesures, retourne une
GovernorDecision — le branchement live est un adaptateur.
"""
from __future__ import annotations

from typing import Dict

from src.alpha20 import load_config
from src.alpha20.contracts import GovernorDecision, RiskProfile

SCALES = {"risk_on": 1.0, "risk_reduced": 0.5, "cash": 0.0, "kill": 0.0}
_ORDER = ["risk_on", "risk_reduced", "cash", "kill"]


def load_profile(name: str = None) -> RiskProfile:
    cfg = load_config()["risk"]
    pname = name or cfg["profile"]
    return RiskProfile(name=pname, **{k: v for k, v in cfg[pname].items()})


def evaluate(metrics: Dict, profile: RiskProfile = None) -> GovernorDecision:
    """metrics attendues (fractions de NAV, positives = pertes/utilisations) :
    drawdown, daily_loss, weekly_loss, es99_1d, net_delta, margin_used,
    venue_unsecured_max, naked_leg_age_s."""
    p = profile or load_profile()
    reasons: Dict = {}
    dd = float(metrics.get("drawdown", 0.0))
    if dd >= p.dd_kill:
        state = "kill"; reasons["dd_kill"] = dd
    elif dd >= p.dd_cash:
        state = "cash"; reasons["dd_cash"] = dd
    elif dd >= p.dd_reduce:
        state = "risk_reduced"; reasons["dd_reduce"] = dd
    else:
        state = "risk_on"

    checks = [("daily_loss", p.daily_loss), ("weekly_loss", p.weekly_loss),
              ("es99_1d", p.es99_1d), ("net_delta", p.net_delta_cap),
              ("margin_used", p.margin_used_cap),
              ("venue_unsecured_max", p.venue_unsecured_cap)]
    breach = False
    for key, cap in checks:
        v = abs(float(metrics.get(key, 0.0)))
        if v > cap:
            reasons[key] = v
            breach = True
    if float(metrics.get("naked_leg_age_s", 0.0)) > p.naked_leg_max_s:
        reasons["naked_leg_age_s"] = float(metrics["naked_leg_age_s"])
        breach = True
    if breach and state != "kill":
        state = _ORDER[min(_ORDER.index(state) + 1, len(_ORDER) - 2)]  # max cash
    return GovernorDecision(state=state, scale=SCALES[state], reasons=reasons)
