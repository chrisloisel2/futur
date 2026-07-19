#!/usr/bin/env python3
"""
scripts/rebuild_enriched_from_origin.py
─────────────────────────────────────────────────────────────────────────────
Wrapper MINIMAL de reconstruction enriched (Phase 23.6).

Réutilise le chargement offline de l'assembleur existant
(`scripts/assemble_enriched_from_dataout.load_symbol_1h`, depuis
data_out/result/{year}_{SYM}_features.parquet) MAIS active les flags canoniques
(MTF + sequence) pour matcher le schéma des fichiers valides (réf : ADAUSDT).

⚠️ N'INVENTE AUCUNE FEATURE : appelle uniquement les fonctions existantes
(`compute_enriched_ohlcv_features`, `compute_label_columns`). Aucune donnée
synthétique, aucun forward-fill de fichier corrompu, aucun téléchargement.

Refuse un actif sans source raw (ex. DOT) → l'actif sort de l'univers.

Usage :
    python3 scripts/rebuild_enriched_from_origin.py --asset BNBUSDT
    python3 scripts/rebuild_enriched_from_origin.py --asset AVAXUSDT --asset LINKUSDT
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.settings import configure_project_imports
configure_project_imports()

from data_pipeline.enriched_ohlcv_features import compute_enriched_ohlcv_features
from ai.level_0.labels import compute_label_columns
from scripts.assemble_enriched_from_dataout import (
    load_symbol_1h, _apply_feature_aliases, MACRO_COLS, OHLCV_AGG, DATA_IN_DIR, DATA_OUT_DIR,
)


def has_raw(symbol: str) -> bool:
    files = [f for f in DATA_IN_DIR.glob(f"*_{symbol}_features.parquet")
             if not f.stem.split("_")[1].islower()]
    return len(files) > 0


def rebuild(symbol: str) -> bool:
    if not has_raw(symbol):
        print(f"  [DROP] {symbol} : aucune source raw dans {DATA_IN_DIR} → retiré de l'univers")
        return False

    df_1h = load_symbol_1h(symbol)
    if df_1h is None or df_1h.empty:
        print(f"  [DROP] {symbol} : load_symbol_1h vide")
        return False

    macro_present = [c for c in MACRO_COLS if c in df_1h.columns]
    df_macro = df_1h[macro_present].copy() if macro_present else None

    ohlcv_cols = [c for c in OHLCV_AGG if c in df_1h.columns]
    df_in = df_1h[ohlcv_cols].copy()
    df_in.index.name = "datetime"

    print(f"  Features enrichies (interval=1h, MTF=ON, sequence=ON) ...")
    df_enriched = compute_enriched_ohlcv_features(
        df_in,
        interval="1h",
        include_labels=False,
        include_multi_timeframe=True,    # ← canonique (match ADA)
        include_sequence_features=True,  # ← canonique
    )

    if df_macro is not None:
        for col in macro_present:
            if col not in df_enriched.columns:
                df_enriched[col] = df_macro[col]

    df_enriched = _apply_feature_aliases(df_enriched)
    df_enriched.index.name = "datetime"
    df_enriched = df_enriched.reset_index()
    df_enriched["datetime"] = pd.to_datetime(df_enriched["datetime"], utc=True)
    try:
        df_enriched = compute_label_columns(df_enriched)
    except Exception as e:
        print(f"  [WARN] compute_label_columns: {e}")

    out = DATA_OUT_DIR / f"{symbol}_1h_enriched.parquet"
    df_enriched.to_parquet(out, index=False)
    print(f"  [OK] {symbol} : {len(df_enriched):,} barres × {len(df_enriched.columns)} cols → {out}")
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--asset", action="append", required=True, help="répétable")
    args = ap.parse_args()
    ok = {a: rebuild(a) for a in args.asset}
    print("\nRésumé :", {a: ("OK" if v else "DROP") for a, v in ok.items()})
    if not all(ok.values()):
        sys.exit(2)  # au moins un actif sans raw


if __name__ == "__main__":
    main()
