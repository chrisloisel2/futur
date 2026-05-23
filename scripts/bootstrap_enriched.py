#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/bootstrap_enriched.py — Création initiale d'un parquet enrichi depuis Binance
=======================================================================================

Contrairement à live_data_update.py (qui appende), ce script crée le parquet
depuis zéro en fetchant toute l'histoire disponible sur Binance (depuis 2018-01-01).

Usage :
  python3 scripts/bootstrap_enriched.py --symbols XRPUSDT DOGEUSDT DOTUSDT LINKUSDT
  python3 scripts/bootstrap_enriched.py --symbols BTCUSDT --since 2020-01-01
  python3 scripts/bootstrap_enriched.py --dry-run --symbols XRPUSDT
"""
from __future__ import annotations

import argparse
import sys
import warnings
from pathlib import Path
from typing import List

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.settings import configure_project_imports
configure_project_imports()

from data_pipeline.enriched_ohlcv_features import compute_enriched_ohlcv_features
from ai.level_0.labels import compute_label_columns
from scripts.live_data_update import (
    fetch_binance_1h,
    _apply_feature_aliases,
    _add_mtf_features,
)

ENRICHED_DIR = ROOT / "data" / "enriched"
ENRICHED_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_SINCE = "2018-01-01"
CHUNK_SIZE    = 5000   # barres par batch de features (gestion mémoire)


def bootstrap_symbol(symbol: str, since: str = DEFAULT_SINCE, dry_run: bool = False) -> int:
    path = ENRICHED_DIR / f"{symbol}_1h_enriched.parquet"

    if path.exists():
        print(f"  [{symbol}] parquet existe déjà ({path.stat().st_size // 1024}KB) — skip.")
        print(f"           Utiliser live_data_update.py pour mettre à jour.")
        return 0

    since_ms = int(pd.Timestamp(since, tz="UTC").timestamp() * 1000)
    print(f"  [{symbol}] Fetch Binance depuis {since}…")

    df_raw = fetch_binance_1h(symbol, since_ms)
    if df_raw.empty:
        print(f"  [{symbol}] Aucune donnée Binance.")
        return 0

    n_raw = len(df_raw)
    print(f"  [{symbol}] {n_raw:,} barres brutes "
          f"({df_raw['datetime'].iloc[0].date()} → {df_raw['datetime'].iloc[-1].date()})")

    if dry_run:
        print(f"  [{symbol}] [DRY-RUN] skip écriture.")
        return n_raw

    # ── Calcul features ───────────────────────────────────────────────────────
    df_ohlcv = df_raw.set_index("datetime")[
        [c for c in ("open", "high", "low", "close", "volume",
                     "number_of_trades", "taker_buy_base_asset_volume")
         if c in df_raw.columns]
    ].copy()
    df_ohlcv.index.name = "datetime"

    print(f"  [{symbol}] Calcul features enrichies…")
    df_enriched = compute_enriched_ohlcv_features(
        df_ohlcv,
        interval="1h",
        include_labels=False,
        include_multi_timeframe=False,
        include_sequence_features=False,
    )
    df_enriched = df_enriched.reset_index()
    df_enriched["datetime"] = pd.to_datetime(df_enriched["datetime"], utc=True)

    df_enriched = _apply_feature_aliases(df_enriched)
    df_enriched = _add_mtf_features(df_enriched)

    try:
        df_enriched = compute_label_columns(df_enriched)
    except Exception:
        pass

    # Réintégrer close/Close si absent
    for src, dst in (("close", "close"), ("close", "Close")):
        if dst not in df_enriched.columns and src in df_raw.columns:
            merged = df_enriched.merge(
                df_raw[["datetime", src]].rename(columns={src: dst}),
                on="datetime", how="left",
            )
            df_enriched = merged

    # Colonnes OHLCV de base toujours présentes
    for col in ("open", "high", "low", "close", "volume"):
        if col not in df_enriched.columns and col in df_raw.columns:
            df_enriched = df_enriched.merge(
                df_raw[["datetime", col]], on="datetime", how="left"
            )

    if "Close" not in df_enriched.columns and "close" in df_enriched.columns:
        df_enriched["Close"] = df_enriched["close"]

    df_enriched = df_enriched.sort_values("datetime").reset_index(drop=True)
    df_enriched.to_parquet(path, index=False)

    size_kb = path.stat().st_size // 1024
    n_cols  = len(df_enriched.columns)
    print(f"  [{symbol}] ✓ {len(df_enriched):,} barres × {n_cols} features → {path.name} ({size_kb}KB)")
    return len(df_enriched)


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap enriched parquet depuis Binance")
    parser.add_argument("--symbols", nargs="+",
                        default=["XRPUSDT", "DOGEUSDT", "DOTUSDT", "LINKUSDT"],
                        help="Symboles à bootstrapper")
    parser.add_argument("--since", default=DEFAULT_SINCE,
                        help=f"Date de départ (défaut: {DEFAULT_SINCE})")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    print("=== bootstrap_enriched ===")
    total = 0
    for sym in args.symbols:
        n = bootstrap_symbol(sym, since=args.since, dry_run=args.dry_run)
        total += n
    print(f"\nTotal : {total:,} barres créées pour {len(args.symbols)} symbole(s)")


if __name__ == "__main__":
    main()
