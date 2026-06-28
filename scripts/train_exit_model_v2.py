#!/usr/bin/env python3
"""
scripts/train_exit_model_v2.py — Entraînement Exit Model V2 (Temporal Transformer)
====================================================================================

Pipeline :
  1. Génère les samples V1 (generate_exit_samples) pour chaque asset
  2. Construit les séquences temporelles (build_sequences)
  3. Entraîne ExitFleetV2 (ensemble 3×Transformer + Platt scaling)
  4. Simule baseline 8h vs V2 sur val 2025
  5. Sauvegarde

Usage :
  python scripts/train_exit_model_v2.py
  python scripts/train_exit_model_v2.py --epochs 30 --symbols BTCUSDT ETHUSDT
  python scripts/train_exit_model_v2.py --fast        # 20 epochs pour test rapide
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import warnings
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.settings import configure_project_imports
configure_project_imports()

import torch
torch.set_num_threads(8)   # utiliser 8 des 16 cœurs (laisser 8 au reste)

from ai.level_0.labels import compute_label_columns, build_labels, compute_long_regime_col
from ai.level_0.exit_labels import generate_exit_samples, COST_PCT
from ai.level_2.exit_model_v2 import ExitFleetV2, build_sequences, EXIT_MARKET_FEATURES

try:
    from ai.level_0.institutional_features import FEATURES_INST_LONG
except ImportError:
    from ai.level_0.features import FEATURES_LONG as FEATURES_INST_LONG
from ai.level_0.features import get_available_features

ENRICHED_DIR = ROOT / "data" / "enriched"
MODELS_DIR   = ROOT / "reports" / "paper_trading" / ".models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_PATH  = MODELS_DIR / "exit_model_v2.pkl"

TOP_10 = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT",
    "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT",
    "DOTUSDT", "LINKUSDT",
]
TRAIN_END = 2024
VAL_YEAR  = 2025
HORIZON   = 8
SIZING    = 0.25


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _load_enriched(sym: str) -> pd.DataFrame | None:
    p = ENRICHED_DIR / f"{sym}_1h_enriched.parquet"
    if not p.exists():
        return None
    try:
        df = pd.read_parquet(p)
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
        if "Close" not in df.columns and "close" in df.columns:
            df["Close"] = df["close"]
        if "rv_24" not in df.columns and "realized_volatility_20" in df.columns:
            df["rv_24"] = df["realized_volatility_20"]
        if "rv_72" not in df.columns and "realized_volatility_50" in df.columns:
            df["rv_72"] = df["realized_volatility_50"]
        return df.sort_values("datetime").reset_index(drop=True)
    except Exception as e:
        print(f"  [{sym}] erreur lecture : {e}")
        return None


def _simulate(fleet: ExitFleetV2, sym: str, df: pd.DataFrame,
              val_mask: np.ndarray, entry_model) -> dict:
    """Simule baseline 8h vs V2 sur la validation."""
    from ai.level_0.exit_labels import compute_position_state

    feats   = get_available_features(df, FEATURES_INST_LONG)
    val_idx = np.where(val_mask)[0]
    df_val  = df.iloc[val_idx].copy().reset_index(drop=True)
    ones    = np.ones(len(df_val), dtype=bool)
    p_entry = entry_model.predict(df_val, ones)

    thr_path = MODELS_DIR / f"{sym}_{TRAIN_END}_thresholds.json"
    thr      = 0.55
    if thr_path.exists():
        thr = json.loads(thr_path.read_text()).get("general", 0.55)

    signal_idx = np.where(p_entry >= thr)[0]
    close_val  = df_val["close"].values
    n          = len(df_val)

    bl_pnls, v2_pnls, or_pnls = [], [], []

    for si in signal_idx:
        if si + HORIZON >= n:
            continue
        ep = close_val[si]

        # Baseline
        ret_bl = np.log(close_val[si + HORIZON] / ep) - COST_PCT
        bl_pnls.append(ret_bl * SIZING)

        # Oracle
        fret = np.log(close_val[si + 1: si + HORIZON + 1] / ep)
        ret_or = fret.max() - COST_PCT
        or_pnls.append(ret_or * SIZING)

        # V2 exit model
        rv_24 = float(df_val["rv_24"].iloc[si]) if "rv_24" in df_val.columns else 0.02
        max_c = min_c = ep
        prev_c = c3ago = ep
        exit_k = HORIZON - 1

        for k in range(1, HORIZON):
            tk = si + k
            if tk >= n:
                break
            cur_p = close_val[tk]
            max_c = max(max_c, cur_p)
            min_c = min(min_c, cur_p)

            ps = compute_position_state(
                bars_held=k, entry_price=ep, current_price=cur_p,
                max_price=max_c, min_price=min_c,
                prev_price=prev_c, price_3ago=c3ago,
                rv_24=rv_24, entry_bar=dict(df_val.iloc[si]),
                max_hold=HORIZON, cost_pct=COST_PCT,
            )
            ps["symbol"] = sym

            last_bar = df_val.iloc[tk]
            should_exit, _ = fleet.should_exit(last_bar, ps)

            if ps["unrealized_ret"] < -0.025 or should_exit:
                exit_k = k
                break

            c3ago, prev_c = prev_c, cur_p

        ret_v2 = np.log(close_val[si + exit_k] / ep) - COST_PCT
        v2_pnls.append(ret_v2 * SIZING)

    return {
        "n":        len(bl_pnls),
        "bl_total": sum(bl_pnls) * 100,
        "v2_total": sum(v2_pnls) * 100,
        "or_total": sum(or_pnls) * 100,
    }


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbols", nargs="+", default=None)
    parser.add_argument("--epochs",  type=int, default=40)
    parser.add_argument("--fast",    action="store_true", help="20 epochs pour test")
    parser.add_argument("--lr",      type=float, default=5e-4)
    parser.add_argument("--batch",   type=int,   default=256)
    args = parser.parse_args()

    if args.fast:
        args.epochs = 20

    t_start = time.time()
    now     = datetime.now(timezone.utc)

    print("=" * 68)
    print("  TRAIN EXIT MODEL v2 — Temporal Transformer (PyTorch)")
    print(f"  {now.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  {args.epochs} epochs  lr={args.lr}  batch={args.batch}")
    print("=" * 68)

    targets   = args.symbols or TOP_10
    available = [s for s in targets if (ENRICHED_DIR / f"{s}_1h_enriched.parquet").exists()]
    print(f"\n  Assets : {', '.join(available)}")

    # ── Phase 1 : Génération des samples + séquences ─────────────────────────
    print(f"\n{'─'*68}")
    print("  PHASE 1 — Génération samples + construction séquences")
    print(f"{'─'*68}")

    all_samples_tr: list = []
    all_samples_vl: list = []
    df_enriched_map: dict = {}

    for sym in available:
        t0_sym = time.time()
        print(f"\n  [{sym}]")

        df = _load_enriched(sym)
        if df is None:
            continue
        df_enriched_map[sym] = df

        try:
            df = compute_label_columns(df)
            df = compute_long_regime_col(df)
        except Exception as e:
            print(f"    WARN labels: {e}"); continue

        years   = df["datetime"].dt.year.values
        tr_mask = years <= TRAIN_END
        vl_mask = years == VAL_YEAR

        try:
            df, _ = build_labels(df, tr_mask)
        except Exception as e:
            print(f"    WARN build_labels: {e}"); continue

        n_tr = int((df["y_long"].values[tr_mask] == 1).sum())
        n_vl = int((df["y_long"].values[vl_mask] == 1).sum()) if vl_mask.sum() else 0
        print(f"    y_long==1 : train={n_tr:,}  val={n_vl:,}")

        if n_tr < 10:
            continue

        # Samples plats (V1 format)
        df_tr = generate_exit_samples(df, tr_mask, symbol=sym)
        df_vl = generate_exit_samples(df, vl_mask, symbol=sym) if n_vl >= 5 else pd.DataFrame()

        # Injecter le dataframe enrichi pour les barres pré-entrée
        df_enriched_map[sym] = df   # avec y_long calculé

        if len(df_tr) > 0:
            all_samples_tr.append(df_tr)
            print(f"    Train samples : {len(df_tr):,}  y_exit={df_tr['y_exit'].mean():.1%}  "
                  f"({time.time()-t0_sym:.0f}s)")
        if len(df_vl) > 0:
            all_samples_vl.append(df_vl)
            print(f"    Val   samples : {len(df_vl):,}  y_exit={df_vl['y_exit'].mean():.1%}")

    if not all_samples_tr:
        print("  ERREUR : aucun sample. Abandon.")
        sys.exit(1)

    df_train_flat = pd.concat(all_samples_tr, ignore_index=True)
    df_val_flat   = pd.concat(all_samples_vl, ignore_index=True) if all_samples_vl else None

    print(f"\n  Pool samples : train={len(df_train_flat):,}  "
          f"val={len(df_val_flat) if df_val_flat is not None else 0:,}")

    # Construction des séquences temporelles
    print("\n  Construction des séquences temporelles …")
    t_seq = time.time()
    records_train = build_sequences(df_train_flat, df_enriched_map)
    print(f"  Train : {len(records_train):,} séquences  ({time.time()-t_seq:.0f}s)")

    records_val = []
    if df_val_flat is not None and len(df_val_flat) > 0:
        records_val = build_sequences(df_val_flat, df_enriched_map)
        print(f"  Val   : {len(records_val):,} séquences")

    # ── Phase 2 : Entraînement ────────────────────────────────────────────────
    print(f"\n{'─'*68}")
    print("  PHASE 2 — Entraînement ExitFleetV2")
    print(f"{'─'*68}")

    fleet = ExitFleetV2()
    fleet.fit(
        records_train,
        records_val if records_val else None,
        epochs=args.epochs,
        lr=args.lr,
        batch_size=args.batch,
        n_workers=4,
    )

    # ── Phase 3 : Simulation ──────────────────────────────────────────────────
    print(f"\n{'─'*68}")
    print("  PHASE 3 — Simulation val 2025 (V2 vs baseline 8h vs Oracle)")
    print(f"{'─'*68}")

    bl_tot = v2_tot = or_tot = n_sig = 0
    print(f"\n  {'Asset':<12} {'n':>5} {'Baseline':>10} {'V2':>10} {'Oracle':>10} {'Δ V2-BL':>10}")
    print(f"  {'─'*12} {'─'*5} {'─'*10} {'─'*10} {'─'*10} {'─'*10}")

    for sym in available:
        df = df_enriched_map.get(sym)
        mpth = MODELS_DIR / f"{sym}_{TRAIN_END}.pkl"
        if df is None or not mpth.exists():
            continue
        em = joblib.load(mpth)
        years   = df["datetime"].dt.year.values
        vl_mask = years == VAL_YEAR
        if vl_mask.sum() == 0:
            continue

        try:
            sim = _simulate(fleet, sym, df, vl_mask, em)
        except Exception as e:
            print(f"  [{sym}] sim error: {e}"); continue

        bl_tot += sim["bl_total"]
        v2_tot += sim["v2_total"]
        or_tot += sim["or_total"]
        n_sig  += sim["n"]
        delta   = sim["v2_total"] - sim["bl_total"]
        print(f"  {sym:<12} {sim['n']:>5}  "
              f"{sim['bl_total']:>+9.1f}%  {sim['v2_total']:>+9.1f}%  "
              f"{sim['or_total']:>+9.1f}%  {delta:>+9.1f}%")

    print(f"  {'─'*12} {'─'*5} {'─'*10} {'─'*10} {'─'*10} {'─'*10}")
    print(f"  {'TOTAL':<12} {n_sig:>5}  "
          f"{bl_tot:>+9.1f}%  {v2_tot:>+9.1f}%  "
          f"{or_tot:>+9.1f}%  {v2_tot-bl_tot:>+9.1f}%")
    print(f"\n  Amélioration totale V2 vs Baseline : {v2_tot - bl_tot:+.1f}%")
    print(f"  V1 improvement (référence)         : +9.4%")
    print(f"  Oracle upper bound                 : {or_tot:+.1f}%")

    # ── Sauvegarde ────────────────────────────────────────────────────────────
    print(f"\n{'─'*68}")
    print(f"  Sauvegarde → {OUTPUT_PATH}")
    joblib.dump(fleet, OUTPUT_PATH)
    size_mb = OUTPUT_PATH.stat().st_size / 1024 / 1024
    print(f"  Taille : {size_mb:.1f} MB")
    print(f"  Threshold : {fleet.threshold_:.2f}")
    print(f"  Ensemble : {len(fleet.models)} modèles")

    elapsed = time.time() - t_start
    print(f"\n{'═'*68}")
    print(f"  DONE — {elapsed/60:.0f}m{elapsed%60:.0f}s")
    print(f"{'═'*68}")


if __name__ == "__main__":
    main()
