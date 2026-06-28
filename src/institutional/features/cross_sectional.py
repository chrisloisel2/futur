"""
src/institutional/features/cross_sectional.py
─────────────────────────────────────────────────────────────────────────────
Features cross-sectional — requiert un panel multi-actifs.

Ces features comparent chaque actif à l'univers à un instant T.
Causales : le rang est calculé uniquement sur les données disponibles à T.

Usage typique :
    panel = load_universe_panel(assets, start, end)
    cs_features = compute_cross_sectional_features(panel)
"""
from __future__ import annotations

from typing import List, Optional

import numpy as np
import pandas as pd


def cross_sectional_rank(
    values: pd.Series,
    ascending: bool = True,
    normalize: bool = True,
) -> pd.Series:
    """
    Rang cross-sectionnel d'une série dans son groupe timestamp.
    normalize=True → [0, 1] (percentile rang)
    """
    return values.groupby(level="timestamp").rank(ascending=ascending, pct=normalize)


def compute_cross_sectional_features(
    panel: pd.DataFrame,
    momentum_horizons: Optional[List[int]] = None,
    close_col: str = "close",
) -> pd.DataFrame:
    """
    Calcule les features cross-sectional sur un panel (MultiIndex asset/timestamp).

    Le panel doit avoir 'asset' comme colonne ou second niveau d'index.
    Retourne un DataFrame avec le même index que panel.

    Features calculées :
      - rank_mom_Xh   : rang cross-sectionnel du momentum à X heures
      - rank_vol_24h  : rang de la volatilité réalisée 24h
      - rank_funding  : rang du z-score funding
      - rank_strength : rang de la relative strength vs BTC

    Toutes les features sont causales : calculées en regroupant par timestamp.
    """
    if momentum_horizons is None:
        momentum_horizons = [24, 72, 168]

    # Le panel peut avoir un index MultiIndex (timestamp, asset) ou une colonne asset
    if isinstance(panel.index, pd.MultiIndex):
        df = panel.copy()
    else:
        # Assume panel a une colonne 'asset' et index timestamp
        df = panel.set_index(["asset"], append=True).swaplevel()
        df.index.names = ["asset", "timestamp"]

    out_parts = []

    for asset_name, asset_df in df.groupby(level="asset"):
        asset_df = asset_df.droplevel("asset")
        asset_out = pd.DataFrame(index=asset_df.index)
        asset_out["asset"] = asset_name
        out_parts.append(asset_out)

    # Approche vectorisée pour les rangs
    out = pd.DataFrame(index=panel.index)

    # Extraire close par actif (ou use existing column)
    if close_col not in panel.columns:
        return out

    close = panel[close_col]

    for h in momentum_horizons:
        mom_col = f"mom_{h}h"
        if mom_col in panel.columns:
            # Rang du momentum dans l'univers à chaque timestamp
            out[f"rank_{mom_col}"] = panel.groupby(level="timestamp")[mom_col].rank(pct=True)
        else:
            # Calcul à la volée
            mom = np.log(close / close.shift(h))
            out[f"rank_mom_{h}h"] = mom.groupby(level="timestamp").rank(pct=True)

    # Rang de la volatilité
    for vol_col in ["rv_24h", "rv_72h"]:
        if vol_col in panel.columns:
            out[f"rank_{vol_col}"] = panel.groupby(level="timestamp")[vol_col].rank(pct=True)

    # Rang du funding z-score
    if "funding_zscore" in panel.columns:
        out["rank_funding_zscore"] = (
            panel.groupby(level="timestamp")["funding_zscore"].rank(pct=True)
        )

    # Rang de l'OI change
    if "oi_change_24h" in panel.columns:
        out["rank_oi_change"] = (
            panel.groupby(level="timestamp")["oi_change_24h"].rank(pct=True)
        )

    # Score composite de "relative strength" (moyenne des rangs momentum)
    rank_mom_cols = [c for c in out.columns if c.startswith("rank_mom_")]
    if rank_mom_cols:
        out["composite_rank"] = out[rank_mom_cols].mean(axis=1)

    return out
