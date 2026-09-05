#!/usr/bin/env python
"""T3 — Ecran ETA uniforme applique aux 5 candidats REJETES (cible 3).

Audit documentaire : aucune reimplementation, aucun re-backtest. On applique la
MEME arithmetique de puissance a des chiffres deja publies (par la decouverte ou
par la validation), pour repondre a une question unique et prealable a toute
reformulation : *une expression quelconque de ce mecanisme peut-elle etre
confirmee forward en moins de 3 ans ?*

Arithmetique (conventions du briefing §2, identiques a gate.py) :
  n_required = (z_alpha + z_beta)^2 * sigma^2 / (0.5*mu)^2   [haircut 50%]
  or  t = mu / (sigma/sqrt(N))  =>  sigma^2 = N * mu^2 / t^2
  d'ou  n_required = (z_a+z_b)^2 / 0.25 * N / t^2 = 24.74 * N / t^2

L'interet : n_required ne depend QUE de (N_independant, t) — les deux chiffres
que toute decouverte publie deja. L'ecran est donc applicable A LA DECOUVERTE,
avant de depenser un worker de validation.
"""
import json
from pathlib import Path

Z_ALPHA, Z_POWER, HAIRCUT = 1.6449, 0.8416, 0.5
K = (Z_ALPHA + Z_POWER) ** 2 / HAIRCUT ** 2      # 24.74
OUT = Path(__file__).resolve().parent


def eta(n_indep, t, per_year, label, source):
    if not t or t == 0:
        return {"label": label, "source": source, "n_independent": n_indep,
                "t": t, "status": "t=0 -> n_required infini"}
    n_req = K * n_indep / (t ** 2)
    return {"label": label, "source": source,
            "n_independent": n_indep, "t_stat": t,
            "independent_episodes_per_year": per_year,
            "n_required_haircut50_power80": int(round(n_req)),
            "eta_years": round(n_req / per_year, 1),
            "unconfirmable_in_3y": bool(n_req / per_year > 3.0)}


ROWS = [
    # ── ce que la DECOUVERTE elle-meme publiait (N_indep, t) ──
    eta(224, 2.16, 37.3, "POSITIONING_TAKER_FLOW — grille 7D de la DECOUVERTE (+51.9bps)",
        "decouverte round3 W3, D-TAKER_LSR-mom-7D (N_indep=224 sur ~6 ans)"),
    eta(239, 2.05, 39.8, "GLOBAL_ACCOUNT_LSR_FADE — grille 7D de la DECOUVERTE (+51.5bps)",
        "decouverte round3 W3, D-GLOBAL_LSR-fade-7D (N_indep=239 sur ~6 ans)"),
    eta(157, 1.94, 20.9, "BTC_ETH_CURVE_STEEPNESS — DECOUVERTE (+77.8bps)",
        "decouverte round3 W3, D-CURVESHAPE-BTCvsETH-PAIR-7D (N=157)"),
    # ── ce que la VALIDATION a mesure ──
    eta(333, 0.22, 52.0, "CROSS_SECTIONAL_MOMENTUM_CVD — cote CONFIRMED (tradable), validation",
        "validation 2026-09, single-anchor +4.5bps"),
    eta(330, -1.58, 52.0, "CROSS_SECTIONAL_MOMENTUM_CVD — cote DIVERGENT (filtre), validation",
        "validation 2026-09, single-anchor -61.4bps"),
    eta(57, 0.28, 20.9, "BTC_ETH_CURVE_STEEPNESS — PRIMARY_SPEC, validation",
        "validation 2026-09, +17.1bps"),
    eta(61, 2.35, 20.9, "BTC_ETH_CURVE_STEEPNESS — piste 'ETH single-asset' signalee par la validation",
        "validation 2026-09, +259.6bps, 4/4 annees — MECANISME DIFFERENT, ecran applique pour le clore"),
    eta(63, 0.64, 478.0, "GLOBAL_ACCOUNT_LSR_FADE — miroir momentum (piste signalee, non adoptee)",
        "validation 2026-09, +19.6bps, t day-clustered 0.64, 63 clusters systemiques en 48j"),
    # ── reference : ce qui a ete VALIDE dans le projet, pour calibrer l'ecran ──
    eta(332, 2.92, 52.4, "[REFERENCE] AMIHUD_ILLIQUIDITY_PREMIUM_V1 — FROZEN",
        "registre : validation +105.7bps, ETA publie ~17 ans"),
    eta(789, 2.75, 233.9, "[REFERENCE] LIQ_CASCADE_REPEAT exhaustion thr3 — alpha en shadow",
        "mesure de ce rapport (t1c), ETA publie 11.0 ans"),
]


def main():
    out = {"formula": "n_required = (z_a+z_b)^2/haircut^2 * N_indep / t^2 = %.2f * N / t^2" % K,
           "conventions": {"alpha": 0.05, "side": "unilateral", "power": 0.80, "haircut": 0.5},
           "why_it_matters": ("n_required ne depend que de (N_independant, t), les deux chiffres "
                              "que toute decouverte publie deja -> l'ecran ETA est applicable AVANT "
                              "de depenser un worker de validation."),
           "rows": ROWS}
    (OUT / "t3_eta_screen.json").write_text(json.dumps(out, indent=2))
    print("%-78s %8s %7s %9s %9s" % ("candidat / expression", "N_indep", "t", "n_req", "ETA(ans)"))
    print("-" * 116)
    for r in ROWS:
        print("%-78s %8s %7s %9s %9s%s"
              % (r["label"][:78], r["n_independent"], r["t_stat"],
                 r.get("n_required_haircut50_power80"), r.get("eta_years"),
                 "  <3ans OK" if not r.get("unconfirmable_in_3y", True) else ""))


if __name__ == "__main__":
    main()
