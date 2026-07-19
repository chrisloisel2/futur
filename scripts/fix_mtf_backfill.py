#!/usr/bin/env python3
"""
scripts/fix_mtf_backfill.py
============================
Backfill les 13 features MTF manquantes (mtf_4h_*, mtf_1d_*) dans les parquets
enrichis pour toutes les barres post-2025 (les nouvelles barres live manquent ces
features car _add_mtf_features produisait de mauvais noms de colonnes).

Usage :
  python3 scripts/fix_mtf_backfill.py
  python3 scripts/fix_mtf_backfill.py --symbols BTCUSDT ETHUSDT
  python3 scripts/fix_mtf_backfill.py --dry-run
"""
from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Import helpers depuis live_data_update (pour ne pas dupliquer les fonctions MTF)
from live_data_update import _add_mtf_features

ENRICHED_DIR = ROOT / "data" / "enriched"

TOP_10 = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT",
    "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT",
    "DOTUSDT", "LINKUSDT",
]

MTF_COLS = [
    "mtf_4h_adx_20", "mtf_4h_adx_10", "mtf_4h_ema_distance_20", "mtf_4h_rsi_10",
    "mtf_4h_return_5", "mtf_4h_donchian_position_20", "mtf_4h_realized_vol_10",
    "mtf_1d_return_5", "mtf_1d_adx_5", "mtf_1d_ema_distance_5", "mtf_1d_rsi_5",
    "mtf_1d_donchian_position_5", "mtf_1d_realized_vol_5",
]

# Barres de contexte pour les fenêtres ADX/RSI (ADX(20) sur 4h × 20 = 80 barres 1h)
# On utilise 600 barres pour être sûr
N_CONTEXT = 600


def fix_mtf_for_symbol(symbol: str, dry_run: bool = False) -> int:
    path = ENRICHED_DIR / f"{symbol}_1h_enriched.parquet"
    if not path.exists():
        print(f"  [{symbol}] parquet absent — skip")
        return 0

    df = pd.read_parquet(path)
    df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
    df = df.sort_values("datetime").reset_index(drop=True)

    # Vérifier le taux de NaN sur les colonnes MTF dans les barres récentes
    broken_cols = []
    for col in MTF_COLS:
        if col in df.columns:
            nan_pct_recent = df.tail(100)[col].isna().mean()
            if nan_pct_recent > 0.5:
                broken_cols.append(col)
        else:
            broken_cols.append(col)

    if not broken_cols:
        print(f"  [{symbol}] MTF OK — aucune correction nécessaire")
        return 0

    print(f"  [{symbol}] {len(broken_cols)} colonnes MTF à corriger: {broken_cols[:3]}...")

    # Recomputer les MTF sur le parquet complet (besoin de contexte)
    # On prend seulement les colonnes OHLCV pour le calcul
    df_ohlcv = df[["datetime", "open", "high", "low", "close"]].copy()
    df_ohlcv_with_mtf = _add_mtf_features(df_ohlcv)

    # Extraire les nouvelles colonnes MTF calculées
    new_mtf_cols = [c for c in df_ohlcv_with_mtf.columns if c.startswith("mtf_")]

    if not new_mtf_cols:
        print(f"  [{symbol}] ERREUR: _add_mtf_features n'a produit aucune colonne MTF")
        return 0

    # Vérifier la qualité des nouvelles valeurs
    sample_col = new_mtf_cols[0]
    fill_rate = 1 - df_ohlcv_with_mtf[sample_col].isna().mean()
    tail_fill = 1 - df_ohlcv_with_mtf.tail(100)[sample_col].isna().mean()
    print(f"  [{symbol}] {sample_col}: fill_global={fill_rate:.1%}  fill_recent={tail_fill:.1%}")

    if tail_fill < 0.8:
        print(f"  [{symbol}] WARN: fill récent insuffisant — vérifier le contexte OHLCV")

    if dry_run:
        print(f"  [{symbol}] [DRY-RUN] corrections calculées — écriture skippée")
        return len(new_mtf_cols)

    # Écrire les nouvelles valeurs dans le parquet
    for col in new_mtf_cols:
        df[col] = df_ohlcv_with_mtf[col].values

    df.to_parquet(path, index=False)
    print(f"  [{symbol}] parquet mis à jour — {len(new_mtf_cols)} colonnes MTF corrigées")
    return len(new_mtf_cols)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=TOP_10)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print("=== fix_mtf_backfill ===\n")
    total = 0
    for sym in args.symbols:
        n = fix_mtf_for_symbol(sym, dry_run=args.dry_run)
        total += n

    print(f"\nTotal: {total} colonnes MTF corrigées pour {len(args.symbols)} symboles")


if __name__ == "__main__":
    main()
