"""
research/edge_factory/cross_sectional_momentum_v1/momentum_engine.py
─────────────────────────────────────────────────────────────────────────────
Fonctions pures, testables indépendamment de toute donnée réelle — extraites
de backtest_momentum_crypto_v1.py suite à l'audit du 2026-07-21
(QUARANTINE_2026-07-21.md) pour permettre des tests de symétrie de signe et
d'identité comptable sans dépendre de qbee.

Convention d'exécution (corrigée le 2026-07-21, voir le commit "fix(backtest):
align close signal with actual next-open execution") : signal connu à la
clôture du jour t -> exécuté à l'ouverture de t+1 -> rendement capté entre
l'ouverture de t+1 et l'ouverture de t+2. `EXEC_DELAY_DAYS = 2` dans
`backtest_momentum_crypto_v1.py` implémente ce décalage via
`signed_w.shift(2)` appliqué à un rendement open-to-open (jamais close-to-close
appliqué avec un simple décalage d'un jour, qui n'aurait laissé aucune marge
réelle entre la connaissance du signal et l'exécution).
"""
from __future__ import annotations

from typing import Dict, Tuple

import numpy as np
import pandas as pd


def compute_weights(score: pd.DataFrame, vol: pd.DataFrame, long_short_frac: float,
                    max_weight_per_name: float
                    ) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Poids signés long-short (long positif, short négatif), SANS hedge.
    Long = score le plus élevé (top `long_short_frac`), short = le plus bas.
    Pondération inverse-vol, cap par nom SANS violation (voir normalize_capped).

    Retourne (signed_w, is_long, is_short) — les masques sont exposés pour
    permettre les tests d'invariants (nombre de longs/shorts, etc).
    """
    n_names = score.notna().sum(axis=1)
    n_per_leg = (n_names * long_short_frac).apply(np.floor).clip(lower=1)

    rank_desc = score.rank(axis=1, ascending=False, method="first")
    rank_asc = score.rank(axis=1, ascending=True, method="first")
    is_long = rank_desc.le(n_per_leg, axis=0) & score.notna()
    is_short = rank_asc.le(n_per_leg, axis=0) & score.notna()

    inv_vol = 1.0 / vol.clip(lower=1e-6)
    long_w = normalize_capped(inv_vol.where(is_long, 0.0), max_weight_per_name)
    short_w = normalize_capped(inv_vol.where(is_short, 0.0), max_weight_per_name)
    return long_w - short_w, is_long, is_short


def _water_fill_row(weights: np.ndarray, cap: float) -> np.ndarray:
    """Répartition proportionnelle sous contrainte de cap, maximisant la
    somme atteinte (water-filling) : redistribue itérativement le budget
    libéré par les noms plafonnés vers les noms pas encore plafonnés,
    jusqu'à convergence. Ne dépasse jamais `cap` par construction."""
    w = np.asarray(weights, dtype=float).copy()
    active = w > 0
    result = np.zeros_like(w)
    if not active.any():
        return result
    budget = 1.0
    for _ in range(active.sum() + 1):
        if not active.any() or budget <= 1e-15:
            break
        sub_sum = w[active].sum()
        if sub_sum <= 0:
            break
        scale = budget / sub_sum
        candidate = w * scale
        newly_capped = active & (candidate > cap)
        if not newly_capped.any():
            result[active] = candidate[active]
            budget = 0.0
            break
        result[newly_capped] = cap
        budget -= cap * newly_capped.sum()
        active = active & ~newly_capped
    return np.minimum(result, cap)


def normalize_capped(raw_w: pd.DataFrame, cap: float) -> pd.DataFrame:
    """Normalise chaque ligne à somme <= 1 par water-filling : le cap par nom
    n'est JAMAIS dépassé, et la somme atteint exactement 1 dès que c'est
    mathématiquement possible (n_noms_actifs * cap >= 1) — pas seulement
    "au moins un nom plafonné => on abandonne le reste du budget", ce que
    faisait la version précédente (un simple clip sans redistribution),
    trouvé insuffisant par l'audit d'invariants du 2026-07-21 : sur 765/2373
    jours, les deux jambes finissaient à des sommes différentes (jusqu'à 29
    points de neutralité dollar perdue) alors que le budget aurait pu être
    réparti plus loin sans violer le cap.
    """
    return raw_w.fillna(0.0).clip(lower=0.0).apply(
        lambda row: pd.Series(_water_fill_row(row.to_numpy(), cap), index=row.index), axis=1)


def compute_btc_hedge(signed_w: pd.DataFrame, beta_ex_btc: pd.DataFrame) -> pd.Series:
    """Position BTC (positive=long, négative=short) pour ramener le bêta net
    du portefeuille (hors hedge) à ~0."""
    return -(signed_w * beta_ex_btc).sum(axis=1)


def portfolio_returns(signed_w: pd.DataFrame, btc_hedge_w: pd.Series,
                      open_prices: pd.DataFrame, funding: pd.DataFrame,
                      crypto_cols: list, btc_col: str, exec_delay: int
                      ) -> Dict[str, object]:
    """Rendements bruts/funding/turnover, exécution décalée de `exec_delay`
    jours, sur rendements OPEN-TO-OPEN (jamais close-to-close)."""
    open_ret = open_prices.pct_change()
    w_lag = signed_w.shift(exec_delay).fillna(0.0)
    hedge_lag = btc_hedge_w.shift(exec_delay).fillna(0.0)

    asset_gross = w_lag * open_ret[crypto_cols]
    asset_funding = -w_lag * funding[crypto_cols]
    btc_hedge_gross = hedge_lag * open_ret[btc_col]
    btc_hedge_funding = -hedge_lag * funding[btc_col]

    gross_ret = asset_gross.sum(axis=1) + btc_hedge_gross
    funding_pnl = asset_funding.sum(axis=1) + btc_hedge_funding
    turnover = ((signed_w.fillna(0) - signed_w.shift(1).fillna(0)).abs().sum(axis=1)
               + (btc_hedge_w.fillna(0) - btc_hedge_w.shift(1).fillna(0)).abs())

    return dict(gross_ret=gross_ret, funding_pnl=funding_pnl, turnover=turnover,
               asset_gross=asset_gross, asset_funding=asset_funding,
               btc_hedge_gross=btc_hedge_gross, btc_hedge_funding=btc_hedge_funding,
               w_lag=w_lag, hedge_lag=hedge_lag)


def check_daily_invariants(signed_w: pd.DataFrame, btc_hedge_w: pd.Series,
                           beta_ex_btc: pd.DataFrame, max_weight_per_name: float,
                           net_returns: pd.Series) -> Dict[str, int]:
    """Retourne un dict {nom_violation: nombre_de_jours_violés} — vide si
    tous les invariants tiennent sur toute la période."""
    violations: Dict[str, int] = {}

    gross = signed_w.abs().sum(axis=1)
    bad = gross > 2.0 + 1e-6
    if bad.any():
        violations["gross_leg_exceeds_2"] = int(bad.sum())

    max_abs_w = signed_w.abs().max(axis=1)
    bad = max_abs_w > max_weight_per_name + 1e-9
    if bad.any():
        violations["per_name_cap_violated"] = int(bad.sum())

    net_signed_sum = signed_w.sum(axis=1)
    bad = net_signed_sum.abs() > 1e-6
    if bad.any():
        violations["long_short_not_dollar_neutral"] = int(bad.sum())

    port_beta = (signed_w * beta_ex_btc).sum(axis=1) + btc_hedge_w
    bad = port_beta.abs() > 0.05
    if bad.any():
        violations["portfolio_beta_not_neutral_gt_0.05"] = int(bad.sum())

    bad = net_returns <= -1.0
    if bad.any():
        violations["daily_return_leq_minus_100pct"] = int(bad.sum())

    return violations
