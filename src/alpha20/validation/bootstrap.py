"""
src/alpha20/validation/bootstrap.py — bootstrap RÉEL (pas l'approximation
normale de robust_allocator.lcb_annual) pour la borne basse 95 % exigée en
tête du classement de sélection (étape 7, critère 1), et le block-bootstrap
pour le test de robustesse (étape 7, tests de robustesse).

Déterministe (seed figée) — reproductible d'un run à l'autre sur les mêmes
données, jamais une nouvelle tentative tant que le run n'a pas changé.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

TRADING_DAYS = 365


def bootstrap_lcb95(daily_returns: pd.Series, n_boot: int = 2000,
                    seed: int = 42, min_obs: int = 30) -> Optional[float]:
    """5e percentile de la distribution bootstrap (i.i.d., avec remise) du
    rendement TOTAL annualisé — None si l'historique est insuffisant."""
    r = pd.Series(daily_returns).dropna().values
    if len(r) < min_obs:
        return None
    rng = np.random.RandomState(seed)
    n = len(r)
    samples = rng.choice(r, size=(n_boot, n), replace=True)
    total = np.prod(1 + samples, axis=1) - 1
    ann = (1 + total) ** (TRADING_DAYS / n) - 1
    return round(float(np.percentile(ann, 5)), 5)


def block_bootstrap_lcb95(daily_returns: pd.Series, block_len: int = 5,
                          n_boot: int = 1000, seed: int = 43,
                          min_obs: int = 30) -> Optional[float]:
    """Bootstrap en BLOCS contigus (préserve l'autocorrélation court terme) —
    test de robustesse, pas le critère de classement primaire."""
    r = pd.Series(daily_returns).dropna().values
    n = len(r)
    if n < max(min_obs, block_len * 4):
        return None
    rng = np.random.RandomState(seed)
    n_blocks = int(np.ceil(n / block_len))
    starts_max = n - block_len
    results = []
    for _ in range(n_boot):
        starts = rng.randint(0, starts_max + 1, size=n_blocks)
        blocks = [r[s:s + block_len] for s in starts]
        sample = np.concatenate(blocks)[:n]
        total = np.prod(1 + sample) - 1
        results.append((1 + total) ** (TRADING_DAYS / n) - 1)
    return round(float(np.percentile(results, 5)), 5)


def drop_top_n_events_return(event_pnl: pd.Series, n: int,
                             nav_start: float) -> Optional[float]:
    """Rendement total si l'on retire les n MEILLEURS événements — mesure la
    dépendance excessive à quelques événements (étape 7)."""
    ev = pd.Series(event_pnl).dropna()
    if len(ev) <= n:
        return None
    trimmed = ev.sort_values(ascending=False).iloc[n:]
    return round(float(trimmed.sum() / nav_start), 5)
