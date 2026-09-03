"""Runner de la famille CROSS-SECTIONNELLE (wave 2 : V3, V4, V5).

Couvre les candidats :
  XSEC_MOMENTUM_HORIZON_EXTENSION  (14D_LO primaire, 30D_LO, 14D_LS)   — V3
  XSEC_RESIDUAL_MOMENTUM_14D       (momentum résiduel beta-BTC)        — V3
  XSEC_RELATIVE_LEVERAGE_14D       (proxy levier OI/vol 30 j)          — V4
  SECTOR_ROTATION / SECTOR_RELATIVE_STRENGTH_REVERSAL                  — V5

Chaque signal est réimplémenté depuis sa DÉFINITION ÉCONOMIQUE (briefing §2) ; aucun
script ni evidence de découverte n'a été ouvert. La spec primaire, les perturbations
et les critères viennent des PREREGISTRATION.md, jamais d'un résultat déjà vu.

Usage : python run_xsec_family.py <candidate>[,<candidate>...]
"""
from __future__ import annotations

import os
import sys
import json

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import validation_lib as vl  # noqa: E402

SCRATCH = os.environ.get(
    "VAL_SCRATCH",
    "/tmp/claude-1000/-home-qbee-futur/96533575-ccfe-4d52-a4ae-a61df9219e6e/scratchpad/validation_wave2",
)
PANEL = os.path.join(SCRATCH, "daily_panel.parquet")
OUTDIR = "/home/qbee/futur/reports/edge_discovery/validation_2026-09"
BTC = "BTCUSDT"


# ═══════════════════════════════════════════════════════════════════════════
# Signaux (une fonction = une définition économique)
# ═══════════════════════════════════════════════════════════════════════════

def sig_momentum(n_days: int):
    """mom_N(d) = close_d / close_{d-N} − 1, jours calendaires, strictement causal."""
    def f(xp, d, elig):
        prev = d - pd.Timedelta(days=n_days)
        if prev not in xp.close.index:
            return pd.Series(np.nan, index=elig)
        return xp.close.loc[d, elig] / xp.close.loc[prev, elig] - 1.0
    return f


def sig_residual_momentum(form_days: int = 14, beta_days: int = 60, min_pairs: int = 40,
                          subtract_alpha: bool = False, vol_scaled: bool = False):
    """Momentum résiduel beta-BTC (Blitz-Huij-Martens 2011).

    beta_i(d) = pente OLS de r_i sur r_BTC sur [d−beta_days+1, d] (>= min_pairs paires)
    resid(d)  = Σ_{t=d−form_days+1..d} (r_it − [α_i] − β_i · r_BTC,t)

    Sans l'intercept par défaut : le soustraire ajoute un terme de retour à la moyenne
    à 60 j au signal (variante = perturbation P2 du prereg).
    """
    def f(xp, d, elig):
        r = getattr(xp, "logret", None)
        if r is None:
            r = np.log(xp.close).diff()
            xp.logret = r                      # mémoïsé : recalculer à chaque date coûte 100x
        w = r.loc[(r.index > d - pd.Timedelta(days=beta_days)) & (r.index <= d)]
        if len(w) < min_pairs or BTC not in w.columns:
            return pd.Series(np.nan, index=elig)
        b = w[BTC]
        valid = b.notna()
        b = b[valid]
        w = w.loc[b.index, elig]
        n_pairs = w.notna().sum()
        bc = b - b.mean()
        var_b = float((bc ** 2).sum())
        if var_b <= 0:
            return pd.Series(np.nan, index=elig)
        wc = w - w.mean()
        beta = wc.mul(bc, axis=0).sum() / var_b
        beta[n_pairs < min_pairs] = np.nan

        fw = r.loc[(r.index > d - pd.Timedelta(days=form_days)) & (r.index <= d)]
        if BTC not in fw.columns:
            return pd.Series(np.nan, index=elig)
        mom_i = fw[elig].sum(min_count=1)
        mom_b = float(fw[BTC].sum())
        resid = mom_i - beta * mom_b
        if subtract_alpha:
            alpha = w.mean() - beta * b.mean()          # intercept OLS
            resid = resid - alpha * len(fw)
        if vol_scaled:
            sd = (w - (bc.to_frame().values * beta.values)).std()
            resid = resid / sd.replace(0, np.nan)
        return resid
    return f


def sig_relative_leverage(oi_days: int = 30):
    """Proxy de levier relatif : OI notionnel / dollar-volume moyen sur `oi_days`.

    Réclamation V4 : « proxy levier OI/vol 30 j, rang -> 14 j fwd, long high / short low ».
    L'OI vient de binance_vision_metrics (chargé séparément) ; en son absence, le proxy
    dégradé documenté est dv/vol_realisee, qui N'EST PAS le même facteur -> le candidat
    est alors DATA_BLOCKED plutôt que testé sur un proxy différent.
    """
    def f(xp, d, elig):
        oi = getattr(xp, "oi_notional", None)
        if oi is None:
            return pd.Series(np.nan, index=elig)
        w_oi = oi.loc[(oi.index > d - pd.Timedelta(days=oi_days)) & (oi.index <= d), :]
        w_dv = xp.dv.loc[(xp.dv.index > d - pd.Timedelta(days=oi_days)) & (xp.dv.index <= d), :]
        cols = [c for c in elig if c in w_oi.columns]
        if not cols:
            return pd.Series(np.nan, index=elig)
        num = w_oi[cols].mean()
        den = w_dv[cols].mean().replace(0, np.nan)
        return (num / den).reindex(elig)
    return f


def sig_sector_rotation(sector_map: dict[str, str], form_days: int = 7):
    """SECTOR_ROTATION : rendement `form_days` du PANIER sectoriel (moyenne
    équipondérée des membres éligibles), continuation. Chaque nom hérite du score de
    son secteur -> le rang cross-sectionnel classe les secteurs, pas les noms."""
    def f(xp, d, elig):
        prev = d - pd.Timedelta(days=form_days)
        if prev not in xp.close.index:
            return pd.Series(np.nan, index=elig)
        ret = xp.close.loc[d, elig] / xp.close.loc[prev, elig] - 1.0
        sec = pd.Series({s: sector_map.get(s, "OTHER") for s in elig})
        basket = ret.groupby(sec).mean()
        return sec.map(basket)
    return f


def sig_sector_relative_strength(sector_map: dict[str, str], form_days: int = 7):
    """vs_sector_7d = rendement du nom − rendement de son panier sectoriel (REVERSAL :
    le rang est ensuite inversé par `descending=False`)."""
    def f(xp, d, elig):
        prev = d - pd.Timedelta(days=form_days)
        if prev not in xp.close.index:
            return pd.Series(np.nan, index=elig)
        ret = xp.close.loc[d, elig] / xp.close.loc[prev, elig] - 1.0
        sec = pd.Series({s: sector_map.get(s, "OTHER") for s in elig})
        return ret - sec.map(ret.groupby(sec).mean())
    return f


def sig_amihud(window: int = 30):
    """Illiquidité d'Amihud (2002) : moyenne de |r| / dollar-volume sur les `window`
    jours se terminant STRICTEMENT avant d — repris de la spec figée
    AMIHUD_ILLIQUIDITY_PREMIUM_V1 pour les contrôles de chevauchement."""
    def f(xp, d, elig):
        r = xp.close.pct_change().abs()
        illiq = r / xp.dv.replace(0, np.nan)
        w = illiq.loc[(illiq.index >= d - pd.Timedelta(days=window)) & (illiq.index < d), elig]
        m = w.mean()
        m[w.count() < 20] = np.nan     # >= 20 jours valides exigés, jamais d'imputation
        return m
    return f


# ═══════════════════════════════════════════════════════════════════════════
# Gate
# ═══════════════════════════════════════════════════════════════════════════

def gate_from_runs(runs: pd.DataFrame, *, column: str, n_legs: int,
                   minimum_calendar_days: int, exclude_years: list[int] | None = None,
                   cost_multiplier: float = 1.0) -> dict:
    """Applique le gate §3.1+§3.4 sur une colonne de rendement brut par période."""
    df = runs.copy()
    if exclude_years:
        df = df[~pd.to_datetime(df["date"]).dt.year.isin(exclude_years)]
    if df.empty:
        return {"error": "no periods"}
    nominal, stress = vl.cost_pair(n_legs)
    nominal *= cost_multiplier
    stress *= cost_multiplier
    l3 = vl.month_clusters(df["date"])
    out = vl.full_gate(
        df[column], dates=df["date"], l3=l3,
        cost_nominal=nominal, cost_stress=stress,
        l3_definition="mois calendaire de la date de rebalancement",
        minimum_calendar_days=minimum_calendar_days,
        n_raw=int(df["k"].sum()),
        n_l1=int(df["k"].sum()),
        n_l2=int(len(df)),
    )
    return out


def load_panel() -> tuple[vl.XSecPanel, pd.Series, list[str]]:
    raw = pd.read_parquet(PANEL)
    raw["date"] = pd.to_datetime(raw["date"])
    onboard, fallback = vl.load_onboard_ts(raw)
    panel = vl.add_causal_liquidity(raw, window=30)
    xp = vl.XSecPanel(panel, onboard)
    return xp, onboard, fallback


if __name__ == "__main__":
    xp, onboard, fallback = load_panel()
    print(f"panel: {len(xp.days)} days x {len(xp.symbols)} symbols, "
          f"{xp.days.min().date()}..{xp.days.max().date()}, onboard fallback={len(fallback)}")
    elig = xp.eligibility(1_000_000.0)
    n = elig.sum(axis=1)
    print(f"n_eligible: median={int(n.median())} min={int(n.min())} max={int(n.max())} "
          f"first>=20 = {n[n>=20].index[0].date()}")
